"""Getting each title's file: from dlcache, a URL or the catalogue, into the
output directory"""
import hashlib
import re
import shutil
import time
from io import BytesIO
from pathlib import Path, PurePosixPath

import pycurl

from .errors import CatalogueError, report, show_status, warn
from .software import validate_software

CACHE_DIR = Path('dlcache')
RISC_OS_COMMA_FILETYPE_PATTERN = r',[a-z90-9]{3}$'
RISC_OS_TYPE_MAP = {'ddc': 'spk'}
VALID_FILE_EXTS = ('.arc', '.zip', '.adf', '.spk')


class DownloadError(Exception):
    pass


def is_url(path):
    return path.startswith('http://') or path.startswith('https://')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path):
    return sha256(path.read_bytes())


def filename_to_canonical(filename_or_url: str, software_id):
    """<software_id>.<ext>, the ext being the file's (or its RISC OS type's)"""
    if re.search(RISC_OS_COMMA_FILETYPE_PATTERN, filename_or_url, re.IGNORECASE):
        _, filetype = filename_or_url.rsplit(',', 1)
        ext = '.' + RISC_OS_TYPE_MAP.get(filetype.lower(), '')
    else:
        ext = PurePosixPath(filename_or_url).suffix
    # JASPP package URLs have no extension (e.g. .../Pipe-Mania_0-3), or a
    # version that looks like one (.../Alone-in-the-Dark_1.00-5), but are zips
    if (not ext or ext.lower() not in VALID_FILE_EXTS) and 'jaspp' in filename_or_url:
        ext = '.zip'
    if not ext or ext.lower() not in VALID_FILE_EXTS:
        raise CatalogueError(f"bad extension: {filename_or_url} ({software_id})")
    return f'{software_id}{ext}'


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


class Fetcher:
    """Gets files into out_dir, keeping them in cache_dir. With refresh, each
    is fetched again rather than taken from cache_dir"""

    def __init__(self, out_dir, refresh=False, cache_dir=CACHE_DIR):
        self.out_dir = Path(out_dir)
        self.refresh = refresh
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_path(self, path, software_id):
        return self.cache_dir / filename_to_canonical(path, software_id)

    def fetch(self, root, path, software_id, known_hash, app_path, toml_path, transform=None):
        """Puts the file (path: a URL, or relative to root, a str) in out_dir,
        changed by transform (bytes -> bytes) if it's given. Returns (its name
        there, the hash of the file as fetched, whether refresh found a new
        version of it)"""
        cache_path = hash = None
        if self.refresh:
            cache_path, hash = self.refresh_file(root, path, software_id, known_hash,
                                                 app_path, toml_path)
        refreshed = cache_path is not None and hash != known_hash

        if not cache_path:
            cache_path, hash = self.fetch_cached(path, software_id, known_hash)
        if not cache_path:
            if is_url(path):
                cache_path, hash = self.fetch_url(path, software_id, known_hash)
            else:
                cache_path, hash = self.fetch_local(root, path, software_id, known_hash)
        # Served as <id>-<hash><ext> (the hash of what's served), so each
        # version of a file has its own URL and can be cached for good. dlcache
        # keeps the plain <id><ext> name
        canonical = PurePosixPath(filename_to_canonical(path, software_id))
        if transform:
            data = transform(cache_path.read_bytes())
            new_name = f'{canonical.stem}-{sha256(data)[:12]}{canonical.suffix}'
            (self.out_dir / new_name).write_bytes(data)
        else:
            new_name = f'{canonical.stem}-{hash[:12]}{canonical.suffix}'
            shutil.copy(cache_path, self.out_dir / new_name)
        return new_name, hash, refreshed

    def refresh_file(self, root, path, software_id, known_hash, app_path, toml_path):
        """Fetch the URL, or the local file if it's there, and put it in
        dlcache. A changed file (or a new one) is only taken if riscos-conv
        can read it and it holds app_path. Returns (cache path, hash), or
        (None, None) to use dlcache as usual"""
        if is_url(path):
            try:
                data = download(path, software_id)
            except DownloadError as e:
                warn(f'could not download {software_id} from {path}: {e}')
                return None, None
        else:
            src_path = Path(root) / path
            if not src_path.is_file():
                return None, None
            data = src_path.read_bytes()

        hash = sha256(data)
        name = filename_to_canonical(path, software_id)
        cache_path = self.cache_dir / name
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
        cache_path.write_bytes(data)
        return cache_path, hash

    def fetch_cached(self, path, software_id, known_hash=None):
        cache_path = self.cache_path(path, software_id)
        if not cache_path.is_file():
            return None, None
        hash = file_sha256(cache_path)
        if known_hash is not None and known_hash != hash:
            warn(f"{cache_path} doesn't match {software_id}'s hash, so fetching it again")
            return None, None
        return cache_path, hash

    def fetch_url(self, url, software_id, known_hash=None):
        try:
            file_data = download(url, software_id)
        except DownloadError as e:
            raise CatalogueError(f"Unable to download {software_id} from {url}: {e}")
        hash = sha256(file_data)
        if known_hash and hash != known_hash:
            raise CatalogueError(f'incorrect hash for {software_id} downloaded from {url}')
        cache_path = self.cache_path(url, software_id)
        cache_path.write_bytes(file_data)
        return cache_path, hash

    def fetch_local(self, root, path, software_id, known_hash):
        src_path = Path(root) / path
        if not src_path.is_file():
            raise CatalogueError(f"file {src_path} does not exist ({software_id})")
        hash = file_sha256(src_path)
        if known_hash and hash != known_hash:
            raise CatalogueError(f"Incorrect hash for {software_id} at {path}")
        cache_path = self.cache_path(path, software_id)
        shutil.copy(src_path, cache_path)
        return cache_path, hash
