#!/usr/bin/env python3
import os
import re
import sys
import toml
import json
import shutil
import hashlib
import requests

from toml.decoder import TomlDecodeError

"""
Usage: ./toml2json.py src_dir out_dir

This script will recursively scan src_dir for .toml files containing catalogues of 
disc images (e.g. .adf) and archive files (e.g. .zip). It will create a single 
software.json file in out_dir and copy/download the software to that directory. 
The software files will be renamed according to the software ID in the toml.
"""

# map of id to toml filename
all_software_ids = {}
all_deps = {}

out_dir = None

RISC_OS_COMMA_FILETYPE_PATTERN = r',[a-z90-9]{3}$'

CACHE_DIR = 'dlcache'

MANDATORY_FIELDS = (
    'title', 
    'tags',
)

VALID_FIELDS = (
    'author', 'publisher',
    'year',
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
)

VALID_FIELDS = set(VALID_FIELDS + MANDATORY_FIELDS)

VALID_TAGS = (
    'game', 
    'demo', 
    'public-domain', 
    'education',
    'utility',
    'music',
    'ex-commercial',
    'demoscene'
)

VALID_OS = ('arthur120', 'riscos201', 'riscos311')
VALID_CPU = ('arm2', 'arm3', 'arm250')
VALID_MEM = ('1MB', '2MB', '4MB', '8MB')
VALID_FILE_EXTS = ('.arc', '.zip', '.adf', '.spk')
RISC_OS_TYPE_MAP = { 'ddc' : 'spk'}

def find_toml_files(root_dir):
    for root, dirs, files in os.walk(root_dir):
        for f in files:
            if f.endswith('.toml') and not f.endswith('-hashes.toml'):
                yield root, f


def parse_toml(root, file):
    toml_path = os.path.join(root, file)
    toml_hash_path = os.path.join(root, os.path.basename(file).removesuffix('.toml') + '-hashes.toml')
    print(f'Parsing {toml_path}')
    try:
        data = toml.load(toml_path)
    except TomlDecodeError as e:
        raise Exception(f"TOML error in file {toml_path}: " + str(e))
    hashes = {}
    if os.path.isfile(toml_hash_path):
        hashes = toml.load(toml_hash_path)

    for software_id, disc_meta in data.items():
        if software_id in all_software_ids:
            existing_toml_file = all_software_ids[software_id]
            raise Exception(f"Duplicate software id '{software_id}' in {toml_path} (existing one in {existing_toml_file})")

        if 'disc' not in disc_meta and 'archive' not in disc_meta:
            raise Exception(f"Must have 'disc' or 'archive' field ('{software_id}' in {toml_path})")

        if 'disc' in disc_meta and 'archive' in disc_meta:
            raise Exception(f"Cannot define 'disc' and 'archive' '{software_id}' in {toml_path}")
        
        known_hash = hashes.get(software_id, None)
        if 'disc' in disc_meta:
            disc_meta['disc'], hash = fetch_file(root, disc_meta['disc'], software_id, known_hash)
        elif 'archive' in disc_meta:
            disc_meta['archive'], hash = fetch_file(root, disc_meta['archive'], software_id, known_hash)

        if known_hash and known_hash != hash:
            raise Exception(f"file hash for {software_id} doesn't match")
        else:
            hashes[software_id] = hash

        for field in MANDATORY_FIELDS:
            if type(field) == str:
                if field not in disc_meta:
                    raise Exception(f"Field '{field}' missing from '{software_id}' in {toml_path}")
        
        if 'tags' in disc_meta:
            tags = disc_meta['tags'].split(',')
            disc_meta['tags'] = tags
            for t in tags:
                if t not in VALID_TAGS:
                    raise Exception(f"'{software_id}' in {toml_path}: Unknown tag '{t}'")

        if 'best-os' in disc_meta and disc_meta['best-os'] not in VALID_OS:
            raise Exception(f"'{software_id}' in {toml_path}: Invalid best-os: {disc_meta['best-os']}")

        if 'best-cpu' in disc_meta and disc_meta['best-cpu'] not in VALID_CPU:
            raise Exception(f"'{software_id}' in {toml_path}: Invalid best-cpu: {disc_meta['best-cpu']}")

        if 'min-mem' in disc_meta:
            if disc_meta['min-mem'] not in VALID_MEM:
                raise Exception(f"'{software_id}' in {toml_path}: Invalid min-mem: {disc_meta['min-mem']}")
            mem_kb = int(disc_meta['min-mem'][0].removesuffix('MB')) * 1024
            disc_meta['min-mem'] = mem_kb

        if 'depends' in disc_meta:
            deps = disc_meta['depends'].split(',')
            disc_meta['depends'] = deps
            all_deps[software_id] = deps


        if 'sound-filter' in disc_meta:
            sf = disc_meta['sound-filter']
            assert type(sf) == int and 0 <= sf <= 2
        for field in disc_meta.keys():
            if field not in VALID_FIELDS:
                raise Exception(f"Unknown field '{field}' in '{software_id}' ({toml_path})")

        all_software_ids[software_id] = toml_path
        disc_meta['id'] = software_id
        with open(toml_hash_path, 'w') as f:
            toml.dump(hashes, f)

    return data

