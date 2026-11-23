"""Replacing the boot scripts that ADFFS runs for games.

ADFFS keeps a boot script for each game it knows in !ADFFS.obey, a zip of
Obey files named by the game's disc ID (e.g. F1026001 for Nebulus).
*ADFBootFloppy <ID> (what a JASPP package's !Run does) takes the script out
of the zip and runs it. To change what a game does, the ADFFS title's archive
is rebuilt with the scripts in the boot-overrides directory beside its
catalogue file in place of ADFFS's own, keeping every file's RISC OS
metadata."""
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

# Makes zipfile keep the Acorn extra fields that hold RISC OS file types,
# and write leafnames as Latin-1
from riscosconv import riscos_zip  # noqa: F401

from .errors import CatalogueError

OBEY_ZIP = '!ADFFS/obey'

# The ADFFS title, and the directory of scripts (beside its catalogue file),
# each named by the disc ID of the script it replaces
ADFFS_ID = 'adffs'
BOOT_OVERRIDES_DIR = 'boot-overrides'


def replace_zip_files(zip_data: bytes, replacements: dict[str, bytes], what: str) -> bytes:
    """zip_data with the files named in replacements (by their name in the
    zip) given new contents. Everything else about them (date, RISC OS file
    type, compression) is kept, as is every other file"""
    missing = set(replacements)
    out = BytesIO()
    try:
        with ZipFile(BytesIO(zip_data)) as src, ZipFile(out, 'w') as dst:
            for info in src.infolist():
                data = src.read(info)
                if info.filename in replacements:
                    data = replacements[info.filename]
                    missing.discard(info.filename)
                dst.writestr(info, data)
    except BadZipFile as e:
        raise CatalogueError(f'{what} is not a zip I can read: {e}')
    if missing:
        raise CatalogueError(f"{what} has no {', '.join(sorted(missing))}")
    return out.getvalue()


def override_boot_scripts(archive: bytes, scripts_dir: Path, software_id) -> bytes:
    """The ADFFS archive with the scripts in its !ADFFS.obey replaced by the
    files in scripts_dir, each named by the disc ID of the script it
    replaces"""
    scripts = {p.name: p.read_bytes() for p in sorted(scripts_dir.iterdir())
               if p.is_file() and not p.name.startswith('.')}
    if not scripts:
        raise CatalogueError(f'{software_id}: no scripts in {scripts_dir}')
    try:
        with ZipFile(BytesIO(archive)) as zf:
            obey = zf.read(OBEY_ZIP)
    except KeyError:
        raise CatalogueError(f"{software_id}: the archive has no {OBEY_ZIP} (is it ADFFS?)")
    obey = replace_zip_files(obey, scripts, f'{software_id} {OBEY_ZIP}')
    return replace_zip_files(archive, {OBEY_ZIP: obey}, software_id)
