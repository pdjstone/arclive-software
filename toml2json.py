#!/usr/bin/env python3
"""
Recursively scans src_dir for .toml files containing catalogues of disc
images (e.g. .adf) and archive files (e.g. .zip). Creates a single
software.json file in out_dir and copies/downloads the software to that
directory. The software files are renamed according to the software ID in
the toml.

Downloads are kept in dlcache, and each file's hash in a -hashes.toml file
beside its toml. A file whose hash doesn't match is an error, unless
--refresh is given.
"""
from io import BytesIO
import argparse
import os
import re
import sys
import tempfile
import toml
import json
import shutil
import hashlib
import time
import pycurl
import subprocess
from tqdm import tqdm

from toml.decoder import TomlDecodeError

from riscosconv.cli import load_disc
from riscosconv.riscos_path import PureRiscOsPath
from riscosconv.sprites import SpriteArea

# map of id to toml filename
all_software_ids = {}
all_deps = {}

out_dir = None
# --refresh: fetch every file again, rather than using dlcache
refresh = False
# The progress bar, when run as a script
progress = None


class CatalogueError(Exception):
    """Something wrong with the catalogue or its files, which stops the build.
    Shown as just its message"""


def report(msg):
    """Say what changed (in dlcache or a hash file)"""
    tqdm.write(msg)


def warn(msg):
    tqdm.write(f'Warning: {msg}', file=sys.stderr)


def show_status(text):
    """Show what's happening (e.g. a download) beside the progress bar"""
    if progress:
        progress.set_postfix_str(text)

pngcrush_available = shutil.which('pngcrush')


RISC_OS_COMMA_FILETYPE_PATTERN = r',[a-z90-9]{3}$'

CACHE_DIR = 'dlcache'

MANDATORY_FIELDS = (
    'title', 
    'year',
    'tags',
)

VALID_FIELDS = (
    'author', 'publisher',

    'version', # version number or string
    'disc', 'archive', # filename (relative to TOML file) or URL of disc image/archive file
    'tags', # comma-separated list of valid tags (see below)
    'description', 
    'best-os', # must be a valid OS from below.
    'best-cpu', # must be a valid CPU from below.
    'min-mem', # 1MB/2MB/4MB/8MB
    'info-url', # link to software homepage or further information
    'app-path', # path of app to launch when autobooting, relative to root of disc or archive 
                # (e.g. "!Foo" if in root of disc/archive or "dirname.!Foo") 
                # Also used for icon extraction
    'click-icon', # set to true to launch app after loading it by clicking its icon on the icon bar
    'autoboot', # custom boot script, which will override the default "*desktop filer_run <path.to.app-path>"
    'ff-ms', # number of milliseconds to fast-forward when autobooting
    'sound-filter', # 0 (full), 1 (reduced) or 2 (more reduced)
    'depends', # archive dependencies
    'mouse-capture', # force/never (auto is default, so never needed)
)

VALID_FIELDS = set(VALID_FIELDS + MANDATORY_FIELDS)

VALID_TAGS = (
    'game', 
    'demo', 
    'public-domain', 
    'desktop',
    'education',
    'utility',
    'music',
    'ex-commercial',
    'demoscene',
    'hidden' # don't show in software browser, but allow loading the game via URL param
)

VALID_OS = ('arthur120', 'riscos201', 'riscos311')
VALID_CPU = ('arm2', 'arm3', 'arm250')
VALID_MEM = ('1MB', '2MB', '4MB', '8MB')
VALID_FILE_EXTS = ('.arc', '.zip', '.adf', '.spk')
VALID_MOUSE_CAPTURE = ('force', 'never')
RISC_OS_TYPE_MAP = { 'ddc' : 'spk'}


