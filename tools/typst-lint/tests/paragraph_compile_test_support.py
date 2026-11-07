"""Owned paragraph compile fixtures with an explicit simulated runtime seam.

The fake runner performs no native/process execution. Its invocations are
recorded in the real private attempt ledger; successful output bytes are only
fixture bytes, not evidence that Typst ran. Source admission uses the real parser.
"""
from contextlib import contextmanager
from dataclasses import fields
from pathlib import Path
from unittest import mock

import closed_core_compile as runtime
from paragraph_fix_test_support import fixture, context

VERSION = b'owned paragraph fake runtime 1\n'
OLD_OUTPUT = b'Owned preexisting paragraph output.\x00\xff\r\n'


def pdf_bytes():
    start = b'%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n'
    return (start + b'xref\n0 1\n0000000000 65535 f \ntrailer\n<<>>\nstartxref\n'
            + str(len(start)).encode() + b'\n%%EOF\n')


def clone_opaque(value):
    clone = object.__new__(type(value))
    for item in fields(value):
        object.__setattr__(clone, item.name, getattr(value, item.name))
    return clone


class ParagraphFixture:
    def __init__(self, base, sequence, *, present=True, **kwargs):
        self.__dict__.update(fixture(Path(base).resolve(), sequence, **kwargs).__dict__)
        self.compiler = self.base / 'owned fake compiler'
        raw = b'Owned inert executable fixture. Never executed.\n'
        self.compiler.write_bytes(raw)
        self.compiler.chmod(0o500)
        self.reviewed = runtime._ReviewedNativeArtifact(self.compiler, runtime._hash(raw), VERSION)
        self.destination = 'build with spaces/nested/document file.pdf'
        self.output = self.root / self.destination
        self.output.parent.mkdir(parents=True)
        if present:
            self.output.write_bytes(OLD_OUTPUT)
            self.output.chmod(0o644)
        import pdf_publication_store as store
        self.output_lease = store.OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers()
        self.attempts = []
        self.before_run = self.after_run = None
        self.dependency_bytes = None

    def context(self, *, committed=True, **kwargs):
        @contextmanager
        def entered():
            with context(self, **kwargs) as fix:
                if committed:
                    fix.apply(fix.validate())
                yield fix
        return entered()

    def expected_dependencies(self, argv, cwd):
        packages = Path(argv[argv.index('--package-path') + 1])
        return (self.target, str(packages / 'local/text-utils/0.1.2/lib.typ'),
                str(packages / 'local/text-utils/0.1.2/typst.toml'))

    def run(self, argv, *, purpose, cwd, environment, deadline, cancel_event,
            check_workspace, _ledger=None):
        check_workspace()
        if self.before_run is not None:
            self.before_run(argv, purpose, Path(cwd))
        check_workspace()
        output, error = bytearray(), bytearray()
        attempt = _ledger.begin(purpose, argv, cwd, environment, output, error)
        attempt.started, attempt.lifecycle = True, 'running'
        self.attempts.append((purpose, tuple(argv), Path(cwd), dict(environment)))
        if purpose == 'version-probe':
            output.extend(VERSION)
        else:
            Path(argv[-1]).write_bytes(pdf_bytes())
            deps = Path(argv[argv.index('--deps') + 1])
            raw = self.dependency_bytes
            if callable(raw):
                raw = raw(argv, Path(cwd))
            if raw is None:
                raw = b''.join(path.encode() + b'\0' for path in self.expected_dependencies(argv, cwd))
            deps.write_bytes(raw)
        attempt.returncode, attempt.lifecycle = 0, 'exited'
        if self.after_run is not None:
            self.after_run(argv, purpose, Path(cwd), attempt)
        check_workspace()
        return attempt.snapshot()

    @contextmanager
    def resources(self):
        with mock.patch.object(runtime, '_reviewed_native_artifact', return_value=self.reviewed), \
                mock.patch.object(runtime, '_run', side_effect=self.run), \
                mock.patch.object(runtime, '_reviewed_resource_artifact',
                                  side_effect=AssertionError('ASCII touched font resources')):
            yield
