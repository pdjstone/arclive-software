#!/usr/bin/env python3

from typing import Iterator
import requests
import sys
import tomlkit
import re 
from urllib.parse import urlparse

def chunk_packages(line_iter: Iterator[str]):
    last_line = None
    cur_pkg = {}
    k = v = None
    for l in line_iter:
        if last_line == '' and l.strip() == '':
            cur_pkg[k] = v
            k = v = None
            yield cur_pkg
            cur_pkg = {}
            continue
        last_line = l.strip()
        if l.strip() == '':
            continue
        if l.startswith(' '):
            l = l.strip()
            if l == '.':
                l = ''
            v += '\n' + l
        else:    
            if k:
                cur_pkg[k] = v
                k = v = None
            k,v = l.split(':', 1)
            v = v.strip()
        
def ro_path_to_unix_path(path):
    path = path.replace('.', '/')
    return path

def fetch_packman_index_from_url(url):
    resp = requests.get(repo_url, stream=True)
    resp.encoding = 'utf-8'
    if not resp.ok:
        raise Exception("Couldn't fetch repo " + url)
    yield from chunk_packages(resp.iter_lines(decode_unicode=True))


def keywords_in_str(s, kws):
    for kw in kws:
        if kw in s:
            return True
    return False


def filter_package(pkg):
    pkg_id = pkg['Package']
    pkg_id_blacklist = '[SA]', '[RPC]'
    #if keywords_in_str(pkg_id, pkg_id_blacklist):
    #    return False
    if 'Krisalis' not in pkg['Description']:
        return False
    return True

def make_toml(repo_url, packages) -> tomlkit.document:

    url_bits = urlparse(repo_url)
    base_url = url_bits.scheme + '://' + url_bits.netloc
    doc = tomlkit.document()
    doc.add(tomlkit.comment(f"Generated from packman index at: {repo_url}"))
    
    for pkg in packages:
        package_id = pkg['Package'].replace('[', '').replace(']', '').lower()
        arc_meta = {}
        
        desc = pkg['Description']
        title, body = desc.split('\n', 1)
        if m := re.match('^(.*)\((\d+)\) \(([^)]+)\)', title):
            #print(m.groups())
            title, year, publisher = m.groups()
            arc_meta['title']  = title.strip()
            arc_meta['year'] = int(year)
            arc_meta['publisher'] = publisher
        else:
            arc_meta['title'] = title
        if package_id != 'adffs':
            arc_meta['depends'] = 'adffs'
        arc_meta['ff-ms'] = 23000
        arc_meta['min-mem'] = '4MB' # ADFFS takes up ~700KB
        arc_meta['description'] = tomlkit.string(body, multiline=True)
        arc_meta['archive'] = base_url + pkg['URL']
        arc_meta['tags'] = 'game,ex-commercial'
        if 'Components' in pkg:
            app_path = pkg['Components'].removesuffix(' (Movable)')
            arc_meta['app-path'] = app_path
        doc[package_id] = arc_meta
    return doc
    
if __name__ == '__main__':
    repo_url = sys.argv[1]

    all_packages = fetch_packman_index_from_url(repo_url)
    wanted_packages = filter(filter_package, all_packages)

    toml_data = make_toml(repo_url, wanted_packages)
    tomlkit.dump(toml_data, sys.stdout)
    
