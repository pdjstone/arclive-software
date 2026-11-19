"""The hash files: one per directory, named after it (e.g.
jaspp/jaspp-hashes.toml), with the hashes of the files of all the titles
there"""
from pathlib import Path

import toml

from .errors import report


def hash_file_path(root):
    root = Path(root)
    return root / f'{root.resolve().name}-hashes.toml'


class HashStore:
    def __init__(self):
        # directory (a Path) -> {software_id: hash}
        self.dirs = {}

    def get(self, root):
        """The hashes for directory root ({software_id: hash}), to read and change"""
        root = Path(root)
        if root not in self.dirs:
            path = hash_file_path(root)
            self.dirs[root] = toml.load(path) if path.is_file() else {}
        return self.dirs[root]

    def save(self, root):
        root = Path(root)
        with hash_file_path(root).open('w') as f:
            toml.dump(self.dirs[root], f)

    def prune(self, ids_by_dir):
        """Drop the hashes of titles that are no longer in their directory
        (ids_by_dir: directory, as a Path -> the IDs there)"""
        for root, hashes in self.dirs.items():
            if gone := set(hashes) - ids_by_dir.get(root, set()):
                for id in gone:
                    del hashes[id]
                report(f"{hash_file_path(root)}: removed hashes for {', '.join(sorted(gone))}")
                self.save(root)
