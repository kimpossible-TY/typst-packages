"""Plain temporary fixtures for the internal paragraph repair tests only.

The only executable used by these fixtures is the pinned syntax parser. The
reviewed package is copied as read-only evidence, never imported or evaluated.
"""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
PREFIX = (b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
          b'#show: apply-paragraph-tabs\n')
ROOT = PREFIX + b'\n#paragraph_tab\nthis preserves iPhone eBay NASA and lowerCase.\n'
EXPECTED = ROOT.replace(b'\nthis ', b'\nThis ')
CONFIG = b'rule-set="pde"\ninclude=["main.typ"]\n[capitalization]\nlanguage="en"\n'


def fixture(base, sequence, *, source=ROOT, target='main.typ', config=None):
    from transactions import OfflineExclusive
    import typst_lint as lint
    base = base / f'fixture-{sequence}'
    root = base / 'consumer'
    root.mkdir(parents=True)
    store = base / 'journals'
    store.mkdir(mode=0o700)
    path = root / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source)
    path.chmod(0o640)
    config_path = root / '.typst-lint.toml'
    if config is None:
        config = ('rule-set="pde"\ninclude=[' + json.dumps(target)
                  + ']\n[capitalization]\nlanguage="en"\n').encode()
    config_path.write_bytes(config)
    catalog = base / 'reviewed-catalog.json'
    catalog.write_bytes(lint.CATALOG.read_bytes())
    package_path = base / 'packages'
    leaf = package_path / 'local/text-utils/0.1.2'
    leaf.mkdir(parents=True)
    for name in ('typst.toml', 'lib.typ'):
        (leaf / name).write_bytes(((TOOL / 'tests/fixtures/reviewed-packages') / 'local/text-utils/0.1.2' / name).read_bytes())
        (leaf / name).chmod(0o444)
    unrelated = root / 'unselected.typ'
    unrelated.write_bytes(b'\xff #read("unselected-never")\n')
    return SimpleNamespace(base=base, root=root, target=target, source=path,
        original=source, config=config_path, catalog=catalog,
        package_path=package_path, manifest=leaf / 'typst.toml',
        package=leaf / 'lib.typ', store=store, unrelated=unrelated,
        lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers())


@contextmanager
def context(f, **kwargs):
    import captured_text_utils_paragraph_inputs as inputs
    import captured_text_utils_paragraph_fix as bridge
    options = dict(package_path=f.package_path, journal_directory=f.store, lease=f.lease)
    options.update(kwargs)
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(inputs, 'CATALOG', f.catalog))
        yield stack.enter_context(bridge.ProtectedCapturedTextUtilsParagraphFix(
            f.root, f.target, **options))


def evidence(f):
    return (f.config, f.catalog, f.manifest, f.package, f.unrelated)


def state(path):
    import transactions
    value = path.stat()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = transactions._xattrs(fd)
    finally:
        os.close(fd)
    return (path.read_bytes(), value.st_dev, value.st_ino, stat.S_IMODE(value.st_mode),
            value.st_uid, value.st_gid, metadata)


def same_byte_replace(path):
    value = path.stat()
    temporary = path.with_name(path.name + '.test-user-save')
    temporary.write_bytes(path.read_bytes())
    temporary.chmod(stat.S_IMODE(value.st_mode))
    temporary.replace(path)
    assert value.st_ino != path.stat().st_ino


@contextmanager
def changed(obj, name, value):
    original = getattr(obj, name)
    object.__setattr__(obj, name, value)
    try:
        yield
    finally:
        object.__setattr__(obj, name, original)


def set_fixture_xattr(path, value):
    import transactions
    name = 'com.typst-lint.paragraph-test' if os.sys.platform == 'darwin' else 'user.paragraph-test'
    mode = stat.S_IMODE(path.stat().st_mode)
    # macOS requires owner write permission for fsetxattr. Restore the exact
    # read-only mode so the drift case changes real xattr evidence, not mode.
    path.chmod(mode | stat.S_IWUSR)
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        attrs = tuple((key, raw) for key, raw in transactions._xattrs(fd) if key != name)
        transactions._copy_xattrs(fd, tuple(sorted((*attrs, (name, value)))))
        assert (name, value) in transactions._xattrs(fd)
    finally:
        if fd is not None:
            os.close(fd)
        path.chmod(mode)
