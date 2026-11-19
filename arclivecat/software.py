"""Reading the disc images and archives, with riscos-conv: checking what they
hold, and taking icons from them"""
import tempfile
from pathlib import Path

from riscosconv.cli import load_disc
from riscosconv.riscos_path import PureRiscOsPath
from riscosconv.sprites import SpriteArea

from .errors import CatalogueError, warn


def load_software(path, software_id):
    """The disc image or archive at path, opened"""
    try:
        disc = load_disc(str(path))
    except Exception as e:
        raise CatalogueError(f"Could not load {path} ('{software_id}'): {e!r}") from e
    if not disc:
        raise CatalogueError(f"Could not load {path} ('{software_id}'), "
                             "so can't check what it holds")
    return disc


def path_key(path):
    """RISC OS names are case-insensitive"""
    return tuple(p.lower() for p in PureRiscOsPath(path).parts)


def check_app_path(disc, software_id, app_path, file_name, toml_path):
    """Check app_path is in the disc or archive (file_name): an app directory
    (which the listing doesn't give, only the files in it) or a file"""
    want = path_key(app_path)
    tops = set()
    for path, _ in disc.list():
        if path_key(path)[:len(want)] == want:
            return
        tops.add(PureRiscOsPath(path).parts[0])
    raise CatalogueError(f"'{software_id}' in {toml_path}: app-path '{app_path}' isn't in "
                         f"{file_name} (its root holds: {', '.join(sorted(tops))})")


def check_files(disc, software_id, paths, file_name, toml_path):
    """Check each of paths is a file in the disc or archive (file_name)"""
    files = {path_key(path) for path, _ in disc.list()}
    for path in paths:
        if path_key(path) not in files:
            raise CatalogueError(f"'{software_id}' in {toml_path}: expose-files has '{path}', "
                                 f"which isn't a file in {file_name}")


def extract_icon(disc, software_id, app_path, file_name, out_path):
    """Save the app's icon (its sprite in its !Sprites) to out_path as a PNG.
    Returns whether there was one"""
    app_path = PureRiscOsPath(app_path)
    sprite_fd = None
    try:
        for sf in ('!Sprites22', '!Sprites'):
            if sprite_fd := disc.open(app_path / sf):
                break
    except Exception as e:
        raise CatalogueError(f"Could not read {app_path}'s sprites from {file_name} "
                             f"('{software_id}'): {e!r}") from e
    if not sprite_fd:
        return False
    try:
        sprite = SpriteArea(sprite_fd)[app_path.name]
    except KeyError:
        warn(f"{software_id}: no sprite {app_path.name} in {app_path}'s sprites, so no icon")
        return False
    sprite.get_pil_image().save(out_path)
    return True


def validate_software(name, data, software_id, app_path, toml_path):
    """Raises an exception unless riscos-conv can read data (a file called
    name) and list its files, and it holds app_path (if given)"""
    with tempfile.TemporaryDirectory() as tmp:
        # load_disc tells some kinds of file by their extension
        tmp_path = Path(tmp) / name
        tmp_path.write_bytes(data)
        disc = load_disc(str(tmp_path))
        if not disc:
            raise CatalogueError("riscos-conv doesn't recognise it as a disc image or archive")
        if not list(disc.list()):
            raise CatalogueError('it holds no files')
        if app_path:
            check_app_path(disc, software_id, app_path, name, toml_path)
