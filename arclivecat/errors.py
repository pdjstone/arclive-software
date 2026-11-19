"""Errors, and messages printed while building"""
import sys

from tqdm import tqdm

# The progress bar, when there is one
progress = None


class CatalogueError(Exception):
    """Something wrong with the catalogue or its files, which stops the build.
    Shown as just its message"""


def report(msg):
    """Say what changed (in dlcache or a hash file)"""
    tqdm.write(msg)


def warn(msg):
    tqdm.write(f'Warning: {msg}', file=sys.stderr)


def show_status(text):
    """Show what's happening (e.g. a download) beside the progress bar"""
    if progress:
        progress.set_postfix_str(text)