def fetch_file(root, path, software_id, known_hash):
    new_name = filename_to_canonical(path, software_id)
    dst_path = os.path.join(out_dir, new_name)
    cache_path, hash = fetch_cached(path, software_id, known_hash)

    if not cache_path:     
        if path.startswith('http://') or path.startswith('https://'):
            #print(f'fetching {software_id} from {path}')
            cache_path, hash = fetch_url(root, path, software_id, known_hash)
        else:
            #print(f'fetching {software_id} from local path {path}')
            cache_path, hash = fetch_local(root, path, software_id, known_hash)
    shutil.copy(cache_path, dst_path)
    return new_name, hash


def filename_to_canonical(filename_or_url: str, software_id):
    if re.search(RISC_OS_COMMA_FILETYPE_PATTERN, filename_or_url, re.IGNORECASE):
        base_name, filetype = filename_or_url.rsplit(',', 1)
        ext = '.' + RISC_OS_TYPE_MAP.get(filetype.lower(), None)
    else:
        base_name, ext = os.path.splitext(os.path.basename(filename_or_url))
    if (not ext or ext.lower() not in VALID_FILE_EXTS) and 'jaspp' in filename_or_url:
        ext = '.zip'
    if not ext or ext.lower() not in VALID_FILE_EXTS:
        raise Exception(f"bad extension: {filename_or_url} ({software_id})")
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
        print(f'warning - wrong hash for cached file {software_id}')
        return None, None
    return cache_path, hash

def fetch_url(root, url, software_id, known_hash=None):
    new_name = filename_to_canonical(url, software_id)
    cache_path = os.path.join(CACHE_DIR, new_name)

    print(f'Downloading {url}')
    r = requests.get(url)
    r.raise_for_status()
    file_data = r.content
    hash = hashlib.sha256(file_data).hexdigest()

    if known_hash and hash != known_hash:
        raise Exception(f'incorrect hash for {software_id} downloaded from {url}')
        
    with open(cache_path, 'wb') as f:
        f.write(file_data)
   
    return cache_path, hash

def fetch_local(root, path, software_id, known_hash) -> str:
    global out_dir
    src_path = os.path.join(root, path)
    new_name = filename_to_canonical(path, software_id)
    cache_path = os.path.join(CACHE_DIR, new_name)

    if not os.path.isfile(src_path):
        raise Exception(f"file {src_path} does not exist ({software_id})")
    #print(f'copying {src_path} to {cache_path}')
    shutil.copy(src_path, cache_path)
    with open(src_path,'rb') as f:
        hash = hashlib.sha256(f.read()).hexdigest()
    if known_hash and hash != known_hash:
        raise Exception(f"Incorrect hash for {software_id} at {path}")
    return cache_path, hash 

if __name__ == '__main__':
    src_dir = None
 
    try:
        src_dir, out_dir = sys.argv[1:3]
    except:
        print(f"Usage: {sys.argv[0]} src_dir out_dir")
        sys.exit(-1)

    assert os.path.isdir(src_dir), f"No such src_dir {src_dir}"
    
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)
    json_data = {}
    for root, file in find_toml_files(src_dir):
        data = parse_toml(root, file)
        json_data |= data

    for software_id, deps in all_deps.items():
        for d in deps:
            if d not in all_software_ids:
                raise Exception(f'{software_id} depends on unknown ID {d}')
            if 'archive' not in json_data[d]:
                raise Exception(f'{software_id} depends on ID {d} which is a disc?')

    json_out = os.path.join(out_dir, 'software.json')
    with open(json_out, 'w') as f:
        json.dump(json_data, f, indent=True)
    print(f'Created JSON at {json_out}')
 