def copy_img(src: str, dst: str):
    assert src.lower().endswith('.png')
    if pngcrush_available:
        with open(src, 'rb') as f:
            header = f.read(0x50)
        if b'pngcrush' not in header:
            r = subprocess.run(['pngcrush' ,'-ow', '-text', 'b', 'Software', 'pngcrush', src],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if r.returncode != 0:
                warn(f'pngcrush failed on {src}: {r.stderr.strip()}')
    shutil.copyfile(src, dst)


def find_toml_files(root_dir):
    for root, dirs, files in os.walk(root_dir):
        for f in files:
            if f.endswith('.toml') and not f.endswith('-hashes.toml'):
                yield root, f


def parse_toml(root, file):
    toml_path = os.path.join(root, file)
    toml_hash_path = os.path.join(root, os.path.basename(file).removesuffix('.toml') + '-hashes.toml')
    try:
        data = toml.load(toml_path)
    except TomlDecodeError as e:
        raise CatalogueError(f"TOML error in file {toml_path}: " + str(e))
    hashes = {}
    if os.path.isfile(toml_hash_path):
        hashes = toml.load(toml_hash_path)

    for software_id, disc_meta in data.items():
        show_status(software_id)
        if software_id in all_software_ids:
            existing_toml_file = all_software_ids[software_id]
            raise CatalogueError(f"Duplicate software id '{software_id}' in {toml_path} (existing one in {existing_toml_file})")

        if 'disc' not in disc_meta and 'archive' not in disc_meta:
            raise CatalogueError(f"Must have 'disc' or 'archive' field ('{software_id}' in {toml_path})")

        if 'disc' in disc_meta and 'archive' in disc_meta:
            raise CatalogueError(f"Cannot define 'disc' and 'archive' '{software_id}' in {toml_path}")
        
        known_hash = hashes.get(software_id, None)
        file_field = 'disc' if 'disc' in disc_meta else 'archive'
        source = disc_meta[file_field]
        disc_meta[file_field], hash, refreshed = fetch_file(
            root, source, software_id, known_hash, disc_meta.get('app-path'), toml_path)

        if known_hash and known_hash != hash and not refreshed:
            raise CatalogueError(f"file hash for {software_id} doesn't match")
        else:
            if not known_hash and not refreshed:
                report(f'{software_id}: new file {source} (hash {hash[:12]})')
            hashes[software_id] = hash

        for field in MANDATORY_FIELDS:
            if type(field) is str:
                if field not in disc_meta:
                    raise CatalogueError(f"Field '{field}' missing from '{software_id}' in {toml_path}")
        
        if 'tags' in disc_meta:
            tags = disc_meta['tags'].split(',')
            disc_meta['tags'] = tags
            for t in tags:
                if t not in VALID_TAGS:
                    raise CatalogueError(f"'{software_id}' in {toml_path}: Unknown tag '{t}'")

        if 'best-os' in disc_meta and disc_meta['best-os'] not in VALID_OS:
            raise CatalogueError(f"'{software_id}' in {toml_path}: Invalid best-os: {disc_meta['best-os']}")

        if 'best-cpu' in disc_meta and disc_meta['best-cpu'] not in VALID_CPU:
            raise CatalogueError(f"'{software_id}' in {toml_path}: Invalid best-cpu: {disc_meta['best-cpu']}")

        if 'mouse-capture' in disc_meta and disc_meta['mouse-capture'] not in VALID_MOUSE_CAPTURE:
            raise CatalogueError(f"'{software_id}' in {toml_path}: Invalid mouse-capture: {disc_meta['mouse-capture']}")

        if 'min-mem' in disc_meta:
            if disc_meta['min-mem'] not in VALID_MEM:
                raise CatalogueError(f"'{software_id}' in {toml_path}: Invalid min-mem: {disc_meta['min-mem']}")
            mem_kb = int(disc_meta['min-mem'][0].removesuffix('MB')) * 1024
            disc_meta['min-mem'] = mem_kb

        if 'depends' in disc_meta:
            deps = disc_meta['depends'].split(',')
            disc_meta['depends'] = deps
            all_deps[software_id] = deps

        if 'sound-filter' in disc_meta:
            sf = disc_meta['sound-filter']
            assert type(sf) is int and 0 <= sf <= 2

        for field in disc_meta.keys():
            if field not in VALID_FIELDS:
                raise CatalogueError(f"Unknown field '{field}' in '{software_id}' ({toml_path})")
        
        disc_meta['id'] = software_id

        if 'app-path' in disc_meta:
            disc = load_software(disc_meta)
            check_app_path(disc, software_id, disc_meta['app-path'], disc_meta[file_field], toml_path)
            extract_icon(disc, disc_meta)

        if screenshots := find_screenshots(root, software_id):
            disc_meta['screenshots'] = screenshots
        all_software_ids[software_id] = toml_path
        
        with open(toml_hash_path, 'w') as f:
            toml.dump(hashes, f)
        if progress:
            progress.update()

    return data


def load_software(disc_meta):
    """The disc image or archive (as copied to out_dir), opened"""
    software_path = disc_meta.get('archive') or disc_meta.get('disc')
    software_path = os.path.join(out_dir, software_path)
    try:
        disc = load_disc(software_path)
    except Exception as e:
        raise CatalogueError(f"Could not load {software_path} ('{disc_meta['id']}'): {e!r}") from e
    if not disc:
        raise CatalogueError(f"Could not load {software_path} ('{disc_meta['id']}'), "
                        "so can't check its app-path")
    return disc


def check_app_path(disc, software_id, app_path, file_name, toml_path):
    """Check app_path is in the disc or archive (file_name): an app directory
    (which the listing doesn't give, only the files in it) or a file. RISC OS
    names are case-insensitive"""
    app_path = PureRiscOsPath(app_path)
    want = tuple(p.lower() for p in app_path.parts)
    tops = set()
    for path, _ in disc.list():
        parts = tuple(p.lower() for p in PureRiscOsPath(path).parts)
        if parts[:len(want)] == want:
            return
        tops.add(PureRiscOsPath(path).parts[0])
    raise CatalogueError(f"'{software_id}' in {toml_path}: app-path '{app_path}' isn't in "
                    f"{file_name} (its root holds: {', '.join(sorted(tops))})")


def extract_icon(disc, disc_meta):
    sprite_files = ('!Sprites22', '!Sprites')
    sprite_fd = None
    app_path = PureRiscOsPath(disc_meta['app-path'])
    software_path = disc_meta.get('archive') or disc_meta.get('disc')

    try:
        for sf in sprite_files:
            if sprite_fd := disc.open(app_path / sf):
                break
    except Exception as e:
        raise CatalogueError(f"Could not read {app_path}'s sprites from {software_path} "
                             f"('{disc_meta['id']}'): {e!r}") from e
    
    if sprite_fd:
        sprite_area = SpriteArea(sprite_fd)
        try:
            spr_name = app_path.name
            sprite = sprite_area[spr_name]
            sprite_out_path = os.path.join(out_dir, 'icons', disc_meta['id'] + '-icon.png')
            sprite.get_pil_image().save(sprite_out_path)
        except KeyError:
            warn(f"{disc_meta['id']}: no sprite {spr_name} in {app_path}'s sprites, so no icon")


def find_screenshots(root, software_id):
    file_pattern = software_id + r'-\d+.png'
    files = sorted([f.name for f in os.scandir(root) if re.match(file_pattern, f.name)])
    if not files: 
        return
    for f in files:
        copy_img(os.path.join(root, f), os.path.join(out_dir, 'screenshots', f))
    return files


def is_url(path):
    return path.startswith('http://') or path.startswith('https://')


def fetch_file(root, path, software_id, known_hash, app_path, toml_path):
    """Puts the file in out_dir. Returns (its name there, its hash, whether
    --refresh found a new version of it)"""
    cache_path = hash = None
    if refresh:
        cache_path, hash = refresh_file(root, path, software_id, known_hash, app_path, toml_path)
    refreshed = cache_path is not None and hash != known_hash

    if not cache_path:
        cache_path, hash = fetch_cached(path, software_id, known_hash)

    if not cache_path:     
        if is_url(path):
            #print(f'fetching {software_id} from {path}')
            cache_path, hash = fetch_url(root, path, software_id, known_hash)
        else:
            #print(f'fetching {software_id} from local path {path}')
            cache_path, hash = fetch_local(root, path, software_id, known_hash)
    # Served as <id>-<hash><ext>, so each version of a file has its own URL
    # and can be cached for good. dlcache keeps the plain <id><ext> name
    base_name, ext = os.path.splitext(filename_to_canonical(path, software_id))
    new_name = f'{base_name}-{hash[:12]}{ext}'
    dst_path = os.path.join(out_dir, new_name)
    shutil.copy(cache_path, dst_path)
    return new_name, hash, refreshed


def refresh_file(root, path, software_id, known_hash, app_path, toml_path):
    """--refresh: fetch the URL, or the local file if it's there, and put it
    in dlcache. A changed file (or a new one) is only taken if riscos-conv can
    read it and it holds app_path. Returns (cache path, hash), or (None, None)
    to use dlcache as usual"""
    if is_url(path):
        try:
            data = download(path, software_id)
        except DownloadError as e:
            warn(f'could not download {software_id} from {path}: {e}')
            return None, None
    else:
        src_path = os.path.join(root, path)
        if not os.path.isfile(src_path):
            return None, None
        with open(src_path, 'rb') as f:
            data = f.read()

    hash = hashlib.sha256(data).hexdigest()
    name = filename_to_canonical(path, software_id)
    cache_path = os.path.join(CACHE_DIR, name)
    if hash != known_hash:
        try:
            validate_software(name, data, software_id, app_path, toml_path)
        except Exception as e:
            warn(f"{path} for {software_id} has changed, but the new file can't be used "
                 f"({e}), so keeping the old one")
            return None, None
        if known_hash:
            report(f'{software_id}: {path} has changed (hash {known_hash[:12]} -> {hash[:12]}), '
                   f'updated {cache_path} and its hash')
        else:
            report(f'{software_id}: new file {path} (hash {hash[:12]})')
    with open(cache_path, 'wb') as f:
        f.write(data)
    return cache_path, hash


def validate_software(name, data, software_id, app_path, toml_path):
    """Raises an exception unless riscos-conv can read data (a file called
    name) and list its files, and it holds app_path (if given)"""
    with tempfile.TemporaryDirectory() as tmp:
        # load_disc tells some kinds of file by their extension
        tmp_path = os.path.join(tmp, name)
        with open(tmp_path, 'wb') as f:
            f.write(data)
        disc = load_disc(tmp_path)
        if not disc:
            raise CatalogueError("riscos-conv doesn't recognise it as a disc image or archive")
        files = list(disc.list())
        if not files:
            raise CatalogueError('it holds no files')
        if app_path:
            check_app_path(disc, software_id, app_path, name, toml_path)


def filename_to_canonical(filename_or_url: str, software_id):
    if re.search(RISC_OS_COMMA_FILETYPE_PATTERN, filename_or_url, re.IGNORECASE):
        base_name, filetype = filename_or_url.rsplit(',', 1)
        ext = '.' + RISC_OS_TYPE_MAP.get(filetype.lower(), None)
    else:
        base_name, ext = os.path.splitext(os.path.basename(filename_or_url))
    if (not ext or ext.lower() not in VALID_FILE_EXTS) and 'jaspp' in filename_or_url:
        ext = '.zip'
    if not ext or ext.lower() not in VALID_FILE_EXTS:
        raise CatalogueError(f"bad extension: {filename_or_url} ({software_id})")
    new_name = f'{software_id}{ext}'    
    return new_name


def fetch_cached(path, software_id, known_hash=None):
    new_name = filename_to_canonical(path, software_id)
    cache_path = os.path.join(CACHE_DIR, new_name)
    if not os.path.isfile(cache_path):
        return None, None
    with open(cache_path,'rb') as f:
        hash = hashlib.sha256(f.read()).hexdigest()
    if known_hash is not None and known_hash != hash:
        warn(f"{cache_path} doesn't match {software_id}'s hash, so fetching it again")
        return None, None
    return cache_path, hash


class DownloadError(Exception):
    pass


def download(url, software_id=None):
    """The content of url (for software_id, shown in the progress bar). Raises
    DownloadError if it can't be fetched, or the server gives an error or
    nothing"""
    label = f'downloading {software_id}' if software_id else f'downloading {url}'
    show_status(label)
    last_shown = 0

    def show_progress(total, done, *_):
        nonlocal last_shown
        now = time.monotonic()
        if done and now - last_shown > 0.2:
            last_shown = now
            show_status(f'{label} {done // 1024} kB' + (f' of {total // 1024}' if total else ''))
        return 0

    buffer = BytesIO()
    c = pycurl.Curl()
    c.setopt(c.URL, url)
    c.setopt(c.WRITEDATA, buffer)
    c.setopt(c.SSL_VERIFYPEER, 0)
    c.setopt(c.FOLLOWLOCATION, 1)
    c.setopt(c.CONNECTTIMEOUT, 30)
    c.setopt(c.NOPROGRESS, 0)
    c.setopt(c.XFERINFOFUNCTION, show_progress)
    try:
        c.perform()
        status = c.getinfo(c.RESPONSE_CODE)
    except pycurl.error as e:
        raise DownloadError(e.args[-1])
    finally:
        c.close()
        show_status(software_id or '')
    if status >= 400:
        raise DownloadError(f'HTTP status {status}')
    file_data = buffer.getvalue()
    if len(file_data) == 0:
        raise DownloadError('empty response')
    return file_data


def fetch_url(root, url, software_id, known_hash=None):
    new_name = filename_to_canonical(url, software_id)
    cache_path = os.path.join(CACHE_DIR, new_name)

    try:
        file_data = download(url, software_id)
    except DownloadError as e:
        raise CatalogueError(f"Unable to download {software_id} from {url}: {e}")
    
    hash = hashlib.sha256(file_data).hexdigest()

    if known_hash and hash != known_hash:
        raise CatalogueError(f'incorrect hash for {software_id} downloaded from {url}')
        
    with open(cache_path, 'wb') as f:
        f.write(file_data)
   
    return cache_path, hash


def fetch_local(root, path, software_id, known_hash) -> str:
    global out_dir
    src_path = os.path.join(root, path)
    new_name = filename_to_canonical(path, software_id)
    cache_path = os.path.join(CACHE_DIR, new_name)

    if not os.path.isfile(src_path):
        raise CatalogueError(f"file {src_path} does not exist ({software_id})")
    #print(f'copying {src_path} to {cache_path}')
    shutil.copy(src_path, cache_path)
    with open(src_path,'rb') as f:
        hash = hashlib.sha256(f.read()).hexdigest()
    if known_hash and hash != known_hash:
        raise CatalogueError(f"Incorrect hash for {software_id} at {path}")
    return cache_path, hash 


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('src_dir', help='directory to scan for .toml catalogue files')
    parser.add_argument('out_dir', help='directory for software.json and the software')
    parser.add_argument('--refresh', action='store_true',
                        help='fetch every file again (each URL, or each local file that is there) '
                             'rather than using dlcache. A file that has changed is checked (riscos-conv '
                             'must read it, and it must hold its app-path), then replaces the one in '
                             'dlcache and its hash. A URL that fails to download only gives a warning, '
                             'and the copy in dlcache is used')
    args = parser.parse_args()
    src_dir, out_dir, refresh = args.src_dir, args.out_dir, args.refresh

    if not os.path.isdir(src_dir):
        parser.error(f"No such src_dir {src_dir}")
    
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(os.path.join(out_dir, 'screenshots'), exist_ok=True)
        os.makedirs(os.path.join(out_dir, 'icons'), exist_ok=True)

    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)
    toml_files = sorted(find_toml_files(src_dir))
    total = 0
    for root, file in toml_files:
        try:
            total += len(toml.load(os.path.join(root, file)))
        except TomlDecodeError:
            pass        # parse_toml reports it
    progress = tqdm(total=total, unit='title', dynamic_ncols=True, leave=False,
                    disable=None)     # off when stderr isn't a terminal (e.g. a log)
    try:
        json_data = {}
        for root, file in toml_files:
            data = parse_toml(root, file)
            json_data |= data

        for software_id, deps in all_deps.items():
            for d in deps:
                if d not in all_software_ids:
                    raise CatalogueError(f'{software_id} depends on unknown ID {d}')
                if 'archive' not in json_data[d]:
                    raise CatalogueError(f'{software_id} depends on ID {d} which is a disc?')
    except CatalogueError as e:
        progress.close()
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
    progress.close()

    json_out = os.path.join(out_dir, 'software.json')
    with open(json_out, 'w') as f:
        json.dump(json_data, f, indent=True)
 