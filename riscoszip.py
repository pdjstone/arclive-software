#!/usr/bin/env python3 

from zipfile import ZipFile, ZIP_DEFLATED, ZipInfo
import struct
from datetime import datetime, timedelta
import os
import time
import re
import argparse
from filetypes import RISC_OS_FILETYPES
from pathlib import Path

ZIP_EXT_ACORN = 0x4341    # 'AC' - SparkFS / Acorn
ZIP_ID_ARC0 = 0x30435241  # 'ARC0'

RISC_OS_COMMA_FILETYPE_PATTERN = r',([a-f0-9]{3})$'
RISC_OS_LOAD_EXEC_PATTERN = r',([a-f0-9]{8})-([a-f0-9]{8})$'

RISC_OS_EPOCH = datetime(1900,1,1,0,0,0)

# See http://www.riscos.com/support/developers/prm/fileswitch.html#idx-3804


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
        if m := re.search(RISC_OS_COMMA_FILETYPE_PATTERN, leaf_name, re.IGNORECASE):
            filetype = int(m.group(1), 16)
            mtime = datetime.fromtimestamp(st.st_mtime)
            delta = mtime - RISC_OS_EPOCH
            cs = int(delta.total_seconds() * 100)
            load_addr = (0xfff << 20) | (filetype << 8) | (cs >> 32)
            exec_addr = cs & 0xffffffff
        elif m := re.search(RISC_OS_LOAD_EXEC_PATTERN, leaf_name, re.IGNORECASE):
            load_addr = int(m.group(1), 16)
            exec_addr = int(m.group(2), 16)
        else:
            raise Exception("todo: normal file extensions")
        return RiscOsFileMeta(load_addr, exec_addr)

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
    
ZipInfo._decodeExtra = _decodeExtra
ZipInfo.getRiscOsMeta = _decodeRiscOsExtra

def get_riscos_zipinfo(path: Path, base_path: Path):
    meta = RiscOsFileMeta.from_filepath(path)
    zip_path, _ = str(path.relative_to(base_path)).rsplit(',', 1)
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

def list_riscos_zip(zipfile: ZipFile):
    for info in zipfile.infolist():
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
            extra = ''
        date_formatted = ds.strftime('%Y-%m-%d %H:%M:%S')
        print(f'{extra: >17} {info.file_size: >7} {date_formatted} {info.filename}')

def many_files_in_root(zipfile: ZipFile):
    files_in_root = set()
    for info in zipfile.infolist():
        first = info.filename.split('/', 1).pop(0)
        files_in_root.add(first)
    return len(files_in_root) > 1

def extract_riscos_zipfile(zipfile: ZipFile, path='.'):
    if many_files_in_root(zipfile):
        name, _ = os.path.splitext(os.path.basename(zipfile.filename))
        path += '/' + name
    print(f'Extracting to {path}')
    for info in zipfile.infolist():
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
          
def create_riscos_zipfile(zipfile: ZipFile, paths: list[str]):
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
  

if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='rozip', description="Extract and create RISC OS ZIP files")
    parser.add_argument('-d', '--dir', default='.', help='Output directory')
    parser.add_argument('-a', '--append', action='store_true', help='Append files to existing archive')
    parser.add_argument('action', choices=['x','l','c'], nargs='?', default='l', help='e[x]tract, [l]ist or [c]reate archive')
    parser.add_argument('zipfile', help='ZIP file to create or list/extract')  
    parser.add_argument('files', nargs='*', help='Files to extract / add')
    args = parser.parse_args()

    match args.action:
        case 'l':
            zip = ZipFile(args.zipfile, 'r')
            list_riscos_zip(zip)
        case 'x':
            zip = ZipFile(args.zipfile, 'r')
            extract_riscos_zipfile(zip, args.dir)
        case 'c':
            mode = 'w'
            if args.append:
                mode = 'a'
            zip = ZipFile(args.zipfile, mode)
            create_riscos_zipfile(zip, args.files)

