"""Plain owned fixtures for captured-relative compiler tests; no test classes.

These temporary programs and arbitrary font bytes are not native resources.
Only existing private reviewed-resource seams are patched by the fixture.
"""
from contextlib import contextmanager
from dataclasses import fields, replace
import json
from pathlib import Path
import sys
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_fix as bridge
import captured_relative_inputs as inputs
import closed_core_compile as runtime
from transactions import OfflineExclusive
from typst_lint import CATALOG

FONT_BYTES = b'Owned arbitrary captured-graph font fixture.\x00\xff'
FONT_IDENTITY = runtime.FontIdentity('TrueType', 'Owned Graph Fixture',
    'OwnedGraphFixture', 'Owned Graph Fixture Regular', '1', 'owned-graph-fixture')
VERSION = b'owned captured-relative compiler fixture 1\n'


def pdf_bytes():
    start = b'%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n'
    return (start + b'xref\n0 1\n0000000000 65535 f \ntrailer\n<<>>\nstartxref\n'
            + str(len(start)).encode() + b'\n%%EOF\n')


def clone_opaque(value):
    clone = object.__new__(type(value))
    for item in fields(value):
        object.__setattr__(clone, item.name, getattr(value, item.name))
    return clone


class GraphFixture:
    def __init__(self, base, *, target='nested with spaces/main.typ', sources=None):
        self.base = Path(base).resolve()
        self.base.mkdir(parents=True)
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.target = target
        if sources is None:
            parent = Path(target).parent.as_posix()
            module = ('values.typ' if parent == '.' else parent + '/values.typ')
            sources = {target: b'#import "values.typ": count\n#count $x_count$\n',
                       module: b'#let count = 7\n'}
        self.sources = dict(sources)
        for name, raw in self.sources.items():
            self.put(name, raw)
        self.config = self.put('.typst-lint.toml',
            ('rule-set="core"\ninclude=[' + json.dumps(target, ensure_ascii=False) + ']\n').encode())
        self.unrelated = self.put('unselected.typ', b'\xff unrelated #read("never")\n')
        self.catalog = self.base / 'owned catalog.json'
        self.catalog.write_bytes(CATALOG.read_bytes())
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()
        self.compiler = self.base / 'owned compiler'
        self.font = self.base / 'owned font.ttf'
        self.font.write_bytes(FONT_BYTES)
        self.font.chmod(0o600)
        self.program()

    def put(self, name, raw):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def program(self, behavior='', *, dependency_paths=None):
        paths = tuple(self.sources) if dependency_paths is None else tuple(dependency_paths)
        raw = (f'#!{sys.executable}\n'
            'import json, os, pathlib, signal, sys, time\n'
            'if sys.argv[1:] == ["--version"]:\n'
            f'    os.write(1, {VERSION!r}); raise SystemExit(0)\n'
            'assert sys.argv[1] == "compile"\n'
            'source = pathlib.Path(sys.argv[-2])\n'
            'output = pathlib.Path(sys.argv[-1])\n'
            'deps = pathlib.Path(sys.argv[sys.argv.index("--deps") + 1])\n'
            f'{behavior}\n'
            f'output.write_bytes({pdf_bytes()!r})\n'
            f'deps.write_bytes({b"".join(p.encode("utf-8") + bytes([0]) for p in paths)!r})\n').encode()
        if self.compiler.exists():
            self.compiler.chmod(0o700)
        self.compiler.write_bytes(raw)
        self.compiler.chmod(0o500)
        self.reviewed = runtime._ReviewedNativeArtifact(self.compiler, runtime._hash(raw), VERSION)
        return self.reviewed

    @contextmanager
    def context(self, *, committed=True, **kwargs):
        with mock.patch.object(inputs, 'CATALOG', self.catalog):
            with bridge.ProtectedCapturedRelativeFix(self.root, self.target,
                    journal_directory=self.store, lease=self.lease, **kwargs) as fix:
                if committed:
                    fix.apply(fix.validate())
                yield fix

    @contextmanager
    def resources(self, *, korean=False):
        with mock.patch.object(runtime, '_reviewed_native_artifact', return_value=self.reviewed):
            if not korean:
                yield self.reviewed
            else:
                font = runtime._ReviewedFontArtifact(self.font, runtime._hash(FONT_BYTES),
                    len(FONT_BYTES), 0, replace(FONT_IDENTITY))
                resource = runtime._ReviewedResourceArtifact(self.reviewed.sha256, (font,))
                with mock.patch.object(runtime, '_reviewed_resource_artifact', return_value=resource):
                    yield self.reviewed
