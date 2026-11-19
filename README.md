# Archimedes Live Software Catalogue

A collection of disc images and archive files that can be loaded into [Archimedes Live](https://github.com/pdjstone/archimedes-live). This repository just contains a bunch of [TOML](https://toml.io/en/) files with metadata for each piece of software title. For each title there is either a filename or URL. 

There is also a Python script which downloads or copies each disc image/archive into a single directory and creates a `software.json` file containing all the metadata. This is loaded by Archimedes Live.


## Catalogue files

Titles are defined in `catalogue/`, in either:

- a `.toml` file, with one table per title (the table name is its ID), or
- a `.md` file for one title (the file name is its ID): TOML front matter between `+++` lines, then an H1 with the title, and the description in Markdown. A `## Quick Start` section is shown beside the emulator as a reminder of the controls.

Descriptions are Markdown in both. The text up to the first heading is the short description; the whole of a longer one is written to `docs/` as HTML. Each directory has a hash file named after it (e.g. `jaspp/jaspp-hashes.toml`) with the hashes of its titles' files.

## Building

`toml2json.py` runs with [uv](https://docs.astral.sh/uv/), which installs its dependencies:

```sh
./toml2json.py catalogue/ out/            # or: uv run toml2json.py catalogue/ out/
./toml2json.py --refresh catalogue/ out/  # fetch every file again
```

Downloads are kept in `dlcache/` in the current directory.
