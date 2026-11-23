"""The fields a title can have, and checking them"""
from .errors import CatalogueError

MANDATORY_FIELDS = (
    'title',
    'year',
    'tags',
)

VALID_FIELDS = (
    'author', 'publisher',

    'version', # version number or string
    'disc', 'archive', # filename (relative to TOML file) or URL of disc image/archive file
    'tags', # comma-separated list of valid tags (see below)
    'description', # Markdown
    'best-os', # must be a valid OS from below.
    'best-cpu', # must be a valid CPU from below.
    'min-mem', # 1MB/2MB/4MB/8MB
    'info-url', # link to software homepage or further information
    'app-path', # path of app to launch when autobooting, relative to root of disc or archive
                # (e.g. "!Foo" if in root of disc/archive or "dirname.!Foo")
                # Also used for icon extraction
    'click-icon', # set to true to launch app after loading it by clicking its icon on the icon bar
    'autoboot', # custom boot script, which will override the default "*desktop filer_run <path.to.app-path>"
    'ff-ms', # number of milliseconds to fast-forward when autobooting
    'sound-filter', # 0 (full), 1 (reduced) or 2 (more reduced)
    'depends', # archive dependencies
    'mouse-capture', # force/never (auto is default, so never needed)
    'expose-files', # list of files in the disc or archive (paths relative to its root)
                    # to make available in the UI, e.g. a readme
    'boot-overrides', # for ADFFS only: a directory (relative to the TOML file) of boot
                      # scripts to put in !ADFFS.obey in place of ADFFS's own, each named
                      # by the disc ID of the game it's for (see arclivecat/adffs.py)
)

VALID_FIELDS = set(VALID_FIELDS + MANDATORY_FIELDS)

VALID_TAGS = (
    'game',
    'demo',
    'public-domain',
    'desktop',
    'education',
    'utility',
    'music',
    'ex-commercial',
    'demoscene',
    'hidden' # don't show in software browser, but allow loading the game via URL param
)

VALID_OS = ('arthur120', 'riscos201', 'riscos311')
VALID_CPU = ('arm2', 'arm3', 'arm250')
VALID_MEM = ('1MB', '2MB', '4MB', '8MB')
VALID_MOUSE_CAPTURE = ('force', 'never')


def validate(meta, software_id, path):
    """Checks a title's fields (meta, from the file at path), and turns tags
    and depends into lists, and min-mem into kB"""
    def fail(msg):
        raise CatalogueError(f"'{software_id}' in {path}: {msg}")

    for field in meta:
        if field not in VALID_FIELDS:
            fail(f"Unknown field '{field}'")
    for field in MANDATORY_FIELDS:
        if field not in meta:
            fail(f"Field '{field}' missing")

    if 'disc' not in meta and 'archive' not in meta:
        fail("Must have 'disc' or 'archive' field")
    if 'disc' in meta and 'archive' in meta:
        fail("Cannot have both 'disc' and 'archive'")

    meta['tags'] = meta['tags'].split(',')
    for t in meta['tags']:
        if t not in VALID_TAGS:
            fail(f"Unknown tag '{t}'")

    for field, valid in (('best-os', VALID_OS), ('best-cpu', VALID_CPU),
                         ('mouse-capture', VALID_MOUSE_CAPTURE), ('min-mem', VALID_MEM)):
        if field in meta and meta[field] not in valid:
            fail(f"Invalid {field}: {meta[field]} (must be one of {', '.join(valid)})")

    if 'min-mem' in meta:
        meta['min-mem'] = int(meta['min-mem'].removesuffix('MB')) * 1024

    if 'depends' in meta:
        meta['depends'] = meta['depends'].split(',')

    if 'sound-filter' in meta:
        sf = meta['sound-filter']
        if type(sf) is not int or not 0 <= sf <= 2:
            fail(f'Invalid sound-filter: {sf!r} (must be 0, 1 or 2)')

    if 'boot-overrides' in meta:
        if type(meta['boot-overrides']) is not str:
            fail('boot-overrides must be a directory name')
        if 'archive' not in meta:
            fail('boot-overrides needs an archive')

    expose_files = meta.get('expose-files', [])
    if type(expose_files) is not list or not all(type(f) is str for f in expose_files):
        fail('expose-files must be a list of paths')
