#!/usr/bin/env python3 

import argparse
import os
import re
import struct
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo, is_zipfile

from ADFSlib import ADFSdirectory, ADFSdisc, ADFSfile, ADFS_exception

from filetypes import RISC_OS_FILETYPES

ZIP_EXT_ACORN = 0x4341    # 'AC' - SparkFS / Acorn
ZIP_ID_ARC0 = 0x30435241  # 'ARC0'

RISC_OS_COMMA_FILETYPE_PATTERN = r',([a-f0-9]{3})$'
RISC_OS_LOAD_EXEC_PATTERN = r',([a-f0-9]{8})-([a-f0-9]{8})$'

RISC_OS_EPOCH = datetime(1900,1,1,0,0,0)

DISC_IM_EXTS = ('.adf','.adl')

RO_ZIP = 0xa91
RO_TEXT = 0xfff
RO_DATA = 0xffd

RISC_OS_ARCHIVE_TYPES = (RO_ZIP, )

FILE_EXT_MAP = {
    '': RO_TEXT,
    '.txt': RO_TEXT,
    '.zip': RO_ZIP
}

DEFAULT_RO_FILETYPE = 0xfff

# See http://www.riscos.com/support/developers/prm/fileswitch.html#idx-3804

class KnownFileType(Enum):
    RISC_OS_ZIP = 1
    ZIPPED_DISC_IMAGE = 2
    ZIPPED_MULTI_DISC_IMAGE = 3
    DISC_IMAGE = 4
    UNKNOWN = 5

def has_disc_image_ext(filename: str) -> bool:
    return any(filename.lower().endswith(ext) for ext in DISC_IM_EXTS)


class RiscOsFileMeta:
    def __init__(self, load_addr, exec_addr, attr=3):
        self.load_addr = load_addr
        self.exec_addr = exec_addr
        self.file_attr = attr

    @property
    def filetype(self):
        if self.load_addr >> 20 == 0xfff:
            return self.load_addr >> 8 & 0xfff
        return None

    @property
    def datestamp(self):
        if self.load_addr >> 20 == 0xfff:
            cs = ((self.load_addr & 0xff) << 32) | self.exec_addr
            delta = timedelta(milliseconds=cs*10)
            return RISC_OS_EPOCH + delta
        return None

    def hostfs_file_ext(self):
        if self.load_addr >> 20 == 0xfff:
            return f',{self.filetype:03x}'
        return f',{self.load_addr:08x}-{self.exec_addr:08x}'

    def __repr__(self):
        if self.filetype:
            return f'RiscOsFileMeta(type={self.filetype:03x} date={self.datestamp} attr={self.file_attr:x})'
        else:   
            return f'RiscOsFileMeta(load={self.load_addr:x} exec={self.exec_addr:x} attr={self.file_attr:x})'

    def zip_extra(self) -> bytes:
        return struct.pack('<HHIIII', ZIP_EXT_ACORN, 20, ZIP_ID_ARC0, 
            self.load_addr, self.exec_addr, self.file_attr)

    @staticmethod
    def from_filepath(path: Path):
        leaf_name = path.name
        st = os.stat(path)
        ts = unix_timestamp_to_ro_timestamp(st.st_mtime)
        if m := re.search(RISC_OS_COMMA_FILETYPE_PATTERN, leaf_name, re.IGNORECASE):
            filetype = int(m.group(1), 16)
            load_addr, exec_addr = make_load_exec(filetype, ts)
        elif m := re.search(RISC_OS_LOAD_EXEC_PATTERN, leaf_name, re.IGNORECASE):
            load_addr = int(m.group(1), 16)
            exec_addr = int(m.group(2), 16)
        else:
            extension = path.suffix
            filetype = FILE_EXT_MAP.get(extension.lower(), None)
            if not filetype:
                raise Exception(f"No RISC OS filetype for {leaf_name} {extension}")
            load_addr, exec_addr = make_load_exec(filetype, ts)
        return RiscOsFileMeta(load_addr, exec_addr)

def make_load_exec(filetype, ro_timestamp):
    load_addr = (0xfff << 20) | (filetype << 8) | (ro_timestamp >> 32)
    exec_addr = ro_timestamp & 0xffffffff
    return load_addr, exec_addr

