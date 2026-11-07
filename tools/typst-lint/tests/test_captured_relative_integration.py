"""Real parser/transaction adversaries for INTERNAL captured scalar inputs.

Every source, outside sentinel, config and catalog copy is test-owned. Capture
is not a repair/compile bridge, and these tests never run a Typst compiler.
"""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import copy
import ctypes
from dataclasses import FrozenInstanceError, fields, replace
import io
import json
import os
from pathlib import Path
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
import captured_relative_core as core
import captured_relative_inputs as inputs
import closed_core_compile
import protected_fix
import source_profiles
from syntax_client import EXECUTABLE, parse
import transactions
from transactions import OfflineExclusive, ReadLimits, TransactionError
import typst_lint as lint

CONFIG = b'rule-set = "core"\ninclude = ["main.typ"]\n'
ROOT = b'#import "values.typ": alpha\nValue #alpha and $alpha_1$.\n'
MODULE = b'#let alpha = 7\n'
XATTR = ('com.example.captured-relative-integration' if sys.platform == 'darwin'
         else 'user.com.example.captured-relative-integration')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build the pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX protected capture')
class CapturedRelativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='typst-relative-integration-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.catalog_bytes = lint.CATALOG.read_bytes()
        self.sequence = 0
        self.f = self.fixture()

    def fixture(self, *, source=ROOT, target='main.typ'):
        self.sequence += 1
        base = self.base / f'fixture-{self.sequence}'
        root = base / 'consumer'
        root.mkdir(parents=True)
        store = base / 'journals'
        store.mkdir(mode=0o700)
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
        path.chmod(0o640)
        config = root / '.typst-lint.toml'
        config.write_bytes(('rule-set = "core"\ninclude = [' + json.dumps(target) + ']\n').encode())
        catalog = base / 'catalog.json'
        catalog.write_bytes(self.catalog_bytes)
        self.put('values.typ', MODULE, root=root)
        return SimpleNamespace(base=base, root=root, store=store, source=path,
                               target=target, config=config, catalog=catalog,
                               lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers())

    def put(self, name, raw, *, fixture=None, root=None):
        target = (root or (fixture or self.f).root) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return target

    def context(self, fixture=None, **kwargs):
        f = fixture or self.f
        stack = ExitStack()
        stack.enter_context(mock.patch.object(inputs, 'CATALOG', f.catalog))
        options = dict(journal_directory=f.store, lease=f.lease)
        options.update(kwargs)
        try:
            session = stack.enter_context(inputs.CapturedRelativeInputSession(f.root, f.target, **options))
        except BaseException:
            stack.close()
            raise
        return _RelativeEnteredSession(stack, session)

    def state(self, path):
        value = path.stat()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = transactions._xattrs(fd)
        finally:
            os.close(fd)
        return (path.read_bytes(), value.st_dev, value.st_ino, stat.S_IMODE(value.st_mode),
                value.st_uid, value.st_gid, value.st_mtime_ns, value.st_ctime_ns, metadata)

    def no_manifest(self, fixture=None):
        self.assertFalse(list((fixture or self.f).store.glob('root-*/*/manifest.json')))

    def set_fixture_xattr(self, path, value):
        if sys.platform == 'darwin':
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                libc = transactions._darwin_metadata_api()
                ctypes.set_errno(0)
                if libc.fsetxattr(fd, os.fsencode(XATTR), value, len(value), 0, 0):
                    raise OSError(ctypes.get_errno(), 'Fixture extended attribute unavailable')
            finally:
                os.close(fd)
        else:
            os.setxattr(path, XATTR, value)

    def gap(self, mutate):
        actual = inputs._discover
        def discover(*args, **kwargs):
            proposed = actual(*args, **kwargs)
            mutate()
            return proposed
        return mock.patch.object(inputs, '_discover', side_effect=discover)

    def test_real_diamond_keeps_declaration_origins_and_named_alias_routes(self):
        f = self.f
        self.put('seed.typ', b'#let seed = 7\n#let equal = 7\n')
        self.put('left.typ', b'#import "seed.typ": seed as incoming\n#let left = incoming\n')
        self.put('right.typ', b'#import "seed.typ": seed as incoming, equal\n#let right = incoming\n')
        f.source.write_bytes(b'#import "left.typ": left\n#import "right.typ": right, equal\n'
                             b'= Heading #left\n*Value #right* and $equal_1$.\n')
        with self.context() as session:
            capture = session.capture
            self.assertEqual(set(capture.captures), {'main.typ', 'left.typ', 'right.typ', 'seed.typ'})
            bindings = {b.name: b for b in capture.proof.root_bindings}
            self.assertEqual(bindings['left'].value, bindings['right'].value)
            self.assertEqual(bindings['left'].origin, bindings['right'].origin)
            self.assertNotEqual(bindings['left'].provenance, bindings['right'].provenance)
            self.assertEqual(bindings['equal'].value, bindings['left'].value)
            self.assertNotEqual(bindings['equal'].origin, bindings['left'].origin)
            for binding in bindings.values():
                self.assertEqual(binding.origin.path, 'seed.typ')
                tree = capture.captures[binding.origin.path]
                start, end = binding.origin.initializer_span
                self.assertEqual(tree.source[start:end], b'7')
                self.assertEqual(binding.origin.source_sha256, lint.digest(tree.source))
            self.assertEqual([s.action for s in bindings['left'].provenance],
                             ['declaration', 'import', 'alias', 'import'])
            self.assertEqual(len(capture.proof.edges), 4)
            self.assertEqual({u.name for u in capture.proof.imported_uses}, {'left', 'right', 'equal'})
            self.assertEqual(len(capture.proof.evaluated_rule_ids + capture.proof.not_applicable_rule_ids), 15)
            session.assert_current(capture)
        self.no_manifest()

    def test_needed_leading_parent_path_and_unicode_bytes_are_retained(self):
        f = self.fixture(target='chapters/main.typ', source=(
            b'\xef\xbb\xbf#import "../values.typ": alpha\r\n'
            + '= 원문 #alpha\r\n- *Preserved $alpha_1$*\r\n'.encode()))
        unrelated = self.put('unselected.typ', b'\xff not selected #read("never")', fixture=f)
        before = self.state(f.source), self.state(f.root / 'values.typ'), self.state(unrelated)
        with self.context(f) as session:
            capture = session.capture
            self.assertEqual(capture.captures[f.target].source, before[0][0])
            self.assertEqual(capture.proof.edges[0].request, '../values.typ')
            self.assertEqual(capture.proof.edges[0].target, 'values.typ')
        self.assertEqual((self.state(f.source), self.state(f.root / 'values.typ'), self.state(unrelated)), before)

    def test_capture_is_read_only_and_only_root_is_a_prospective_write_target(self):
        f = self.f
        dependency = f.root / 'values.typ'
        dependency.chmod(0o444)  # Dependencies do not inherit source-write preconditions.
        paths = (f.source, dependency, f.config, f.catalog)
        before = tuple(self.state(p) for p in paths)
        with mock.patch.object(transactions.ProtectedTransaction, 'apply',
                               side_effect=AssertionError('capture must not apply')), \
                mock.patch.object(closed_core_compile, 'compile_committed_snapshot',
                                  side_effect=AssertionError('capture must not compile')):
            with self.context() as session:
                capture = session.capture
                self.assertEqual(session._tx.targets, ('main.typ',))
                graph = session._tx.snapshot
                self.assertEqual(set(graph.sources), {'main.typ'})
                self.assertEqual(set(graph.evidence), {str(f.config), str(f.catalog), str(dependency)})
                self.assertEqual(capture.dependency_evidence, {'values.typ': str(dependency)})
                self.assertEqual(graph.evidence[str(dependency)], MODULE)
                self.assertEqual(graph.evidence[str(f.config)], CONFIG)
                self.assertEqual(graph.evidence[str(f.catalog)], self.catalog_bytes)
                for mapping, key in ((graph.sources, 'main.typ'), (graph.evidence, str(dependency)),
                                     (capture.captures, 'values.typ'),
                                     (capture.dependency_evidence, 'values.typ')):
                    with self.assertRaises(TypeError):
                        mapping[key] = b'forged'
                with self.assertRaises(FrozenInstanceError):
                    capture.target = 'other.typ'
                self.assertFalse(hasattr(session, 'apply'))
                self.assertFalse(hasattr(session, 'compile'))
        self.assertEqual(tuple(self.state(p) for p in paths), before)
        self.no_manifest()

    def test_discovery_scalar_bytes_are_not_an_authoritative_baseline(self):
        dependency = self.f.root / 'values.typ'
        updated = b'#let alpha = "new scalar"\n'
        with self.gap(lambda: dependency.write_bytes(updated)):
            with self.context() as session:
                capture = session.capture
                self.assertEqual(capture.captures['values.typ'].source, updated)
                self.assertEqual(capture.proof.root_bindings[0].value, 'new scalar')
                self.assertEqual(capture.proof.root_bindings[0].origin.source_sha256, lint.digest(updated))
        self.assertEqual(dependency.read_bytes(), updated)
        self.no_manifest()

    def test_discovery_changed_closure_is_refused_without_reading_new_target(self):
        f = self.f
        other = self.put('never-authorized.typ', b'#let alpha = 9\n')
        actual_read = transactions._read
        def read(path, *args, **kwargs):
            self.assertNotEqual(path, other, 'new closure member must not be opportunistically read')
            return actual_read(path, *args, **kwargs)
        with self.gap(lambda: f.source.write_bytes(ROOT.replace(b'values.typ', b'never-authorized.typ'))), \
                mock.patch.object(transactions, '_read', side_effect=read), \
                mock.patch.object(inputs, '_read', side_effect=read):
            with self.assertRaises((inputs.CapturedRelativeCaptureError, TransactionError)):
                with self.context():
                    self.fail('Changed closure was accepted')
        self.assertEqual(other.read_bytes(), b'#let alpha = 9\n')
        self.no_manifest()

    def test_discovery_changed_internal_edge_with_same_file_set_is_refused(self):
        self.put('leaf.typ', b'#let omega = 7\n')
        self.put('values.typ', b'#import "leaf.typ": omega\n#let alpha = omega\n')
        self.f.source.write_bytes(b'#import "values.typ": alpha\n#import "leaf.typ": omega\n#alpha\n')
        changed = b'#let alpha = 7\n'  # Closure vertices stay the same; an edge disappears.
        with self.gap(lambda: (self.f.root / 'values.typ').write_bytes(changed)):
            with self.assertRaises(inputs.CapturedRelativeCaptureError):
                with self.context():
                    self.fail('Changed edge set was accepted')
        self.no_manifest()

    def test_missing_or_extra_proposed_dependencies_cannot_replace_exact_closure(self):
        extra = self.put('extra.typ', b'#let unused = 9\n')
        actual = inputs._discover
        for membership in ('missing', 'extra'):
            with self.subTest(membership=membership):
                def discover(*args, **kwargs):
                    proposed = actual(*args, **kwargs)
                    paths = (('main.typ',) if membership == 'missing'
                             else tuple(sorted((*proposed.paths, 'extra.typ'))))
                    return replace(proposed, paths=paths)
                with mock.patch.object(inputs, '_discover', side_effect=discover):
                    with self.assertRaises(inputs.CapturedRelativeCaptureError):
                        with self.context():
                            self.fail('Discovery path proposal replaced the proven closure')
                self.assertEqual(extra.read_bytes(), b'#let unused = 9\n')
                self.no_manifest()

    def test_unsupported_initializer_introduced_after_discovery_still_refuses(self):
        sentinel = self.f.base / 'outside-secret.txt'
        sentinel.write_bytes(b'owned outside sentinel')
        raw = MODULE + ('#let unused = read(' + json.dumps(str(sentinel)) + ')\n').encode()
        with self.gap(lambda: (self.f.root / 'values.typ').write_bytes(raw)):
            with self.assertRaises(inputs.CapturedRelativeInputError):
                with self.context():
                    self.fail('Discovery grammar was incorrectly reused')
        self.assertEqual(sentinel.read_bytes(), b'owned outside sentinel')
        self.no_manifest()

    def test_revalidation_never_parses_or_rediscovers_live_source_authority(self):
        with self.context() as session:
            capture = session.capture
            with mock.patch.object(inputs, '_discover', side_effect=AssertionError('rediscovery')), \
                    mock.patch.object(inputs, 'parse', side_effect=AssertionError('live reparsing')), \
                    mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('unchecked byte reread')):
                session.assert_current(capture)
                self.assertIs(session.capture, capture)
            self.put('values.typ', b'#let alpha = 99\n')
            fresh_trees = dict(capture.captures)
            fresh_trees['values.typ'] = parse(b'#let alpha = 99\n')
            fresh_proof = core.certify('main.typ', fresh_trees)
            core.verify_proof(fresh_proof, target_path='main.typ', captures=fresh_trees)
            with mock.patch.object(inputs, '_discover', side_effect=AssertionError('stale rediscovery')), \
                    mock.patch.object(inputs, 'parse', side_effect=AssertionError('stale live parsing')):
                with self.assertRaises(TransactionError):
                    session.assert_current(capture)
                with self.assertRaises(inputs.CapturedRelativeCaptureError):
                    session.assert_current(fresh_proof)
        self.no_manifest()

    def test_alias_decisions_consume_retained_device_inode_not_fresh_stat(self):
        actual = inputs.CapturedRelativeInputSession._check_aliases
        inspected = []
        def check(tx, dependencies):
            with mock.patch.object(os, 'stat', side_effect=AssertionError('alias pathname stat')), \
                    mock.patch.object(Path, 'stat', side_effect=AssertionError('alias Path.stat')), \
                    mock.patch.object(transactions, '_read', side_effect=AssertionError('alias recapture')):
                identities = tx.captured_fingerprints
                actual(tx, dependencies)
            inspected.append(identities)
        with mock.patch.object(inputs.CapturedRelativeInputSession, '_check_aliases', side_effect=check):
            with self.context() as session:
                capture = session.capture
                retained = inspected[0]
                self.assertEqual(set(retained.sources), {'main.typ'})
                for name, record in retained.sources.items():
                    value = (self.f.root / name).stat()
                    self.assertEqual((record.device, record.inode), (value.st_dev, value.st_ino))
                for name, record in retained.evidence.items():
                    value = Path(name).stat()
                    self.assertEqual((record.device, record.inode), (value.st_dev, value.st_ino))
                old = retained.evidence[str(self.f.root / 'values.typ')]
                replacement = self.put('replacement.typ', MODULE)
                replacement.replace(self.f.root / 'values.typ')
                self.assertNotEqual(old.inode, (self.f.root / 'values.typ').stat().st_ino)
                self.assertEqual(retained.evidence[str(self.f.root / 'values.typ')], old)
                with self.assertRaises(TransactionError):
                    session.assert_current(capture)

    def test_source_dependency_config_and_catalog_changes_cannot_be_reblessed(self):
        for label in ('source', 'dependency', 'config', 'catalog'):
            for change in ('content', 'identical-inode-replacement', 'mode'):
                with self.subTest(input=label, change=change):
                    f = self.fixture()
                    path = dict(source=f.source, dependency=f.root / 'values.typ',
                                config=f.config, catalog=f.catalog)[label]
                    with self.context(f) as session:
                        capture = session.capture
                        original = path.read_bytes()
                        if change == 'content':
                            path.write_bytes(original + b'\n')
                        elif change == 'identical-inode-replacement':
                            new = f.base / 'same-byte-replacement'
                            new.write_bytes(original)
                            new.chmod(stat.S_IMODE(path.stat().st_mode))
                            old_inode = path.stat().st_ino
                            new.replace(path)
                            self.assertNotEqual(path.stat().st_ino, old_inode)
                        else:
                            path.chmod(stat.S_IMODE(path.stat().st_mode) ^ 0o040)
                        after = self.state(path)
                        with self.assertRaises(TransactionError):
                            session.assert_current(capture)
                        self.assertEqual(self.state(path), after)
                    self.no_manifest(f)

    def test_dependency_parent_swap_is_stale_even_when_file_inode_and_bytes_survive(self):
        self.put('nested/values.typ', MODULE)
        self.f.source.write_bytes(ROOT.replace(b'values.typ', b'nested/values.typ'))
        parent = self.f.root / 'nested'
        with self.context() as session:
            capture = session.capture
            path = parent / 'values.typ'
            original = self.state(path)
            parent.rename(self.f.root / 'old-parent')
            parent.mkdir()
            (self.f.root / 'old-parent/values.typ').rename(path)
            self.assertEqual(self.state(path)[:6], original[:6])
            with self.assertRaises(TransactionError):
                session.assert_current(capture)
        self.no_manifest()

    def test_ancestor_exchange_preserving_immediate_parent_and_file_inodes_is_stale(self):
        self.put('outer/inner/values.typ', MODULE)
        self.f.source.write_bytes(ROOT.replace(b'values.typ', b'outer/inner/values.typ'))
        outer = self.f.root / 'outer'
        inner = outer / 'inner'
        with self.context() as session:
            capture = session.capture
            path = inner / 'values.typ'
            before = self.state(path)
            parent_identity = inner.stat().st_dev, inner.stat().st_ino
            outer.rename(self.f.root / 'retained-outer')
            outer.mkdir()
            (self.f.root / 'retained-outer/inner').rename(inner)
            self.assertEqual((inner.stat().st_dev, inner.stat().st_ino), parent_identity)
            self.assertEqual(self.state(path), before)
            with self.assertRaises(TransactionError):
                session.assert_current(capture)
        self.no_manifest()

    def test_dependency_xattr_drift_is_stale_and_capture_preserves_metadata(self):
        if sys.platform != 'darwin' and not hasattr(os, 'setxattr'):
            self.skipTest('No extended attribute support')
        path = self.f.root / 'values.typ'
        try:
            self.set_fixture_xattr(path, b'original')
        except OSError as error:
            self.skipTest(f'Fixture filesystem has no user xattrs: {error}')
        before = self.state(path)
        with self.context() as session:
            capture = session.capture
            self.assertEqual(self.state(path), before)
            self.set_fixture_xattr(path, b'changed')
            after = self.state(path)
            self.assertEqual(after[0], before[0])
            with self.assertRaises(TransactionError):
                session.assert_current(capture)
            self.assertEqual(self.state(path), after)

    @unittest.skipUnless(sys.platform == 'darwin' and hasattr(os, 'chflags'), 'macOS file flags')
    def test_source_like_dependency_flags_refuse_initial_capture_and_later_drift(self):
        for when in ('initial', 'after-capture'):
            with self.subTest(when=when):
                f = self.fixture()
                dependency = f.root / 'values.typ'
                try:
                    if when == 'initial':
                        os.chflags(dependency, stat.UF_NODUMP)
                        self.assertTrue(dependency.stat().st_flags & stat.UF_NODUMP)
                        with self.assertRaisesRegex(TransactionError, 'flags'):
                            with self.context(f):
                                self.fail('Unsupported dependency flags were accepted')
                    else:
                        with self.context(f) as session:
                            capture = session.capture
                            os.chflags(dependency, stat.UF_NODUMP)
                            with self.assertRaisesRegex(TransactionError, 'flags'):
                                session.assert_current(capture)
                    self.assertEqual(dependency.read_bytes(), MODULE)
                    self.no_manifest(f)
                finally:
                    os.chflags(dependency, 0)

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS extended ACLs')
    def test_source_like_dependency_acl_refuses_initial_capture_and_later_drift(self):
        for when in ('initial', 'after-capture'):
            with self.subTest(when=when):
                f = self.fixture()
                dependency = f.root / 'values.typ'
                def add_acl():
                    result = subprocess.run(['/bin/chmod', '+a', 'everyone allow read', str(dependency)],
                                            capture_output=True, text=True, check=False)
                    if result.returncode:
                        self.skipTest(f'Fixture filesystem cannot create ACL: {result.stderr.strip()}')
                if when == 'initial':
                    add_acl()
                    with self.assertRaisesRegex(TransactionError, 'ACL'):
                        with self.context(f):
                            self.fail('Unsupported dependency ACL was accepted')
                else:
                    with self.context(f) as session:
                        capture = session.capture
                        add_acl()
                        with self.assertRaisesRegex(TransactionError, 'ACL'):
                            session.assert_current(capture)
                self.assertEqual(dependency.read_bytes(), MODULE)
                self.no_manifest(f)

    def test_missing_and_initial_unsupported_inputs_are_not_successful_capture(self):
        cases = ('missing-root', 'missing-dependency', 'root-utf8', 'dependency-utf8',
                 'parser-error', 'unused-call', 'unused-content', 'unused-function',
                 'unused-field', 'unused-mutation', 'comment', 'directive')
        for case in cases:
            with self.subTest(case=case):
                f = self.fixture()
                dependency = f.root / 'values.typ'
                if case == 'missing-root':
                    f.source.unlink()
                elif case == 'missing-dependency':
                    dependency.unlink()
                elif case.endswith('utf8'):
                    (f.source if case == 'root-utf8' else dependency).write_bytes(b'\xff')
                else:
                    extra = {
                        'parser-error': b'#let broken = (\n',
                        'unused-call': b'#let unused = read("never-read.txt")\n',
                        'unused-content': b'#let unused = [content]\n',
                        'unused-function': b'#let unused(x) = x\n',
                        'unused-field': b'#let unused = alpha.field\n',
                        'unused-mutation': b'#let unused = 1\n#unused = 2\n',
                        'comment': b'// unused comment\n',
                        'directive': b'// typst-lint: ignore-next core.parenthesized-scripts -- no\n',
                    }[case]
                    dependency.write_bytes(MODULE + extra)
                sentinel = self.put('never-read.txt', b'outside initializer data', fixture=f)
                actual = transactions._read
                def guarded(path, *args, **kwargs):
                    self.assertNotEqual(path, sentinel, 'an initializer read was executed')
                    return actual(path, *args, **kwargs)
                with mock.patch.object(transactions, '_read', side_effect=guarded), \
                        mock.patch.object(inputs, '_read', side_effect=guarded):
                    with self.assertRaises(inputs.CapturedRelativeInputError):
                        with self.context(f):
                            self.fail('Unsupported input was certified')
                self.assertEqual(sentinel.read_bytes(), b'outside initializer data')
                self.no_manifest(f)

    def test_stale_invalid_utf8_is_a_capture_failure_not_initial_language_refusal(self):
        for label in ('source', 'dependency'):
            with self.subTest(input=label):
                f = self.fixture()
                with self.context(f) as session:
                    capture = session.capture
                    (f.source if label == 'source' else f.root / 'values.typ').write_bytes(b'\xff')
                    with self.assertRaises(TransactionError) as caught:
                        session.assert_current(capture)
                    self.assertNotIsInstance(caught.exception, inputs.CapturedRelativeInputError)

    def test_root_escape_and_nonminimal_or_executable_import_paths_never_open_target(self):
        requests = ('../outside.typ', '/outside.typ', './values.typ', 'a/../values.typ',
                    'a//values.typ', '@preview/secret:0.1.0', 'values.txt', 'values\\u{2e}typ')
        for request in requests:
            with self.subTest(request=request):
                f = self.fixture(source=('#import "' + request + '": alpha\n#alpha\n').encode())
                sentinel = f.base / 'outside.typ'
                sentinel.write_bytes(MODULE)
                actual = transactions._read
                def guarded(path, *args, **kwargs):
                    self.assertEqual(path, f.source, 'unsupported request followed a pathname')
                    return actual(path, *args, **kwargs)
                with mock.patch.object(inputs, '_read', side_effect=guarded):
                    with self.assertRaises((inputs.CapturedRelativeInputError, inputs.CapturedRelativeCaptureError)):
                        with self.context(f):
                            self.fail('Unsupported path was accepted')
                self.assertEqual(sentinel.read_bytes(), MODULE)
                self.no_manifest(f)

    def test_namespace_wildcard_include_dynamic_and_nested_imports_refuse(self):
        samples = (b'#import "values.typ"\n', b'#import "values.typ": *\n',
                   b'#include "values.typ"\n', b'#import ("values" + ".typ"): alpha\n',
                   b'Body first\n#import "values.typ": alpha\n',
                   b'*#import "values.typ": alpha*\n')
        for source in samples:
            with self.subTest(source=source):
                f = self.fixture(source=source)
                with self.assertRaises(inputs.CapturedRelativeInputError):
                    with self.context(f):
                        self.fail('Unadmitted import syntax was accepted')
                self.no_manifest(f)

    def test_proved_builtin_scalar_shadow_is_accepted_but_calls_and_fields_refuse(self):
        for source, supported in ((b'#import "values.typ": sin\n#sin and $sin_1$\n', True),
                                  (b'#import "values.typ": sin\n$sin(x)_1$\n', False),
                                  (b'#import "values.typ": sin\n#sin.field\n', False)):
            with self.subTest(source=source):
                f = self.fixture(source=source)
                self.put('values.typ', b'#let sin = 7\n', fixture=f)
                if supported:
                    with self.context(f) as session:
                        uses = session.capture.proof.imported_uses
                        self.assertEqual([(u.name, u.syntax_kind) for u in uses],
                                         [('sin', 'Ident'), ('sin', 'MathIdent')])
                        self.assertEqual({u.binding.value for u in uses}, {7})
                else:
                    with self.assertRaises(inputs.CapturedRelativeInputError):
                        with self.context(f):
                            self.fail('Scalar proof incorrectly authorized execution')
                self.no_manifest(f)

    def test_symlink_file_symlink_parent_fifo_and_hardlink_are_capture_refusals(self):
        for kind in ('file-symlink', 'parent-symlink', 'fifo', 'hardlink'):
            with self.subTest(kind=kind):
                if kind == 'fifo' and not hasattr(os, 'mkfifo'):
                    continue
                f = self.fixture()
                outside = f.base / 'owned-outside.typ'
                outside.write_bytes(MODULE)
                dep = f.root / 'values.typ'
                if kind == 'parent-symlink':
                    directory = f.base / 'owned-outside'
                    directory.mkdir()
                    (directory / 'values.typ').write_bytes(MODULE)
                    (f.root / 'nested').symlink_to(directory, target_is_directory=True)
                    f.source.write_bytes(ROOT.replace(b'values.typ', b'nested/values.typ'))
                else:
                    dep.unlink()
                    if kind == 'file-symlink':
                        dep.symlink_to(outside)
                    elif kind == 'fifo':
                        os.mkfifo(dep)
                    else:
                        os.link(outside, dep)
                with self.assertRaises((inputs.CapturedRelativeCaptureError, TransactionError)):
                    with self.context(f):
                        self.fail('Unsafe filesystem input was accepted')
                self.assertEqual(outside.read_bytes(), MODULE)
                self.no_manifest(f)

    def test_case_and_unicode_aliases_use_actual_filesystem_identity(self):
        for first, second in (('values.typ', 'VALUES.typ'), ('caf\u00e9.typ', 'cafe\u0301.typ')):
            with self.subTest(first=first, second=second):
                f = self.fixture(source=('#import ' + json.dumps(first, ensure_ascii=False)
                    + ': alpha as left\n#import ' + json.dumps(second, ensure_ascii=False)
                    + ': alpha as right\n#left #right\n').encode())
                a = self.put(first, MODULE, fixture=f)
                b = f.root / second
                aliases = b.exists() and a.stat().st_ino == b.stat().st_ino
                if not aliases:
                    self.put(second, MODULE, fixture=f)
                if aliases:
                    with self.assertRaisesRegex(inputs.CapturedRelativeCaptureError, 'alias'):
                        with self.context(f):
                            self.fail('Two captured spellings hid one inode')
                else:
                    with self.context(f) as session:
                        origins = [v.origin for v in session.capture.proof.root_bindings]
                        self.assertNotEqual(origins[0], origins[1])
                self.no_manifest(f)

    def test_case_and_unicode_directory_aliases_refuse_even_with_distinct_leaf_files(self):
        for first, second in (('values', 'VALUES'), ('caf\u00e9', 'cafe\u0301')):
            with self.subTest(first=first, second=second):
                f = self.fixture(source=('#import ' + json.dumps(first + '/left.typ', ensure_ascii=False)
                    + ': alpha as left\n#import ' + json.dumps(second + '/right.typ', ensure_ascii=False)
                    + ': alpha as right\n#left #right\n').encode())
                directory = f.root / first
                directory.mkdir()
                other = f.root / second
                aliases = other.exists() and directory.stat().st_ino == other.stat().st_ino
                if not aliases:
                    other.mkdir()
                left = self.put(first + '/left.typ', MODULE, fixture=f)
                right = self.put(second + '/right.typ', MODULE, fixture=f)
                self.assertNotEqual(left.stat().st_ino, right.stat().st_ino)
                if aliases:
                    with self.assertRaisesRegex(inputs.CapturedRelativeCaptureError, 'directory alias'):
                        with self.context(f):
                            self.fail('Directory aliases hid behind distinct file identities')
                else:
                    with self.context(f) as session:
                        self.assertEqual(len(session.capture.captures), 3)
                self.no_manifest(f)

    def test_foreign_copied_constructed_and_expired_receipts_have_no_live_authority(self):
        session_object = None
        with self.context() as session:
            session_object = session
            capture = session.capture
            with self.assertRaises(TypeError):
                inputs.CapturedRelativeInputs()
            clone = object.__new__(inputs.CapturedRelativeInputs)
            for field in fields(capture):
                object.__setattr__(clone, field.name, getattr(capture, field.name))
            for fake in (copy.copy(capture), clone, capture.report, capture.proof,
                         object.__new__(inputs.CapturedRelativeInputs), True):
                with self.subTest(fake=type(fake).__name__):
                    with self.assertRaises(inputs.CapturedRelativeCaptureError):
                        session.assert_current(fake)
            other = self.fixture()
            with self.context(other) as other_session:
                with self.assertRaises(inputs.CapturedRelativeCaptureError):
                    other_session.assert_current(capture)
            proof, trees = capture.proof, capture.captures
        core.verify_proof(proof, target_path='main.typ', captures=trees)  # Historical pure fact.
        with self.assertRaises(inputs.CapturedRelativeCaptureError):
            session_object.assert_current(capture)
        with self.assertRaises(inputs.CapturedRelativeCaptureError):
            session_object.__enter__()
        with self.assertRaises(TransactionError):
            session_object._tx.apply(capture)
        self.no_manifest()

    def test_altered_receipt_fields_and_tree_or_proof_evidence_refuse(self):
        for field_name, replacement in (
            ('root', self.base), ('target', 'other.typ'), ('source_profile', 'structured-core-v1'),
            ('captures', MappingProxyType({})), ('dependency_evidence', MappingProxyType({})),
            ('proof', None), ('report', None), ('_owner', object()), ('_seal', object()),
        ):
            with self.subTest(field=field_name):
                f = self.fixture()
                with self.context(f) as session:
                    capture = session.capture
                    object.__setattr__(capture, field_name, replacement)
                    with self.assertRaises(inputs.CapturedRelativeCaptureError):
                        session.assert_current(capture)
        with self.context() as session:
            capture = session.capture
            tree = capture.captures['values.typ']
            original = tree.source
            object.__setattr__(tree, 'source', MODULE.replace(b'7', b'8'))
            with self.assertRaises(inputs.CapturedRelativeCaptureError):
                session.assert_current(capture)
            object.__setattr__(tree, 'source', original)
            object.__setattr__(capture.proof, 'root_bindings', ())
            with self.assertRaises(inputs.CapturedRelativeCaptureError):
                session.assert_current(capture)
        self.no_manifest()

    def test_coherently_recertified_tree_and_proof_cannot_replace_retained_transaction_bytes(self):
        with self.context() as session:
            capture = session.capture
            original = self.state(self.f.root / 'values.typ')
            tree = capture.captures['values.typ']
            alternate = parse(MODULE.replace(b'7', b'8'))
            object.__setattr__(tree, 'source', alternate.source)
            object.__setattr__(tree, 'nodes', alternate.nodes)
            alternate_proof = core.certify('main.typ', capture.captures)
            for field in fields(capture.proof):
                object.__setattr__(capture.proof, field.name, getattr(alternate_proof, field.name))
            core.verify_proof(capture.proof, target_path='main.typ', captures=capture.captures)
            self.assertEqual(capture.proof.root_bindings[0].value, 8)
            with self.assertRaises(inputs.CapturedRelativeCaptureError):
                session.assert_current(capture)
            self.assertEqual(self.state(self.f.root / 'values.typ'), original)
        self.no_manifest()

    def test_in_place_report_mutation_is_refused_even_though_report_is_not_authority(self):
        for field_name, value in (('target', 'other.typ'), ('sources', ()),
                                  ('edges', ()), ('config_sha256', '0' * 64),
                                  ('catalog_sha256', '1' * 64)):
            with self.subTest(field=field_name):
                f = self.fixture()
                with self.context(f) as session:
                    capture = session.capture
                    object.__setattr__(capture.report, field_name, value)
                    with self.assertRaises(inputs.CapturedRelativeCaptureError):
                        session.assert_current(capture)
                self.no_manifest(f)

    def test_legacy_profiles_still_refuse_imports_and_graph_selection_is_separate(self):
        self.assertEqual(source_profiles.KNOWN_SOURCE_PROFILES, {'closed-core-v1', 'structured-core-v1'})
        self.assertNotIn(core.SOURCE_PROFILE, source_profiles.KNOWN_SOURCE_PROFILES)
        self.assertEqual(source_profiles.KNOWN_OFFLINE_SOURCE_PROFILES,
                         source_profiles.KNOWN_SOURCE_PROFILES | {core.SOURCE_PROFILE})
        self.assertEqual(source_profiles.CAPTURED_RELATIVE_SOURCE_PROFILE, core.SOURCE_PROFILE)
        self.assertTrue(source_profiles.is_offline_source_profile(core.SOURCE_PROFILE))
        for profile in (core.SOURCE_PROFILE.upper(), ' ' + core.SOURCE_PROFILE,
                        core.SOURCE_PROFILE + ' '):
            self.assertFalse(source_profiles.is_offline_source_profile(profile))
        for select in (source_profiles.validate_source_profile, source_profiles.grammar_for_source_profile):
            with self.assertRaises(ValueError):
                select(core.SOURCE_PROFILE)
        for profile in sorted(source_profiles.KNOWN_SOURCE_PROFILES):
            with self.subTest(profile=profile):
                with mock.patch.object(protected_fix, 'CATALOG', self.f.catalog), \
                        mock.patch.object(inputs, '_discover', side_effect=AssertionError('legacy route discovery')):
                    with protected_fix.ProtectedCoreFix(self.f.root, 'main.typ',
                            journal_directory=self.f.store, lease=self.f.lease, source_profile=profile) as fix:
                        with self.assertRaises(lint.SafetyError):
                            fix.validate()
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        self.no_manifest()

    def test_ordinary_read_only_routes_never_start_new_capture_or_compiler_dispatch(self):
        for command in ('check', 'fix', 'compile'):
            with self.subTest(command=command):
                output = io.StringIO()
                with redirect_stdout(output), \
                        mock.patch.object(inputs, '_discover', side_effect=AssertionError('ordinary discovery')), \
                        mock.patch.object(inputs.CapturedRelativeInputSession, '__enter__',
                                          side_effect=AssertionError('ordinary protected capture')), \
                        mock.patch.object(closed_core_compile, 'compile_committed_snapshot',
                                          side_effect=AssertionError('ordinary compiler')):
                    code = lint.main([command, '--root', str(self.f.root), '--format', 'json'])
                self.assertEqual(code, 2)
                self.assertFalse(json.loads(output.getvalue())['compiler_invoked'])
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        self.no_manifest()

    def test_exact_file_and_depth_budgets_are_enforced_by_actual_closure(self):
        self.assertEqual((core.MAX_FILES, core.MAX_IMPORT_DEPTH), (32, 16))
        for count, accepted in ((32, True), (33, False)):
            with self.subTest(files=count):
                f = self.fixture(source=b''.join(
                    f'#import "v{i:02}.typ": alpha as value{i}\n'.encode() for i in range(count - 1)))
                for i in range(count - 1):
                    self.put(f'v{i:02}.typ', MODULE, fixture=f)
                if accepted:
                    with self.context(f) as session:
                        self.assertEqual(len(session.capture.captures), count)
                else:
                    with self.assertRaisesRegex(inputs.CapturedRelativeCaptureError, 'file budget'):
                        with self.context(f):
                            self.fail('File budget exceeded')
        for depth, accepted in ((16, True), (17, False)):
            with self.subTest(depth=depth):
                f = self.fixture(source=b'#import "v01.typ": alpha\n#alpha\n')
                for i in range(1, depth + 1):
                    self.put(f'v{i:02}.typ', MODULE if i == depth else
                             f'#import "v{i + 1:02}.typ": alpha\n'.encode(), fixture=f)
                if accepted:
                    with self.context(f) as session:
                        self.assertEqual(len(session.capture.proof.edges), depth)
                else:
                    with self.assertRaisesRegex(inputs.CapturedRelativeCaptureError, 'depth budget'):
                        with self.context(f):
                            self.fail('Depth budget exceeded')

    def test_exact_edge_and_project_directory_budgets_use_real_files(self):
        self.assertEqual((core.MAX_IMPORT_EDGES, core.MAX_PROJECT_DIRECTORIES), (128, 64))
        for edges, accepted in ((128, True), (129, False)):
            with self.subTest(edges=edges):
                f = self.fixture(source=b'#import "middle.typ": value0\n#value0\n')
                self.put('middle.typ', b''.join(f'#import "values.typ": alpha as value{i}\n'.encode()
                                              for i in range(edges - 1)), fixture=f)
                if accepted:
                    with self.context(f) as session:
                        self.assertEqual(len(session.capture.proof.edges), edges)
                else:
                    with self.assertRaisesRegex(inputs.CapturedRelativeCaptureError, '[Ee]dge budget'):
                        with self.context(f):
                            self.fail('Edge budget exceeded')
        for directories, accepted in ((64, True), (65, False)):
            with self.subTest(directories=directories):
                dependency = '/'.join(['d'] * (directories - 1) + ['values.typ'])
                f = self.fixture(source=f'#import "{dependency}": alpha\n#alpha\n'.encode())
                self.put(dependency, MODULE, fixture=f)
                if accepted:
                    with self.context(f) as session:
                        self.assertIn(dependency, session.capture.captures)
                else:
                    with self.assertRaisesRegex(inputs.CapturedRelativeCaptureError, 'parent-directory budget'):
                        with self.context(f):
                            self.fail('Directory budget exceeded')

    def test_exact_utf8_path_and_total_path_budgets_are_not_character_counts(self):
        self.assertEqual((core.MAX_PATH_BYTES, core.MAX_TOTAL_PATH_BYTES), (512, 16 * 1024))
        for length, accepted in ((512, True), (513, False)):
            with self.subTest(path_bytes=length):
                dependency = 'p' * (length - 262) + '/' + 'q' * 250 + '/values.typ'
                self.assertEqual(len(dependency.encode()), length)
                f = self.fixture(source=f'#import "{dependency}": alpha\n#alpha\n'.encode())
                self.put(dependency, MODULE, fixture=f)
                if accepted:
                    with self.context(f) as session:
                        self.assertIn(dependency, session.capture.captures)
                else:
                    with self.assertRaisesRegex(inputs.CapturedRelativeCaptureError, '(character|byte) budget'):
                        with self.context(f):
                            self.fail('Path budget exceeded')
        unicode_dependency = '가' * 70 + '/' + '나' * 70 + '/' + '한' * 27 + 'abcde.typ'
        self.assertEqual(len(unicode_dependency.encode('utf-8')), 512)
        self.assertLess(len(unicode_dependency), 512)
        for tail, accepted in (('', True), ('x', False)):
            with self.subTest(unicode_path=accepted):
                dependency = unicode_dependency[:-4] + tail + '.typ'
                f = self.fixture(source=f'#import "{dependency}": alpha\n#alpha\n'.encode())
                self.put(dependency, MODULE, fixture=f)
                if accepted:
                    with self.context(f) as session:
                        self.assertIn(dependency, session.capture.captures)
                else:
                    with self.assertRaisesRegex(inputs.CapturedRelativeCaptureError, 'byte budget'):
                        with self.context(f):
                            self.fail('UTF-8 byte budget was treated as a character count')
        # 32 files at exactly 512 bytes each reaches the fixed total bound. A
        # greater total is impossible without violating the file/per-path bound.
        prefix = 'p' * 251 + '/' + 'q' * 250 + '/'
        target = prefix + 'root0.typ'
        self.assertEqual(len(target.encode()), 512)
        source = b''.join(f'#import "v{i:04}.typ": alpha as value{i}\n'.encode() for i in range(31))
        f = self.fixture(target=target, source=source)
        for i in range(31):
            self.put(prefix + f'v{i:04}.typ', MODULE, fixture=f)
        with self.context(f) as session:
            self.assertEqual(sum(len(p.encode()) for p in session.capture.captures), core.MAX_TOTAL_PATH_BYTES)

    def test_exact_source_and_total_byte_budgets_never_truncate(self):
        self.assertEqual((core.MAX_FILE_BYTES, core.MAX_SOURCE_BYTES), (1024 * 1024, 4 * 1024 * 1024))
        limit = core.MAX_FILE_BYTES
        for length, accepted in ((limit, True), (limit + 1, False)):
            with self.subTest(file_bytes=length):
                f = self.fixture(source=b'x' * length)
                if accepted:
                    with self.context(f) as session:
                        self.assertEqual(session.capture.captures['main.typ'].source, b'x' * length)
                else:
                    with self.assertRaisesRegex((inputs.CapturedRelativeCaptureError, TransactionError), '[Bb]yte'):
                        with self.context(f):
                            self.fail('File source bytes exceeded')
        prefix = b''.join(f'#import "v{i}.typ": alpha as value{i}\n'.encode() for i in range(4))
        f = self.fixture(source=prefix + b'x' * (limit - len(prefix)))
        small = len(MODULE)
        for i, size in enumerate((limit, limit, limit - small, small)):
            self.put(f'v{i}.typ', MODULE + b' ' * (size - small), fixture=f)
        with self.context(f) as session:
            self.assertEqual(sum(len(t.source) for t in session.capture.captures.values()), core.MAX_SOURCE_BYTES)
        with (f.root / 'v3.typ').open('ab') as stream:
            stream.write(b' ')
        with self.assertRaisesRegex(inputs.CapturedRelativeCaptureError, 'aggregate source byte budget'):
            with self.context(f):
                self.fail('Aggregate source bytes exceeded')

    def test_new_capture_cannot_expand_transaction_limits_or_accept_forged_lease(self):
        for options in ({'lease': None}, {'lease': True},
                        {'limits': replace(ReadLimits(), max_source_bytes=core.MAX_FILE_BYTES + 1)},
                        {'limits': replace(ReadLimits(), max_capture_bytes=32 * 1024 * 1024)}):
            with self.subTest(options=options):
                with mock.patch.object(inputs, '_discover', side_effect=AssertionError('invalid preflight read')):
                    with self.assertRaises((inputs.CapturedRelativeCaptureError, TransactionError)):
                        with self.context(**options):
                            self.fail('Invalid capture preflight accepted')
        with self.assertRaises(TransactionError):
            with self.context(lease=object.__new__(OfflineExclusive)):
                self.fail('Unissued offline lease accepted')


class _RelativeEnteredSession:
    """Keep the location-only catalog patch scoped to its actual live session."""
    def __init__(self, stack, session):
        self.stack, self.session = stack, session

    def __enter__(self):
        return self.session

    def __exit__(self, *exception):
        return self.stack.__exit__(*exception)


if __name__ == '__main__':
    unittest.main()
