#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "markdown-it-py>=4.0",
#     "mdit-py-plugins>=0.5",
#     "pycurl>=7.45",
#     "riscos-conv==0.0.3",
#     "toml>=0.10.2",
#     "tqdm>=4.66",
# ]
# ///
"""
Recursively scans src_dir for .toml files containing catalogues of disc
images (e.g. .adf) and archive files (e.g. .zip), and .md files that each
describe one title. Creates a single software.json file in out_dir and
copies/downloads the software to that directory. The software files are
renamed according to the software ID in the toml (or the .md's name).

A .md file starts with TOML front matter between +++ lines, holding the same
fields as a TOML entry except title and description. Its H1 is the title, and
everything after it the description. Descriptions (from either) are Markdown:
the text up to the first heading is the short description, which goes in
software.json as HTML. A longer one goes in docs/ as an HTML fragment, named
in full-description. A "Quick Start" section goes in quickstart as HTML.

Downloads are kept in dlcache, and each file's hash in a hash file in the
directory of the .toml or .md that uses it, named after the directory (e.g.
jaspp/jaspp-hashes.toml), which holds the hashes for all the titles there.
A file whose hash doesn't match is an error, unless --refresh is given.

The work is done by the modules in arclivecat/.
"""
import argparse
import json
import sys
from pathlib import Path

import toml
from toml.decoder import TomlDecodeError
from tqdm import tqdm

from arclivecat import errors
from arclivecat.descriptions import DOCS_DIR, convert_description, parse_markdown_file
from arclivecat.errors import CatalogueError, report, show_status
from arclivecat.fetch import Fetcher
from arclivecat.fields import validate
from arclivecat.hashes import HashStore
from arclivecat.screenshots import find_screenshots
from arclivecat.software import check_app_path, check_files, extract_icon, load_software


def find_catalogue_files(root_dir: Path):
    """Each .toml and .md file under root_dir, a directory's own files before
    those in its subdirectories"""
    files = (p for p in root_dir.rglob('*') if p.is_file() and (
        p.suffix == '.md' or p.suffix == '.toml' and not p.name.endswith('-hashes.toml')))
    return sorted(files, key=lambda p: (str(p.parent), p.name))


def load_catalogue_file(path: Path):
    """({software_id: meta}, {software_id: description nodes}) from a .toml
    or .md file. Only a .md has description nodes"""
    if path.suffix == '.md':
        return parse_markdown_file(path)
    try:
        return toml.load(path), {}
    except TomlDecodeError as e:
        raise CatalogueError(f"TOML error in file {path}: " + str(e))


class Build:
    def __init__(self, out_dir: Path, refresh=False):
        self.out_dir = out_dir
        self.fetcher = Fetcher(out_dir, refresh)
        self.hashes = HashStore()
        self.catalogue = {}         # software_id -> meta, for software.json
        self.paths = {}             # software_id -> the file it's from (a Path)
        for d in ('screenshots', 'icons', DOCS_DIR):
            (out_dir / d).mkdir(parents=True, exist_ok=True)

    def add_file(self, path: Path):
        titles, descriptions = load_catalogue_file(path)
        for software_id, meta in titles.items():
            self.add_title(path.parent, path, software_id, meta, descriptions.get(software_id))
            if errors.progress:
                errors.progress.update()

    def add_title(self, root, path, software_id, meta, description_nodes):
        show_status(software_id)
        if software_id in self.paths:
            raise CatalogueError(f"Duplicate software id '{software_id}' in {path} "
                                 f"(existing one in {self.paths[software_id]})")
        validate(meta, software_id, path)
        meta['id'] = software_id

        hashes = self.hashes.get(root)
        known_hash = hashes.get(software_id)
        file_field = 'disc' if 'disc' in meta else 'archive'
        source = meta[file_field]
        meta[file_field], hash, refreshed = self.fetcher.fetch(
            root, source, software_id, known_hash, meta.get('app-path'), path)
        if known_hash and known_hash != hash and not refreshed:
            raise CatalogueError(f"file hash for {software_id} doesn't match")
        if not known_hash and not refreshed:
            report(f'{software_id}: new file {source} (hash {hash[:12]})')
        hashes[software_id] = hash

        convert_description(meta, self.out_dir, description_nodes)

        app_path = meta.get('app-path')
        expose_files = meta.get('expose-files')
        if app_path or expose_files:
            disc = load_software(self.out_dir / meta[file_field], software_id)
        if app_path:
            check_app_path(disc, software_id, app_path, meta[file_field], path)
            icon = f'icons/{software_id}.png'
            if extract_icon(disc, software_id, app_path, meta[file_field], self.out_dir / icon):
                meta['icon'] = icon
        if expose_files:
            check_files(disc, software_id, expose_files, meta[file_field], path)

        if screenshots := find_screenshots(root, software_id, self.out_dir / 'screenshots'):
            meta['screenshots'] = screenshots

        self.catalogue[software_id] = meta
        self.paths[software_id] = path
        self.hashes.save(root)

    def finish(self):
        """Checks dependencies, drops the hashes of titles that have gone, and
        writes software.json"""
        for software_id, meta in self.catalogue.items():
            for d in meta.get('depends', []):
                if d not in self.catalogue:
                    raise CatalogueError(f'{software_id} depends on unknown ID {d}')
                if 'archive' not in self.catalogue[d]:
                    raise CatalogueError(f'{software_id} depends on ID {d} which is a disc?')

        ids_by_dir = {}
        for software_id, path in self.paths.items():
            ids_by_dir.setdefault(path.parent, set()).add(software_id)
        self.hashes.prune(ids_by_dir)

        with (self.out_dir / 'software.json').open('w') as f:
            json.dump(self.catalogue, f, indent=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('src_dir', type=Path, help='directory to scan for .toml and .md catalogue files')
    parser.add_argument('out_dir', type=Path, help='directory for software.json and the software')
    parser.add_argument('--refresh', action='store_true',
                        help='fetch every file again (each URL, or each local file that is there) '
                             'rather than using dlcache. A file that has changed is checked (riscos-conv '
                             'must read it, and it must hold its app-path), then replaces the one in '
                             'dlcache and its hash. A URL that fails to download only gives a warning, '
                             'and the copy in dlcache is used')
    args = parser.parse_args()
    if not args.src_dir.is_dir():
        parser.error(f"No such src_dir {args.src_dir}")

    files = find_catalogue_files(args.src_dir)
    total_items = 0
    for path in files:
        try:
            total_items += len(load_catalogue_file(path)[0])
        except CatalogueError:
            pass        # reported when it's built
    errors.progress = tqdm(total=total_items, unit='title', dynamic_ncols=True, leave=False,
                           disable=None)     # off when stderr isn't a terminal (e.g. a log)
    try:
        build = Build(args.out_dir, args.refresh)
        for path in files:
            build.add_file(path)
        build.finish()
    except CatalogueError as e:
        errors.progress.close()
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
    errors.progress.close()


if __name__ == '__main__':
    main()
