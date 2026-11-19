#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "tomlkit>=0.13",
# ]
# ///
"""
Moves a title from a .toml catalogue file into its own .md file: finds the
.toml under the catalogue directory that holds software_id, writes
<software_id>.md beside it (its fields as TOML front matter, the title as
the H1 and the description after it) and removes the entry from the .toml.

The rest of the .toml is left as it was. The entry is its [software_id]
line, any comment lines directly above it, and its fields, with any comments
among them or directly after them; those comments go into the front matter.
Comments after a blank line belong to what follows, so they stay.

Screenshots and the hash file stay as they are, since the .md is in the same
directory.
"""
import argparse
import re
import sys
from pathlib import Path

import tomlkit

DEFAULT_CATALOGUE = Path(__file__).resolve().parent / 'catalogue'
TABLE_HEADER = re.compile(r'\s*\[\s*("[^"]*"|\'[^\']*\'|[A-Za-z0-9_-]+)\s*(\.|\])')


def find_title(catalogue: Path, software_id):
    """(path, its text) of the .toml that has software_id"""
    found = []
    for path in sorted(catalogue.rglob('*.toml')):
        if path.name.endswith('-hashes.toml'):
            continue
        text = path.read_text(encoding='utf-8')
        if software_id in tomlkit.parse(text):
            found.append((path, text))
    if not found:
        if md := next(catalogue.rglob(f'{software_id}.md'), None):
            sys.exit(f'{software_id} is already a .md: {md}')
        sys.exit(f'{software_id} is not in any .toml under {catalogue}')
    if len(found) > 1:
        sys.exit(f"{software_id} is in more than one file: {', '.join(str(p) for p, _ in found)}")
    return found[0]


def classify(lines):
    """For each line: 'header' (with its table name), 'comment', 'blank' or
    'value' (a key, or part of a multi-line value)"""
    kinds = []
    in_string = None            # the quotes of the multi-line string we're in
    for line in lines:
        stripped = line.strip()
        if in_string:
            kinds.append(('value', None))
        elif not stripped:
            kinds.append(('blank', None))
        elif stripped.startswith('#'):
            kinds.append(('comment', None))
        elif m := TABLE_HEADER.match(line):
            kinds.append(('header', m[1].strip('"\'')))
        else:
            kinds.append(('value', None))
        # Track multi-line strings (and arrays aren't a problem: their lines
        # are values either way)
        for q in re.findall(r'"""|\'\'\'', line):
            if in_string is None:
                in_string = q
            elif in_string == q:
                in_string = None
    return kinds


def entry_span(lines, software_id):
    """(start, header, end): the lines of software_id's entry are
    lines[start:end], with its [software_id] line at header"""
    kinds = classify(lines)
    headers = [i for i, (k, name) in enumerate(kinds) if k == 'header' and name == software_id]
    if len(headers) != 1:
        sys.exit(f"can't find a single [{software_id}] line")
    header = headers[0]
    start = header
    while start > 0 and kinds[start - 1][0] == 'comment':
        start -= 1
    end = header + 1
    while end < len(lines) and not (kinds[end][0] == 'header' and kinds[end][1] != software_id):
        end += 1
    # Comments after a blank line belong to what follows
    last = header
    for i in range(header + 1, end):
        if kinds[i][0] == 'value':
            last = i
    end = last + 1
    while end < len(lines) and kinds[end][0] == 'comment':
        end += 1
    return start, header, end


def to_markdown(software_id, lines, start, header, end):
    body = tomlkit.parse(''.join(lines[header + 1:end]))
    if 'title' not in body:
        sys.exit(f'{software_id} has no title')
    title = str(body['title']).strip()
    description = str(body.get('description', '')).strip()
    # Keep comments on the title and description lines, as lines of their own
    kept = ''
    for key in ('title', 'description'):
        if key in body:
            if comment := body.item(key).trivia.comment.strip():
                kept += f'{comment}  ({key})\n'
            del body[key]
    front = ''.join(lines[start:header]) + kept + tomlkit.dumps(body).strip('\n') + '\n'
    md = f'+++\n{front.lstrip(chr(10))}+++\n\n# {title}\n'
    if description:
        md += f'\n{description}\n'
    return md


def check(software_id, old_toml, new_toml, md):
    """Stop unless new_toml is old_toml without software_id, and md's front
    matter is software_id's fields"""
    old, new = tomlkit.parse(old_toml).unwrap(), tomlkit.parse(new_toml).unwrap()
    entry = old.pop(software_id)
    if old != new:
        sys.exit("bug: removing the entry would change others in the .toml, so nothing's been changed")
    front = tomlkit.parse(md.split('+++\n')[1]).unwrap()
    entry.pop('title'), entry.pop('description', None)
    if front != entry:
        sys.exit("bug: the front matter doesn't match the entry, so nothing's been changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('software_id')
    parser.add_argument('--catalogue', type=Path, default=DEFAULT_CATALOGUE,
                        help=f'directory to look in (default: {DEFAULT_CATALOGUE})')
    parser.add_argument('-n', '--dry-run', action='store_true',
                        help="print the .md and don't change anything")
    args = parser.parse_args()
    software_id = args.software_id

    toml_path, text = find_title(args.catalogue, software_id)
    md_path = toml_path.parent / f'{software_id}.md'
    if existing := next(args.catalogue.rglob(f'{software_id}.md'), None):
        sys.exit(f'{existing} already exists')
    lines = text.splitlines(keepends=True)
    start, header, end = entry_span(lines, software_id)
    md = to_markdown(software_id, lines, start, header, end)

    # Drop one blank line around the entry, so its neighbours keep one between them
    rest_before, rest_after = lines[:start], lines[end:]
    if rest_after and not rest_after[0].strip():
        rest_after = rest_after[1:]
    elif not rest_after:
        while rest_before and not rest_before[-1].strip():
            rest_before = rest_before[:-1]
    new_text = ''.join(rest_before + rest_after)
    check(software_id, text, new_text, md)

    if args.dry_run:
        print(md, end='')
        return
    md_path.write_text(md, encoding='utf-8')
    toml_path.write_text(new_text, encoding='utf-8')
    print(f'wrote {md_path}, removed {software_id} from {toml_path}')
    if not tomlkit.parse(new_text):
        print(f'{toml_path} has no titles left')


if __name__ == '__main__':
    main()