def unix_timestamp_to_ro_timestamp(timestamp):
    delta = datetime.fromtimestamp(timestamp) - RISC_OS_EPOCH
    centiseconds = int(delta.total_seconds() * 100)
    return centiseconds

def parse_riscos_zip_ext(buf: bytes, offset, fieldLen):
    # See https://www.davidpilling.com/wiki/index.php/SparkFS "A Comment on Zip files"
    if fieldLen == 24:
        fieldLen = 20
    id2 = int.from_bytes(buf[offset+4:offset+8], 'little')
    if id2 != ZIP_ID_ARC0:
        return None

    load, exec, attr = struct.unpack('<III', buf[offset+8:offset+8+12])
    meta = RiscOsFileMeta(load, exec, attr)
    return meta, fieldLen


if not hasattr(ZipInfo, '_decodeExtra'):
    raise Exception("Cannot monkey patch ZipInfo - has implementation changed?")

def _decodeExtra(self):
    pass

def _decodeRiscOsExtra(self):
        offset = 0
        
        # extraFieldTotalLength is total length of all extra fields
        # Iterate through each extra field and parse if known
        while offset < len(self.extra):
            fieldType, fieldLen = struct.unpack('<HH', self.extra[offset:offset+4])
            extraMeta = None
            overrideFieldLen = None
            if fieldType == ZIP_EXT_ACORN:
                extraMeta, overrideFieldLen = parse_riscos_zip_ext(self.extra, offset, fieldLen)
                return extraMeta
            if overrideFieldLen and overrideFieldLen > 0:
                offset += overrideFieldLen + 4; 
            else:
                offset += fieldLen + 4
                
                
        return None

def _encodeFilenameFlags(self):
    return self.filename.encode('iso-8859-1'), self.flag_bits

# for Python < 3.11, we need to override the default codec. 
# for Python >= 3.11 we can use the ZipFile metadata_encoding param
import encodings.cp437
import encodings.iso8859_1
encodings.cp437.decoding_table = encodings.iso8859_1.decoding_table

ZipInfo._decodeExtra = _decodeExtra
ZipInfo.getRiscOsMeta = _decodeRiscOsExtra
ZipInfo._encodeFilenameFlags = _encodeFilenameFlags

def get_riscos_zipinfo(path: Path, base_path: Path):
    meta = RiscOsFileMeta.from_filepath(path)
    if ',' in path.stem:
        zip_path, _ = str(path.relative_to(base_path)).rsplit(',', 1)
    else:
        zip_path = str(path.relative_to(base_path)).removesuffix(path.suffix)
    ds = meta.datestamp
    if not ds:
        ds = datetime.fromtimestamp(path.stat().st_mtime)
   
    date_time = ds.year, ds.month, ds.day, ds.hour, ds.minute, ds.second
    zipinfo = ZipInfo(zip_path, date_time)
    zipinfo.extra = meta.zip_extra()
    st = os.stat(path)
    if st.st_size > 512:
        zipinfo.compress_type = ZIP_DEFLATED
    return zipinfo


def load_ro_filetypes():
    filetype_map = {}
    for l in open('filetypes.txt', 'r'):
        bits = re.split(r'\t', l.strip(), maxsplit=2)
        if len(bits) == 2:
            bits.append('')
        if len(bits) != 3:
            print(len(bits), l.strip())
        filetype, name, desc = bits
        filetype = int(filetype, 16)
        filetype_map[filetype] = name, desc
    return filetype_map

#FILETYPE_MAP = load_ro_filetypes()


def save_filetypes():
    with open('filetypes.py', 'w') as f:
        f.write('RISC_OS_FILETYPES = {\n')
        for filetype, (name, desc) in FILETYPE_MAP.items():
            f.write('  0x{:03x}: ({}, {}),\n'.format(filetype, repr(name), repr(desc)))
        f.write('}\n')

#save_filetypes()

def list_riscos_zip(fd):
    zipfile = ZipFile(fd, 'r',)
    for info in zipfile.infolist():
        if info.is_dir():
            continue
        ro_meta = info.getRiscOsMeta()
        ds = None
        if ro_meta:
            if ro_meta.filetype:
                name, desc = RISC_OS_FILETYPES.get(ro_meta.filetype, (None, None))
                if name:
                    extra = f'{name} {ro_meta.filetype:03x}'
                else:
                    extra = f'{ro_meta.filetype:03x}'
                ds = ro_meta.datestamp
            else:
                extra = f'{ro_meta.load_addr:08x}-{ro_meta.exec_addr:08x}'
                ds = datetime(*info.date_time)
        else:
            ds = datetime(*info.date_time)
            extra = ''
        date_formatted = ds.strftime('%Y-%m-%d %H:%M:%S')
        print(f'{extra: >17} {info.file_size: >7} {date_formatted} {info.filename}')

