"""Real parser/backend/storage integration for the INTERNAL closed core bridge.

Every write is to an isolated temporary fixture, including catalog copies, git
indexes, journals, output sentinels and crash-worker files. Exhaustive closed
grammar proofs do not promote the general 33-rule implementation or CLI gate.
"""
from contextlib import ExitStack
from dataclasses import fields, replace
import base64
import io
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import protected_fix
from protected_fix import (CORE_RULE_IDS, ProtectedCoreError, ProtectedCoreFix,
                           REVIEWED_CATALOG_SHA256)
import script_rule
from syntax_client import DEBUG_EXECUTABLE, RELEASE_EXECUTABLE, EXECUTABLE, parse
import transactions
from transactions import (GraphSnapshot, OfflineExclusive,
                          ProtectedTransaction, RecoveryRequired,
                          TransactionError, TransactionFailure,
                          ValidatedCandidate)
import typst_lint as lint

WORKER = Path(__file__).with_name('protected_fix_process_worker.py')
CONFIG = b'rule-set = "core"\ninclude = ["main.typ"]\n'
ORIGINAL = (b'\xef\xbb\xbf' + '기존 사용자 prose $X^i + Y_j + Z_k^l$ 보존.\r\n'.encode('utf-8')
            + b'Plain unedited text.\r\n')
EXPECTED = ORIGINAL.replace(b'X^i', b'X^(i)').replace(b'Y_j', b'Y_(j)').replace(
    b'Z_k^l', b'Z_(k)^(l)')
XATTR = ('com.example.protected-core-integration' if sys.platform == 'darwin'
         else 'user.com.example.protected-core-integration')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX protected transactions')
class ProtectedFixIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-protected-integration-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.catalog_bytes = lint.CATALOG.read_bytes()
        self.assertEqual(lint.digest(self.catalog_bytes), REVIEWED_CATALOG_SHA256)
        self.counter = 0
        self.f = self.fixture()

    def fixture(self, *, source=ORIGINAL, config=CONFIG):
        self.counter += 1
        base = self.base / f'case-{self.counter}'
        root = base / 'consumer'
        root.mkdir(parents=True)
        store = base / 'journals'
        store.mkdir(mode=0o700)
        source_path = root / 'main.typ'
        source_path.write_bytes(source)
        source_path.chmod(0o640)
        config_path = root / '.typst-lint.toml'
        if config is not None:
            config_path.write_bytes(config)
        catalog = base / 'reviewed-catalog.json'
        catalog.write_bytes(self.catalog_bytes)
        # This would fail UTF-8/parser validation if singleton selection leaked.
        unrelated = root / 'unselected.typ'
        unrelated.write_bytes(b'\xff dirty unrelated bytes #read("never-open")\n')
        return SimpleNamespace(base=base, root=root, store=store, source=source_path,
                               config=config_path, catalog=catalog, unrelated=unrelated,
                               lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers())

    def context(self, fixture=None, **kwargs):
        f = fixture or self.f
        # Patch only the catalog LOCATION, never its bytes, digest or parser.
        stack = ExitStack()
        stack.enter_context(mock.patch.object(protected_fix, 'CATALOG', f.catalog))
        try:
            fix = stack.enter_context(ProtectedCoreFix(
                f.root, 'main.typ', journal_directory=f.store,
                lease=kwargs.pop('lease', f.lease), **kwargs))
        except BaseException:
            stack.close()
            raise
        return _EnteredFix(stack, fix)

    def apply(self, fixture=None, **kwargs):
        with self.context(fixture, **kwargs) as fix:
            candidate = fix.validate()
            return fix.apply(candidate)

    def recover(self, journal, fixture=None, **kwargs):
        f = fixture or self.f
        with ProtectedTransaction(f.root, ('main.typ',), journal_directory=f.store,
                                  lease=f.lease, recovery_only=True) as tx:
            return tx.recover(journal, **kwargs)

    def state(self, path):
        value = path.stat()
        return (path.read_bytes(), value.st_dev, value.st_ino,
                stat.S_IMODE(value.st_mode), value.st_uid, value.st_gid)

    def xattrs(self, path):
        fd = os.open(path, os.O_RDONLY)
        try:
            return transactions._xattrs(fd)
        finally:
            os.close(fd)

    def same_byte_replace(self, path):
        before = path.stat().st_ino
        replacement = path.with_name(path.name + '.user-save')
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(replacement, path)
        self.assertNotEqual(path.stat().st_ino, before)

    def assert_no_manifest(self, f=None):
        self.assertEqual(list((f or self.f).store.glob('root-*/*/manifest.json')), [])

    def test_real_parser_script_backend_durable_atomic_replace_and_zero_edit_repeat(self):
        f = self.f
        unchanged = self.state(f.unrelated)
        evidence = self.state(f.config), self.state(f.catalog)
        boundaries = []
        real_replace = os.replace
        with mock.patch.object(protected_fix, 'parse', wraps=protected_fix.parse) as parser, \
                mock.patch.object(protected_fix, 'analyze_script', wraps=protected_fix.analyze_script) as backend, \
                mock.patch.object(transactions.os, 'replace', wraps=real_replace) as rename:
            with self.context(fault=lambda point, path: boundaries.append((point, path))) as fix:
                candidate = fix.validate()
                self.assertEqual(candidate.passes, 1)
                self.assertEqual(candidate.sources, {'main.typ': EXPECTED})
                self.assertEqual(candidate.findings, frozenset())
                self.assertEqual(f.source.read_bytes(), ORIGINAL)
                self.assert_no_manifest()
                result = fix.apply(candidate)
        self.assertGreaterEqual(parser.call_count, 3)
        self.assertGreaterEqual(backend.call_count, 3)
        self.assertEqual(rename.call_count, 1)
        self.assertEqual(rename.call_args.args[:2], ('stage-0.bin', 'main.typ'))
        self.assertEqual(result.modified, ('main.typ',))
        self.assertFalse(result.unresolved_findings)
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.state(f.unrelated), unchanged)
        self.assertEqual((self.state(f.config), self.state(f.catalog)), evidence)
        journal = next(f.store.glob(f'root-*/{result.journal}'))
        manifest = json.loads((journal / 'manifest.json').read_bytes())
        self.assertEqual(len(manifest['files']), 1)
        self.assertEqual(base64.b64decode(manifest['files'][0]['before']), ORIGINAL)
        self.assertEqual(base64.b64decode(manifest['files'][0]['after']), EXPECTED)
        retained = {row['path']: base64.b64decode(row['bytes']) for row in manifest['evidence']}
        self.assertEqual(retained, {str(f.config): CONFIG, str(f.catalog): self.catalog_bytes})
        events = [json.loads(path.read_bytes())['kind'] for path in sorted(journal.glob('event-*.json'))]
        self.assertEqual(events, ['staged', 'replace_intent', 'applied', 'committed'])
        for point in ('journal_parent_fsync', 'journal_fsync', 'journal_directory_fsync',
                      'stage_fsync', 'source_directory_fsync'):
            self.assertIn(point, {point for point, _ in boundaries})
        state = self.state(f.source)
        with self.context() as fix:
            candidate = fix.validate()
            self.assertEqual(candidate.passes, 0)
            repeated = fix.apply(candidate)
        self.assertEqual(repeated.modified, ())
        self.assertIsNone(repeated.journal)
        self.assertEqual(self.state(f.source), state)
        self.assertEqual(len(list(f.store.glob('root-*/*/manifest.json'))), 1)

    @unittest.skipUnless(shutil.which('git'), 'git required for isolated index preservation check')
    def test_real_fix_preserves_dirty_bytes_git_index_and_supported_metadata(self):
        f = self.f
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        for args in (['init', '-q'], ['add', '--', 'main.typ', '.typst-lint.toml']):
            result = subprocess.run(['git', *args], cwd=f.root, capture_output=True, env=env, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        index = f.root / '.git/index'
        index_before = self.state(index)
        # Unstaged changes are part of the captured baseline, not discarded.
        dirty = ORIGINAL + '추가한 dirty 문장.\r\n'.encode('utf-8')
        f.source.write_bytes(dirty)
        fd = os.open(f.source, os.O_RDONLY)
        try:
            attrs = transactions._xattrs(fd)
            transactions._copy_xattrs(fd, tuple(sorted((*attrs, (XATTR, b'keep-fixture-metadata')))))
        finally:
            os.close(fd)
        before = f.source.stat()
        metadata = self.xattrs(f.source)
        self.apply()
        after = f.source.stat()
        self.assertEqual(f.source.read_bytes(), EXPECTED + dirty[len(ORIGINAL):])
        self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                         (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)))
        self.assertEqual(self.xattrs(f.source), metadata)
        self.assertEqual(self.state(index), index_before)

    def test_analyzer_uses_only_retained_bytes_and_explicit_frozen_catalog(self):
        real_validate = ProtectedTransaction.validate
        real_replay = ProtectedCoreFix._independent_replay
        real_io_open = io.open
        real_stat, real_lstat, real_access = os.stat, os.lstat, os.access
        real_os_open, real_read, real_pipe = os.open, os.read, os.pipe
        real_popen = subprocess.Popen
        calls = []
        original_backend = protected_fix.analyze_script
        # Both fixed profile directories are checked for selection drift. Only
        # the selected executable and its exact directory chain may be opened.
        trusted_metadata = frozenset((EXECUTABLE, *EXECUTABLE.parents,
                                      DEBUG_EXECUTABLE.parent, RELEASE_EXECUTABLE.parent))
        trusted_directories = frozenset((TOOL, TOOL / 'syntax-adapter',
                                         TOOL / 'syntax-adapter/target', EXECUTABLE.parent))
        binary_flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
        directory_flags = binary_flags | getattr(os, 'O_DIRECTORY', 0)
        executable_stat = real_stat(EXECUTABLE, follow_symlinks=False)
        executable_identity = (executable_stat.st_dev, executable_stat.st_ino)
        trusted_pipes = {}
        creating_parser = False

        def trusted_pipe(fd):
            if type(fd) is not int or fd not in trusted_pipes:
                return False
            info = os.fstat(fd)
            return stat.S_ISFIFO(info.st_mode) and (info.st_dev, info.st_ino) == trusted_pipes[fd]

        def parser_pipe():
            if not creating_parser:
                raise AssertionError('analyzer pipe outside parser subprocess')
            ends = real_pipe()
            for fd in ends:
                info = os.fstat(fd)
                if not stat.S_ISFIFO(info.st_mode):
                    os.close(ends[0])
                    os.close(ends[1])
                    raise AssertionError('parser subprocess pipe is not a FIFO')
                trusted_pipes[fd] = (info.st_dev, info.st_ino)
            return ends

        def parser_popen(argv, *args, **kwargs):
            nonlocal creating_parser
            if (creating_parser or type(argv) is not list or len(argv) != 2
                    or argv[0] != str(EXECUTABLE) or argv[1] not in ('markup', 'code', 'math')
                    or args or set(kwargs) != {'stdin', 'stdout', 'stderr'}
                    or kwargs.get('stdin') != subprocess.PIPE
                    or kwargs.get('stdout') != subprocess.PIPE or kwargs.get('stderr') != subprocess.PIPE):
                raise AssertionError('analyzer subprocess outside trusted parser')
            creating_parser = True
            try:
                return real_popen(argv, *args, **kwargs)
            finally:
                creating_parser = False

        def pipe_open(file, *args, **kwargs):
            # subprocess wraps its own already-open anonymous pipe descriptors.
            # Allowing arbitrary integer descriptors would hide graph reads.
            if trusted_pipe(file):
                return real_io_open(file, *args, **kwargs)
            raise AssertionError(f'analyzer byte read outside parser pipes: {file!r}')

        def metadata(operation):
            def checked(path, *args, **kwargs):
                # The debug fallback must probe the exact absent release
                # candidate without following links on every selection check.
                # This permits neither following it nor opening its bytes.
                release_probe = (type(path) is not int and operation is real_stat
                                 and EXECUTABLE != RELEASE_EXECUTABLE
                                 and Path(os.fsdecode(path)) == RELEASE_EXECUTABLE
                                 and not args and kwargs == {'follow_symlinks': False})
                if (type(path) is int or (Path(os.fsdecode(path)) not in trusted_metadata
                                          and not release_probe)):
                    raise AssertionError(f'analyzer metadata read outside trusted parser: {path!r}')
                return operation(path, *args, **kwargs)
            return checked

        def parser_open(path, flags, *args, **kwargs):
            if type(path) is int or args or kwargs:
                raise AssertionError(f'analyzer open outside trusted parser: {path!r}')
            selected = Path(os.fsdecode(path))
            if not ((selected == EXECUTABLE and flags == binary_flags)
                    or (selected in trusted_directories and flags == directory_flags)):
                raise AssertionError(f'analyzer open outside trusted parser: {path!r}')
            return real_os_open(path, flags)

        def parser_read(fd, count):
            info = os.fstat(fd)
            if (not trusted_pipe(fd)
                    and not (stat.S_ISREG(info.st_mode)
                             and (info.st_dev, info.st_ino) == executable_identity)):
                raise AssertionError('analyzer read outside parser pipes or selected binary')
            return real_read(fd, count)

        def read_tripwire():
            trusted_pipes.clear()
            stack = ExitStack()
            stack.enter_context(mock.patch('builtins.open', side_effect=pipe_open))
            stack.enter_context(mock.patch.object(io, 'open', side_effect=pipe_open))
            stack.enter_context(mock.patch.object(os, 'open', side_effect=parser_open))
            stack.enter_context(mock.patch.object(os, 'read', side_effect=parser_read))
            stack.enter_context(mock.patch.object(os, 'pipe', side_effect=parser_pipe))
            stack.enter_context(mock.patch.object(subprocess, 'Popen', side_effect=parser_popen))
            stack.enter_context(mock.patch.object(os, 'stat', side_effect=metadata(real_stat)))
            stack.enter_context(mock.patch.object(os, 'lstat', side_effect=metadata(real_lstat)))
            stack.enter_context(mock.patch.object(os, 'access', side_effect=metadata(real_access)))
            stack.enter_context(mock.patch.object(script_rule, 'load_rules',
                                                 side_effect=AssertionError('script catalog reread')))
            return stack

        unrelated_fd = real_os_open(self.f.unrelated, os.O_RDONLY)
        unrelated_pipe = real_pipe()
        try:
            with read_tripwire():
                # A sibling in a trusted directory is not itself trusted.
                for path in (self.f.source, self.f.config, self.f.catalog, self.f.unrelated,
                             EXECUTABLE.parent / 'not-the-adapter',
                             DEBUG_EXECUTABLE if EXECUTABLE == RELEASE_EXECUTABLE else RELEASE_EXECUTABLE):
                    with self.subTest(refused_path=path), self.assertRaises(AssertionError):
                        os.open(path, binary_flags)
                    with self.subTest(refused_metadata=path), self.assertRaises(AssertionError):
                        os.stat(path)
                with self.assertRaises(AssertionError):
                    os.open(EXECUTABLE, os.O_WRONLY)
                with self.assertRaises(AssertionError):
                    os.open(EXECUTABLE, binary_flags | os.O_CREAT)
                if getattr(os, 'O_NOFOLLOW', 0):
                    with self.assertRaises(AssertionError):
                        os.open(EXECUTABLE, binary_flags & ~os.O_NOFOLLOW)
                if getattr(os, 'O_DIRECTORY', 0):
                    with self.assertRaises(AssertionError):
                        os.open(EXECUTABLE.parent, binary_flags)
                if EXECUTABLE != RELEASE_EXECUTABLE:
                    # Only the non-following existence probe is needed for
                    # debug fallback; the test still forbids opening it.
                    with self.assertRaises(FileNotFoundError):
                        os.stat(RELEASE_EXECUTABLE, follow_symlinks=False)
                with self.assertRaises(AssertionError):
                    os.read(unrelated_fd, 1)
                with self.assertRaises(AssertionError):
                    os.read(unrelated_pipe[0], 0)
                with self.assertRaises(AssertionError):
                    io.open(unrelated_pipe[0], 'rb', closefd=False)
                with self.assertRaises(AssertionError):
                    os.pipe()
                with self.assertRaises(AssertionError):
                    subprocess.Popen([sys.executable, '-c', 'pass'])
                with self.assertRaises(AssertionError):
                    subprocess.Popen([str(EXECUTABLE), 'markup'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        finally:
            os.close(unrelated_fd)
            os.close(unrelated_pipe[0])
            os.close(unrelated_pipe[1])

        def inspect_backend(*args, **kwargs):
            catalog = kwargs.get('catalog')
            self.assertIsInstance(catalog, tuple)
            self.assertTrue(catalog)
            with self.assertRaises(TypeError):
                catalog[0]['grade'] = 'forged'
            calls.append(catalog)
            return original_backend(*args, **kwargs)

        def guarded_validate(tx, analyze, **kwargs):
            def guard(graph):
                # Revalidation is intentionally outside this guard. The parser
                # may inspect its trusted executable and communicate over pipes,
                # but no analyzer may reopen graph/config/catalog/dependencies.
                with read_tripwire():
                    return analyze(graph)
            return real_validate(tx, guard, **kwargs)

        def guarded_replay(fix, snapshot):
            with read_tripwire():
                return real_replay(fix, snapshot)

        with mock.patch.object(ProtectedTransaction, 'validate', new=guarded_validate), \
                mock.patch.object(ProtectedCoreFix, '_independent_replay', new=guarded_replay), \
                mock.patch.object(protected_fix, 'analyze_script', side_effect=inspect_backend):
            self.apply()
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)

    def test_caller_must_supply_genuine_offline_exclusive_assertion(self):
        for lease in (None, True, object.__new__(OfflineExclusive)):
            with self.subTest(lease=type(lease).__name__):
                f = self.fixture()
                with self.assertRaises(TransactionError):
                    with self.context(f, lease=lease):
                        pass
                self.assertEqual(f.source.read_bytes(), ORIGINAL)
                self.assertEqual(list(f.store.iterdir()), [])

    def test_closed_language_rejects_unsupported_descendants_before_any_write(self):
        samples = (
            b'#import "never-read.typ": *\n$X^i$\n',
            b'#include "never-read.typ"\n$X^i$\n',
            b'#let x = 1\n$X^i$\n',
            b'#read("never-read.txt")\n$X^i$\n',
            b'#image("never-read.png")\n$X^i$\n',
            b'#box[$X^i$]\n',
            b'*Nested $X^i$*\n',
            b'// comment\n$X^i$\n',
            b'/* comment */ $X^i$\n',
            b'// typst-lint: ignore-next core.parenthesized-scripts -- example\n$X^i$\n',
            b'$X^/* comment */i$\n',
            b'$X^"text"$\n',
            b'$sqrt(x)^i$\n',
            b'$X_(sqrt(i))$\n',
            b'$X^#(1 + 1)$\n',
            b'$X^i$ <label>\n',
            b'@reference $X^i$\n',
            b'`$X^i$`\n',
            b'= Heading $X^i$\n',
            b'$X^$\n',
        )
        for source in samples:
            with self.subTest(source=source):
                f = self.fixture(source=source)
                dependency = f.root / 'never-read.typ'
                dependency.write_bytes(b'\xff must never be opened')
                before = self.state(f.source), self.state(dependency)
                with self.assertRaises(lint.SafetyError):
                    self.apply(f)
                self.assertEqual((self.state(f.source), self.state(dependency)), before)
                self.assert_no_manifest(f)

    def test_nested_real_script_operands_and_identifiers_fix_in_exactly_one_pass(self):
        source = b'Preserved prose. $X^(i^j) + alpha^beta$\n'
        expected = b'Preserved prose. $X^(i^(j)) + alpha^(beta)$\n'
        f = self.fixture(source=source)
        with self.context(f) as fix:
            candidate = fix.validate()
            self.assertEqual(candidate.passes, 1)
            self.assertEqual(candidate.sources, {'main.typ': expected})
            self.assertFalse(candidate.findings)
            fix.apply(candidate)
        self.assertEqual(f.source.read_bytes(), expected)
        with self.context(f) as fix:
            candidate = fix.validate()
            self.assertEqual(candidate.passes, 0)
            self.assertEqual(fix.apply(candidate).modified, ())

    def test_forged_wrong_source_wrong_path_and_stale_candidate_proofs_are_rejected(self):
        proof = protected_fix.certify(parse(ORIGINAL), 'main.typ')
        forged = object.__new__(type(proof))
        for field in fields(proof):
            object.__setattr__(forged, field.name, getattr(proof, field.name))
        object.__setattr__(forged, 'not_applicable_rule_ids', ())
        variants = (
            object.__new__(type(proof)),
            forged,
            protected_fix.certify(parse(b'$Q^z$\n'), 'main.typ'),
            protected_fix.certify(parse(ORIGINAL), 'other.typ'),
            # Valid for the baseline, stale when the repaired candidate is parsed.
            proof,
        )
        for stale in variants:
            with self.subTest(proof=type(stale).__name__):
                f = self.fixture()
                with self.context(f) as fix, mock.patch.object(protected_fix, 'certify', return_value=stale):
                    with self.assertRaises(lint.SafetyError):
                        fix.validate()
                self.assertEqual(f.source.read_bytes(), ORIGINAL)
                self.assert_no_manifest(f)

    def test_explicit_exact_singleton_core_config_and_full_reviewed_catalog_required(self):
        configs = (
            None, b'', b'include=["main.typ"]\n',
            b'rule-set="pde"\ninclude=["main.typ"]\n',
            b'rule-set="core"\ninclude=["**/*.typ"]\n',
            b'rule-set="core"\ninclude=["main.typ", "unselected.typ"]\n',
            CONFIG + b'exclude=["unselected.typ"]\n',
            CONFIG + b'catalog="references.json"\n',
            CONFIG + b'entrypoint="unselected.typ"\n',
            CONFIG + b'[[ignore]]\npaths=["main.typ"]\nrules=["core.parenthesized-scripts"]\nreason="test"\n',
            CONFIG + b'[capitalization]\nlanguage="en"\n',
        )
        for config in configs:
            with self.subTest(config=config):
                f = self.fixture(config=config)
                before = self.state(f.source)
                with self.assertRaises((OSError, ProtectedCoreError, TransactionError)):
                    self.apply(f)
                self.assertEqual(self.state(f.source), before)
                self.assert_no_manifest(f)
        for mutate in ('subset', 'metadata', 'whitespace'):
            with self.subTest(catalog=mutate):
                f = self.fixture()
                parsed = json.loads(self.catalog_bytes)
                if mutate == 'subset':
                    parsed['rules'] = [row for row in parsed['rules'] if row['id'] == script_rule.RULE_ID]
                elif mutate == 'metadata':
                    parsed['rules'][0]['grade'] = 'warning'
                f.catalog.write_bytes(self.catalog_bytes + b'\n' if mutate == 'whitespace'
                                      else json.dumps(parsed).encode())
                before = self.state(f.source)
                with self.assertRaises((ProtectedCoreError, TransactionError)):
                    self.apply(f)
                self.assertEqual(self.state(f.source), before)
                self.assert_no_manifest(f)

    def test_same_byte_inode_races_during_locked_capture_refuse_all_writes(self):
        real_read = transactions._read
        for field in ('source', 'config', 'catalog'):
            with self.subTest(field=field):
                f = self.fixture()
                target = getattr(f, field)
                fired = False

                def race(path, **kwargs):
                    nonlocal fired
                    value = real_read(path, **kwargs)
                    if Path(path) == target and not fired:
                        fired = True
                        self.same_byte_replace(target)
                    return value

                with mock.patch.object(transactions, '_read', side_effect=race):
                    with self.assertRaises(TransactionError):
                        self.apply(f)
                self.assertTrue(fired)
                self.assertEqual(f.source.read_bytes(), ORIGINAL)
                self.assert_no_manifest(f)

    def test_same_byte_inode_races_during_real_validation_refuse_candidate(self):
        real_parse = protected_fix.parse
        for field in ('source', 'config', 'catalog'):
            with self.subTest(field=field):
                f = self.fixture()
                fired = False

                def race(*args, **kwargs):
                    nonlocal fired
                    tree = real_parse(*args, **kwargs)
                    if not fired:
                        fired = True
                        self.same_byte_replace(getattr(f, field))
                    return tree

                with self.context(f) as fix, mock.patch.object(protected_fix, 'parse', side_effect=race):
                    with self.assertRaises(TransactionError):
                        fix.validate()
                self.assertTrue(fired)
                self.assertEqual(f.source.read_bytes(), ORIGINAL)
                self.assert_no_manifest(f)

    def test_source_config_catalog_bytes_or_identity_changed_after_validation_are_stale(self):
        for field in ('source', 'config', 'catalog'):
            for same_bytes in (False, True):
                with self.subTest(field=field, same_bytes=same_bytes):
                    f = self.fixture()
                    target = getattr(f, field)
                    with self.context(f) as fix:
                        candidate = fix.validate()
                        if same_bytes:
                            self.same_byte_replace(target)
                        else:
                            target.write_bytes(target.read_bytes() + b'\nLater user work.\n')
                        latest = self.state(target)
                        with self.assertRaises(TransactionError):
                            fix.apply(candidate)
                    self.assertEqual(self.state(target), latest)
                    if field != 'source':
                        self.assertEqual(f.source.read_bytes(), ORIGINAL)
                    self.assert_no_manifest(f)

    def test_staging_and_prerename_revalidate_real_source_config_catalog(self):
        for point in ('before_revalidate', 'before_replace'):
            for field in ('source', 'config', 'catalog'):
                for same_bytes in (False, True):
                    with self.subTest(point=point, field=field, same_bytes=same_bytes):
                        f = self.fixture()
                        fired = False

                        def race(where, path):
                            nonlocal fired
                            if where == point and not fired:
                                fired = True
                                target = getattr(f, field)
                                if same_bytes:
                                    self.same_byte_replace(target)
                                else:
                                    target.write_bytes(target.read_bytes() + b'\nLater user work.\n')

                        with self.assertRaises(TransactionFailure) as caught:
                            self.apply(f, fault=race)
                        self.assertTrue(fired)
                        self.assertEqual(caught.exception.report.restored, ())
                        self.assertNotEqual(f.source.read_bytes(), EXPECTED)
                        latest = self.state(getattr(f, field))
                        report = self.recover(caught.exception.report.journal, f)
                        if field == 'source':
                            self.assertFalse(report.complete)
                            self.assertEqual(report.changed, ('main.typ',))
                            report = self.recover(report.journal, f, preserve_conflicts=report.conflicts)
                        self.assertTrue(report.complete, report)
                        self.assertEqual(self.state(getattr(f, field)), latest)
                        if field != 'source' or same_bytes:
                            self.assertEqual(f.source.read_bytes(), ORIGINAL)

    def test_graph_extra_evidence_and_rule_subset_cannot_claim_complete_core(self):
        with self.context() as fix:
            graph = fix.transaction.snapshot
            self.assertEqual(len(CORE_RULE_IDS), 15)
            actual = fix._analyze_graph(graph)
            self.assertEqual(actual.implemented_rules, CORE_RULE_IDS)
            self.assertEqual(actual.evaluated_rules, CORE_RULE_IDS)
            self.assertEqual(actual.parsed_sources, frozenset({'main.typ'}))
            self.assertEqual(actual.examined_evidence, frozenset({str(self.f.config), str(self.f.catalog)}))
            extra = GraphSnapshot(graph.root, graph.sources,
                                  MappingProxyType(dict(graph.evidence, **{str(self.f.base / 'extra'): b'bad'})))
            with self.assertRaises((ProtectedCoreError, TransactionError)):
                fix._analyze_graph(extra)
            with mock.patch.object(fix, '_analyze_graph', return_value=replace(
                    actual, evaluated_rules=frozenset({script_rule.RULE_ID}))):
                with self.assertRaises(TransactionError):
                    fix.validate()
        self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
        self.assert_no_manifest()

    def test_postreplacement_final_revalidation_preserves_any_later_source_or_evidence(self):
        for field in ('source', 'config', 'catalog'):
            for same_bytes in (False, True):
                with self.subTest(field=field, same_bytes=same_bytes):
                    f = self.fixture()
                    latest = None

                    def race(point, path):
                        nonlocal latest
                        if point == 'before_final_validation':
                            target = getattr(f, field)
                            if same_bytes:
                                self.same_byte_replace(target)
                            else:
                                target.write_bytes(target.read_bytes() + b'Later user work.\n')
                            latest = self.state(target)

                    with self.assertRaises(TransactionFailure) as caught:
                        self.apply(f, fault=race)
                    self.assertIsNotNone(latest)
                    self.assertEqual(self.state(getattr(f, field)), latest)
                    report = self.recover(caught.exception.report.journal, f)
                    if field == 'source':
                        self.assertFalse(report.complete)
                        self.assertEqual(report.changed, ('main.typ',))
                        report = self.recover(report.journal, f, preserve_conflicts=report.conflicts)
                    else:
                        self.assertEqual(f.source.read_bytes(), ORIGINAL)
                    self.assertTrue(report.complete, report)
                    self.assertEqual(self.state(getattr(f, field)), latest)

    def test_foreign_session_unissued_and_forged_candidates_never_write(self):
        with self.context() as first:
            old = first.validate()
        with self.context() as second:
            fresh = second.validate()
            for candidate in (old, True, object.__new__(ValidatedCandidate)):
                with self.subTest(type=type(candidate).__name__):
                    with self.assertRaises((ProtectedCoreError, TransactionError)):
                        second.apply(candidate)
            self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
            second.apply(fresh)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)

    def test_error_after_real_replacement_rolls_back_and_requires_explicit_recovery(self):
        reached = False

        def fail(point, path):
            nonlocal reached
            if (point, path) == ('after_replace', 'main.typ'):
                reached = True
                self.assertEqual(self.f.source.read_bytes(), EXPECTED)
                raise OSError('test-owned failure after real replacement')

        with self.assertRaises(TransactionFailure) as caught:
            self.apply(fault=fail)
        self.assertTrue(reached)
        self.assertEqual(caught.exception.report.restored, ('main.typ',))
        self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
        with self.assertRaises(RecoveryRequired):
            self.apply()
        self.assertTrue(self.recover(caught.exception.report.journal).complete)
        self.assertEqual(self.apply().modified, ('main.typ',))

    def test_general_cli_modes_stay_partial_read_only_and_never_compile(self):
        f = self.f
        output = f.root / 'existing.pdf'
        output.write_bytes(b'KEEP EXISTING OUTPUT')
        compiler = f.root / 'compiler-tripwire'
        compiler.write_bytes(b'#!/bin/sh\nprintf invoked > "$0.ran"\n')
        compiler.chmod(0o755)
        before = self.state(f.source), self.state(f.config), self.state(f.unrelated), self.state(output)
        for mode in ('check', 'dry-run', 'fix', 'compile'):
            with self.subTest(mode=mode):
                command = [sys.executable, str(TOOL / ('typst-style' if mode == 'compile' else 'typst-lint')),
                           'fix' if mode == 'dry-run' else mode,
                           '--root', str(f.root), '--format', 'json']
                if mode == 'dry-run':
                    command.append('--dry-run')
                if mode == 'compile':
                    command += ['--', str(compiler), 'main.typ', 'existing.pdf']
                result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stderr, '')
                report = json.loads(result.stdout)
                self.assertFalse(report['coverage']['complete'])
                self.assertEqual(len(report['coverage']['enabled']), 15)
                self.assertFalse(report['applied_fixes'] or report['compiler_invoked'])
                self.assertEqual(report['proposal_status'], 'partial-not-applicable-transaction')
                self.assertTrue(report['proposed_edits'])
                self.assertEqual((self.state(f.source), self.state(f.config), self.state(f.unrelated),
                                  self.state(output)), before)
                self.assertFalse(Path(str(compiler) + '.ran').exists())
                self.assertEqual(list(f.store.iterdir()), [])
        audit = json.loads((TOOL / 'audit/latest-evidence.json').read_bytes())
        self.assertEqual(len(audit['rules']), 33)
        self.assertTrue(all(row['status'] == 'PARTIAL' for row in audit['rules']))

    def request(self, action, **kwargs):
        f = self.f
        return dict(root=str(f.root), store=str(f.store), target='main.typ',
                    catalog=str(f.catalog), action=action, **kwargs)

    def worker(self, action, **kwargs):
        result = subprocess.run([sys.executable, str(WORKER), json.dumps(self.request(action, **kwargs))],
                                capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def kill_worker(self, action, boundary, **kwargs):
        child = subprocess.Popen(
            [sys.executable, str(WORKER), json.dumps(self.request(action, boundary=boundary, **kwargs))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=30), 'Real-analysis worker did not reach crash boundary')
                line = child.stdout.readline()
            if not line:
                _, error = child.communicate(timeout=5)
                self.fail(f'Real-analysis worker exited before boundary: {error}')
            self.assertEqual(json.loads(line), {'paused': list(boundary)})
            child.kill()  # Exact child owned by this test. No Python unwinding.
            _, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGKILL, error)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def pending(self):
        report = self.worker('probe')
        self.assertEqual(set(report), {'pending'}, report)
        self.assertEqual(len(report['pending']), 1)
        return report['pending'][0]

    def test_sigkill_before_replace_then_restart_recovers_and_real_fix_remains_idempotent(self):
        unrelated = self.state(self.f.unrelated)
        self.kill_worker('apply', ('before_replace', 'main.typ'))
        self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
        journal = self.pending()
        recovery = self.worker('recover', journal=journal)
        self.assertTrue(recovery['complete'], recovery)
        self.assertEqual(recovery['unchanged'], ['main.typ'])
        applied = self.worker('apply')
        self.assertEqual(applied['modified'], ['main.typ'])
        self.assertEqual(applied['passes'], 1)
        self.assertEqual(applied['findings'], [])
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        fixed = self.state(self.f.source)
        repeated = self.worker('apply')
        self.assertEqual(repeated['modified'], [])
        self.assertEqual(repeated['passes'], 0)
        self.assertEqual(self.state(self.f.source), fixed)
        self.assertEqual(self.state(self.f.unrelated), unrelated)

    def test_sigkill_after_real_fix_and_during_restore_recovers_in_fresh_interpreters(self):
        self.kill_worker('apply', ('after_replace', 'main.typ'))
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        journal = self.pending()
        self.kill_worker('recover', ('after_restore', 'main.typ'), journal=journal)
        self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
        restored = self.state(self.f.source)
        self.assertEqual(self.pending(), journal)
        report = self.worker('recover', journal=journal)
        self.assertTrue(report['complete'], report)
        self.assertEqual(report['unchanged'], ['main.typ'])
        self.assertEqual(self.state(self.f.source), restored)
        self.assertEqual(self.worker('probe'), {'open': True})

    def test_sigkill_recovery_preserves_later_user_edits_and_stale_conflict_resolution_fails(self):
        self.kill_worker('apply', ('after_replace', 'main.typ'))
        journal = self.pending()
        self.f.source.write_bytes(b'Later user work, not a lint postimage.\n')
        saved = self.state(self.f.source)
        report = self.worker('recover', journal=journal)
        self.assertFalse(report['complete'])
        self.assertEqual(report['changed'], ['main.typ'])
        self.assertEqual(self.state(self.f.source), saved)
        self.f.source.write_bytes(b'Even newer user work.\n')
        latest = self.state(self.f.source)
        stale = self.worker('recover', journal=journal, preserve=report['conflicts'])
        self.assertFalse(stale['complete'])
        self.assertEqual(self.state(self.f.source), latest)
        self.assertEqual(self.pending(), journal)
        final = self.worker('recover', journal=journal, preserve=stale['conflicts'])
        self.assertTrue(final['complete'], final)
        self.assertEqual(final['preserved'], ['main.typ'])
        self.assertEqual(self.state(self.f.source), latest)
        self.assertEqual(self.worker('probe'), {'open': True})

    def test_sigkill_same_postimage_new_inode_is_preserved_until_exact_conflict_ack(self):
        self.kill_worker('apply', ('after_replace', 'main.typ'))
        journal = self.pending()
        self.same_byte_replace(self.f.source)
        saved = self.state(self.f.source)
        report = self.worker('recover', journal=journal)
        self.assertFalse(report['complete'])
        self.assertEqual(report['changed'], ['main.typ'])
        self.assertEqual(self.state(self.f.source), saved)
        final = self.worker('recover', journal=journal, preserve=report['conflicts'])
        self.assertTrue(final['complete'], final)
        self.assertEqual(self.state(self.f.source), saved)


class _EnteredFix:
    """Keep catalog location patched for exactly the protected context lifetime."""
    def __init__(self, stack, fix):
        self.stack, self.fix = stack, fix

    def __enter__(self):
        return self.fix

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


if __name__ == '__main__':
    unittest.main()
