"""Screenshots: <id>-<n>.png beside the title's .toml or .md"""
import re
import shutil
import subprocess
from pathlib import Path

from .errors import warn

pngcrush_available = shutil.which('pngcrush')


def copy_img(src: Path, dst: Path):
    """Copy a PNG, crushing it first (in place) if pngcrush is there and it
    hasn't been already"""
    assert src.suffix.lower() == '.png'
    if pngcrush_available:
        with src.open('rb') as f:
            header = f.read(0x50)
        if b'pngcrush' not in header:
            r = subprocess.run(['pngcrush', '-ow', '-text', 'b', 'Software', 'pngcrush', src],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if r.returncode != 0:
                warn(f'pngcrush failed on {src}: {r.stderr.strip()}')
    shutil.copyfile(src, dst)


def find_screenshots(root, software_id, out_dir):
    """Copies software_id's screenshots in root to out_dir, and returns their
    names (or None)"""
    pattern = re.escape(software_id) + r'-\d+\.png$'
    root, out_dir = Path(root), Path(out_dir)
    files = sorted(f.name for f in root.iterdir() if re.match(pattern, f.name))
    for f in files:
        copy_img(root / f, out_dir / f)
    return files or None