def many_files_in_root(zipfile: ZipFile):
    files_in_root = set()
    for info in zipfile.infolist():
        first = info.filename.split('/', 1).pop(0)
        files_in_root.add(first)
    return len(files_in_root) > 1

def extract_riscos_zipfile(fd, path='.'):
    zipfile = ZipFile(fd, 'r')
    if many_files_in_root(zipfile):
        name, _ = os.path.splitext(os.path.basename(zipfile.filename))
        path += '/' + name
    print(f'Extracting to {path}')
    for info in zipfile.infolist():
        if info.is_dir():
            continue
        ro_meta = info.getRiscOsMeta()
        extract_path = os.path.join(path, info.filename + ro_meta.hostfs_file_ext())
        print(extract_path)
        extract_dir = os.path.dirname(extract_path)
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.open(info, 'r') as f:
            with open(extract_path, 'wb') as ff:
                ff.write(f.read())
        ds = ro_meta.datestamp
        if not ds:
            ds = datetime(*info.date_time)
        if ds:
            ts = time.mktime(ds.timetuple())
            ts_ns = int(ts * 1_000_000_000) + ds.microsecond * 1000
            os.utime(extract_path, ns=(ts_ns,ts_ns))

def add_file_to_zip(zipfile: ZipFile, filepath: Path, base_path: Path):
    zipinfo = get_riscos_zipinfo(filepath, base_path)
    ro_meta = zipinfo.getRiscOsMeta()
    print(zipinfo.filename, ro_meta)
    with open(filepath, 'rb') as f:
        zipfile.writestr(zipinfo, f.read(), compresslevel=9)

def add_dir_tree_to_zip(zipfile: ZipFile, dirpath: Path, basepath: Path):
    for root, dirs, files in os.walk(dirpath):
        for filename in files:
            filepath = Path(root) / filename
            add_file_to_zip(zipfile, filepath, basepath)
          
def create_riscos_zipfile(zipfile: ZipFile, paths: list[str]|str):
    if type(paths) == str:
        paths = [paths]

    for path in paths:
        path = Path(path)
        if path.is_file():
            add_file_to_zip(zipfile, path, os.path.dirname(path))
        elif path.is_dir():
            dirname = path.name
            basepath = path
            if dirname.startswith('!'):
                basepath = path.parent
            add_dir_tree_to_zip(zipfile, path, basepath)
  
def identify_zipfile(zipfile: ZipFile):
    num_ro_meta = 0
    num_discim_exts = 0
    for info in zipfile.infolist():
        ro_meta = info.getRiscOsMeta()
        if ro_meta:
            num_ro_meta +=1
        if has_disc_image_ext(info.filename):
            num_discim_exts += 1

    if num_discim_exts == 1:
        item_fd = zipfile.open(info, 'r')
        result = identify_discimage(info.filename, item_fd)
        if result == KnownFileType.DISC_IMAGE:
            return KnownFileType.ZIPPED_DISC_IMAGE
        return KnownFileType.UNKNOWN
    if num_discim_exts > 1:
        raise Exception('not support multi disc zips')
    if num_ro_meta >= 1:
        return KnownFileType.RISC_OS_ZIP
    

def identify_discimage(filename:str, fd):
    try:
        adfsdisc = ADFSdisc(fd)
        return KnownFileType.DISC_IMAGE
    except ADFS_exception as e:
        return KnownFileType.UNKNOWN

def identify_file(filename: str, fd) -> KnownFileType:
    if is_zipfile(fd):
        zipfile = ZipFile(fd)
        return identify_zipfile(zipfile)
    else:
        return identify_discimage(filename, fd)

def extract_single_disc_image_from_zip(fd):
    zipfile = ZipFile(fd, 'r')
    for info in zipfile.infolist():
        if has_disc_image_ext(info.filename):
            return zipfile.open(info, 'r')
    raise Exception("Did not find single disc image in ZIP file")

def list_disc_image(fd):
    adfs = ADFSdisc(fd)
    print(adfs.disc_format(), adfs.disc_name)
    adfs.print_catalogue()
    #print(adfs.files[0].name, adfs.files[0].files)

def extract_disc_image(fd, path='.'):
    adfs = ADFSdisc(fd)

    if len(adfs.files) > 1:
        path = path + '/' + adfs.disc_name
        os.makedirs(path, exist_ok=True)
    adfs.extract_files(path, with_time_stamps=True, filetypes=True)

def convert_disc_to_zip(fd, zip_path, extract_paths: list[str] = None):
    assert type(extract_paths) == list

    adfs = ADFSdisc(fd)

    extract_items = []
    if extract_paths:
        for ep in extract_paths:
            item = adfs.get_path(ep)
            if not item:
                raise Exception(f'disc path does not exist: {ep}')
            extract_items.append(item)
    else:
        extract_items = adfs.files 

    with TemporaryDirectory() as temp_dir:
        print('temp dir', temp_dir)
        extract_dir = temp_dir
        for item in extract_items:
            if isinstance(item, ADFSdirectory):
                print('dir', item.name)
                if item.name.startswith('!'): # it's an app
                    extract_dir = os.path.join(temp_dir, item.name)
                    os.mkdir(extract_dir)
                files = item.files
            elif isinstance(item, ADFSfile):
                files = [item]
            adfs.extract_files(extract_dir, files, with_time_stamps=True, filetypes=True)
        zip = ZipFile(zip_path, 'w')
        create_riscos_zipfile(zip, [temp_dir])

HandlerFns = namedtuple('HandlerFns', ['list', 'extract', 'create'], defaults=(None,))

HANDLER_FNS = {
    KnownFileType.DISC_IMAGE: HandlerFns(list_disc_image, extract_disc_image),
    KnownFileType.RISC_OS_ZIP: HandlerFns(list_riscos_zip, extract_riscos_zipfile)
}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='roconv', description="Extract and create RISC OS ZIP files")
    parser.add_argument('-d', '--dir', default='.', help='Output directory')
    parser.add_argument('-a', '--append', action='store_true', help='Append files to existing archive')
    parser.add_argument('action', choices=['x','l','c','d2z'], nargs='?', default='l', help='e[x]tract, [l]ist, [c]reate archive or convert disc to ZIP archive [d2z]')
    parser.add_argument('file', help='ZIP or (zipped) disc file to create or list/extract')  
    parser.add_argument('files', nargs='*', help='Files to extract / add')
    args = parser.parse_args()

    main_file = args.file

    if args.action in ('l', 'x', 'd2z'):
        if not os.path.isfile(main_file):
            sys.stderr.write(f'file not found: {main_file}\n')
            sys.exit(-1)
        fd = open(main_file, 'rb')
        file_type = identify_file(main_file, fd)
        if file_type == KnownFileType.UNKNOWN:
            sys.stderr.write(f'{main_file}: unknown file type\n')
            sys.exit(-1)
        print(f'file type {file_type.name}')
        if file_type == KnownFileType.ZIPPED_DISC_IMAGE:
            fd = extract_single_disc_image_from_zip(fd)
            file_type = KnownFileType.DISC_IMAGE

    elif args.action == 'c':
        if not main_file.lower().endswith('.zip'):
            sys.stderr.write('Only support creating zip files\n')
            sys.exit(-1)
    
    if args.action == 'd2z':
        if file_type != KnownFileType.DISC_IMAGE:
            sys.stderr.write('Must provide disc image to convert to archive\n')
            sys.exit(-1)
        if len(args.files) == 0:
            sys.stderr.write('Must provide an output ZIP filename\n')
            sys.exit(-1)
        output_zip_path = args.files[0]
        extract_paths = args.files[1:]

    match args.action:
        case 'l':
            list_fn = HANDLER_FNS[file_type].list
            list_fn(fd)
        case 'x':
            assert os.path.isdir(args.dir)
            extract_fn = HANDLER_FNS[file_type].extract
            extract_fn(fd, args.dir)
        case 'c':
            mode = 'w'
            if args.append:
                mode = 'a'
            zip = ZipFile(main_file, mode)
            create_riscos_zipfile(zip, args.files)
        case 'd2z':
            convert_disc_to_zip(fd, output_zip_path, extract_paths)

