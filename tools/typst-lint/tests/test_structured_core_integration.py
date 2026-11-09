"""Independent real-parser and private native tests for structured-core-v1.

All consumers, journals, PDFs and comparator inputs are temporary. Native tests
reuse the existing captured compiler/font workspace; the bare-script comparator
never discovers system fonts or saves an Apple font outside that workspace.
These small context checks do not establish arbitrary Unicode/style fidelity.
"""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
import zlib

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core_compile as cc
import offline_compile_cli as cli
import protected_fix as bridge
from closed_core import ClosedCoreError, certify, verify_proof
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII, APPLEGOTHIC_RESOURCE_PROFILE as KOREAN
from script_rule import analyze as real_script
from syntax_client import EXECUTABLE, parse
from transactions import ProtectedTransaction, TransactionError
import test_korean_profile_integration as korean
import test_pdf_publication_integration as fixtures

PROFILE = 'structured-core-v1'
GRAMMAR = 'closed-core-markup-v2'
V1 = 'closed-core-v1'
V1_GRAMMAR = 'closed-core-markup-v1'
WRAPPERS = frozenset(('Heading', 'ListItem', 'EnumItem', 'Strong', 'Emph'))
TARGET = '한글 e\u0301 😀/중첩 with spaces/문서.typ'
ORIGINAL = ('\ufeff머리말.\r\n\r\n= 제목 *굵게 _강조 $a_b^c$_*\r\n\r\n'
            '- 항목 *강조 $x_i$*\r\n  + 자동 _목록 $y^2$_\r\n\r\n'
            '본문 $ z_한 $ 끝.\r\n').encode()
EXPECTED = ORIGINAL.replace(b'a_b^c', b'a_(b)^(c)').replace(b'x_i', b'x_(i)').replace(
    b'y^2', b'y^(2)').replace('z_한'.encode(), 'z_(한)'.encode())
ASCII_ORIGINAL = (b'Preface $r_i$.\n\n= Heading $h_i$\n\n'
                  b'Plain $p_i$.\n\n*Strong $b_i$*\n\n_Emphasis $e_i$_\n\n'
                  b'- Bullet $l_i$\n  + Number $n_i$\n\n'
                  b'- *Boldbullet $q_i$*\n\n= _Emphheading $t_i$_\n')
KOREAN_ORIGINAL = ('머리말 $r_i$.\n\n= 제목한글 $h_i$\n\n'
                   '평문가나다 $p_i$.\n\n*굵게라마바 $b_i$*\n\n_강조사아자 $e_i$_\n\n'
                   '- 항목차카타 $l_i$\n  + 번호파하가 $n_i$\n\n'
                   '- *굵은목록나따라 $q_i$*\n\n= _기울인제목마싸아 $t_i$_\n\n'
                   '동일글자\n\n*동일글자*\n\n_동일글자_\n\n*_동일글자_*\n').encode()
KOREAN_CONTEXTS = ('제목한글', '평문가나다', '굵게라마바', '강조사아자',
                   '항목차카타', '번호파하가', '굵은목록나따라', '기울인제목마싸아')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _owners(tree):
    """Equation wrapper ancestry/display identity, independent of bridge code."""
    result = []

    def visit(index, owners):
        node = tree.nodes[index]
        if node.kind in WRAPPERS:
            owners += (node.kind,)
        if node.kind == 'Equation':
            result.append((owners, node.display))
        for child in node.children:
            visit(child, owners)

    visit(0, ())
    return tuple(result)


def _tree_state(root):
    """Stable bytes and identity, excluding read-induced access times."""
    result = {}
    for path in (root, *sorted(root.rglob('*'))):
        info = path.lstat()
        result[str(path.relative_to(root))] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns,
            os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None)
    return result


class _StructuredFixtures(korean._KoreanFixtures):
    def fixture(self, **kwargs):
        kwargs.setdefault('source', ORIGINAL)
        kwargs.setdefault('target', TARGET)
        return super().fixture(**kwargs)

    def fix(self, f, **kwargs):
        kwargs.setdefault('source_profile', PROFILE)
        return fixtures.fixture_fix(f, **kwargs)

    def fix_arguments(self, f, *, profile=PROFILE):
        return ['fix', '--root', str(f.root), '--format', 'json',
                '--profile', profile, '--offline-exclusive', '--target', f.target]

    def compile_arguments(self, f, profile=KOREAN):
        result = super().compile_arguments(f, profile)
        result[result.index('--profile') + 1] = PROFILE
        return result

    def source_cli(self, arguments, *, gate=False):
        command = TOOL / ('typst-style' if gate else 'typst-lint')
        result = subprocess.run([sys.executable, str(command), *arguments],
            cwd=self.base, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True, timeout=30)
        self.assertEqual(result.stderr, b'')
        self.assertIn(result.returncode, (0, 2, 3), result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['status'], result.returncode, payload)
        return payload

    def assert_truth(self, summary):
        self.assertEqual((summary['profile'], summary['grammar_version']), (PROFILE, GRAMMAR))
        self.assertEqual(len(summary['required_rule_ids']), 15)
        self.assertEqual(summary['evaluated_rule_ids'], [bridge.SCRIPT])
        self.assertEqual(len(summary['not_applicable']), 14)
        self.assertTrue(summary['coverage']['input_profile_complete'])
        self.assertFalse(summary['coverage']['global_complete'])
        self.assertEqual(summary['coverage']['global_rule_status'], 'PARTIAL')
        self.assertEqual(summary['coverage']['global_partial_rule_count'], 33)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX protected publication')
class StructuredCoreIntegrationTests(_StructuredFixtures):
    def test_real_parser_one_pass_preserves_nested_ownership_original_bytes_and_noop_identity(self):
        f = self.fixture()
        before_tree = parse(ORIGINAL)
        self.assertTrue(WRAPPERS <= {node.kind for node in before_tree.nodes})
        untouched = tuple(fixtures.file_state(p) for p in (f.config, f.unrelated, f.output))
        with mock.patch.object(bridge, 'analyze_script', wraps=real_script) as actual:
            with self.fix(f) as fix:
                candidate = fix.validate()
                self.assertEqual(candidate.passes, 1)
                self.assertEqual(candidate.sources, {f.target: EXPECTED})
                self.assertFalse(candidate.findings)
                result = fix.apply(candidate)
                summary = fix.summary
                self.assertEqual((summary.profile, summary.grammar_version), (PROFILE, GRAMMAR))
                snapshot = fix.compile_snapshot()
                self.assertEqual((snapshot.source_profile, snapshot.grammar_version), (PROFILE, GRAMMAR))
                self.assertEqual(snapshot.source, EXPECTED)
                fix.assert_compile_current(snapshot)
            self.assertEqual(len(actual.call_args_list), 6)
            self.assertTrue(all(call.kwargs['_verify'] is False for call in actual.call_args_list))
        self.assertEqual(result.modified, (f.target,))
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertTrue(EXPECTED.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(ORIGINAL.count(b'\r\n'), EXPECTED.count(b'\r\n'))
        replay = ORIGINAL
        for edit in sorted(summary.applied_insertions, key=lambda e: e.start, reverse=True):
            self.assertEqual((edit.path, edit.rule_id, edit.start), (f.target, bridge.SCRIPT, edit.end))
            self.assertIn(edit.replacement, (b'(', b')'))
            replay = replay[:edit.start] + edit.replacement + replay[edit.end:]
        self.assertEqual(replay, EXPECTED)
        after_tree = parse(EXPECTED)
        self.assertEqual(_owners(before_tree), _owners(after_tree))
        self.assertEqual([r[0] for r in bridge._operand_records(before_tree, f.target)],
                         [r[0] for r in bridge._operand_records(after_tree, f.target)])
        old_wrappers = [node for node in before_tree.nodes if node.kind in WRAPPERS]
        new_wrappers = [node for node in after_tree.nodes if node.kind in WRAPPERS]
        self.assertEqual([n.kind for n in old_wrappers], [n.kind for n in new_wrappers])
        for old, new in zip(old_wrappers, new_wrappers, strict=True):
            raw = ORIGINAL[old.start:old.end]
            for edit in sorted(summary.applied_insertions, key=lambda e: e.start, reverse=True):
                if old.start <= edit.start < old.end:
                    position = edit.start - old.start
                    raw = raw[:position] + edit.replacement + raw[position:]
            self.assertEqual(raw, EXPECTED[new.start:new.end])
        self.assertEqual(tuple(fixtures.file_state(p) for p in (f.config, f.unrelated, f.output)), untouched)
        state = _tree_state(f.base)
        with self.fix(f) as fix:
            candidate = fix.validate()
            self.assertEqual(candidate.passes, 0)
            result = fix.apply(candidate)
            self.assertEqual((result.modified, result.journal), ((), None))
            self.assertEqual(fix.summary.applied_insertions, ())
        self.assertEqual(_tree_state(f.base), state)

    def test_real_subprocess_v1_refuses_v2_repairs_and_reports_exact_truth_then_noops(self):
        f = self.fixture()
        before = fixtures.file_state(f.source), fixtures.file_state(f.output)
        refused = self.source_cli(self.fix_arguments(f, profile=V1))
        self.assertEqual(refused['status'], 2, refused)
        self.assertEqual((fixtures.file_state(f.source), fixtures.file_state(f.output)), before)
        result = self.source_cli(self.fix_arguments(f))
        self.assertEqual(result['status'], 0, result)
        self.assert_truth(result)
        self.assertEqual((result['original_sha256'], result['final_sha256']),
                         (_hash(ORIGINAL), _hash(EXPECTED)))
        self.assertEqual(result['modified_paths'], [f.target])
        self.assertFalse(result['compiler_invoked'])
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        state = _tree_state(f.base)
        repeated = self.source_cli(self.fix_arguments(f))
        self.assert_truth(repeated)
        self.assertEqual((repeated['modified_paths'], repeated['applied_insertions'], repeated['journal_id']),
                         ([], [], None))
        self.assertEqual(_tree_state(f.base), state)

    def test_default_v1_still_refuses_every_new_wrapper_without_source_writes(self):
        for source in (b'= Heading $x_i$\n', b'- Bullet $x_i$\n', b'+ Number $x_i$\n',
                       b'*Strong $x_i$*\n', b'_Emphasis $x_i$_\n'):
            with self.subTest(source=source):
                f = self.fixture(source=source)
                before = fixtures.file_state(f.source)
                with fixtures.fixture_fix(f) as fix, self.assertRaises(ClosedCoreError):
                    fix.validate()
                self.assertEqual(fixtures.file_state(f.source), before)
                self.assertEqual(list(f.source_store.glob('root-*/*/manifest.json')), [])

    def test_forbidden_nodes_at_nested_depth_never_create_source_journals(self):
        cases = (
            (b'- *outer _inner #read("secret") $x_i$_*\n', 'FuncCall'),
            (b'- *outer _inner /* ordinary */ $x_i$_*\n', 'BlockComment'),
            (b'- *outer _inner /* typst-lint-disable */ $x_i$_*\n', 'BlockComment'),
            (b'- *outer _inner `raw` $x_i$_*\n', 'Raw'),
            (b'- *outer _inner #include "other.typ" $x_i$_*\n', 'Include'),
            (b'- *outer _inner #import "other.typ": x $x_i$_*\n', 'Import'),
            (b'- *outer _inner #set text(size: 12pt) $x_i$_*\n', 'SetRule'),
            (b'- *outer _inner @reference $x_i$_*\n', 'Ref'),
            (b'- *outer _inner $x_i$ <private-label>_*\n', 'Label'),
            (b'- Outer\n  2. Numbered $x_i$\n', 'EnumItem'),
            (b'- Outer\n  / Term: $x_i$\n', 'TermItem'),
        )
        for source, kind in cases:
            with self.subTest(kind=kind, source=source):
                self.assertIn(kind, {n.kind for n in parse(source).nodes})
                f = self.fixture(source=source)
                before = tuple(fixtures.file_state(p) for p in (f.source, f.output, f.unrelated))
                with self.fix(f) as fix, self.assertRaises(ClosedCoreError):
                    fix.validate()
                self.assertEqual(tuple(fixtures.file_state(p) for p in (f.source, f.output, f.unrelated)), before)
                self.assertEqual(list(f.source_store.glob('root-*/*/manifest.json')), [])

    def test_unknown_nested_parser_node_refuses_before_backend_or_writes(self):
        f = self.fixture()
        tree = parse(ORIGINAL)
        nodes = list(tree.nodes)
        # Preserve real spans/partition and only replace a deeply owned leaf kind.
        target = next(index for index, node in enumerate(nodes)
                      if node.kind == 'Text' and tree.source[node.start:node.end] == '강조'.encode())
        nodes[target] = replace(nodes[target], kind='FutureUnknownLiteral')
        forged = replace(tree, nodes=tuple(nodes))
        before = fixtures.file_state(f.source)
        with self.fix(f) as fix, mock.patch.object(bridge, 'parse', return_value=forged), \
                mock.patch.object(bridge, 'analyze_script', side_effect=AssertionError('backend must not run')):
            with self.assertRaises(ClosedCoreError):
                fix.validate()
        self.assertEqual(fixtures.file_state(f.source), before)
        self.assertEqual(list(f.source_store.glob('root-*/*/manifest.json')), [])

    def test_new_profile_keeps_exact_core_single_literal_config_and_explicit_leases(self):
        for config in (b'rule-set="pde"\n',
                       ('rule-set="core"\ninclude=["' + TARGET + '","other.typ"]\n').encode(),
                       b'rule-set="core"\ninclude=["**/*.typ"]\n',
                       ('rule-set="core"\ninclude=[' + json.dumps(TARGET) + ']\nignore=[]\n').encode()):
            with self.subTest(config=config):
                f = self.fixture()
                f.config.write_bytes(config)
                before = fixtures.file_state(f.source)
                with self.assertRaises(bridge.ProtectedCoreError):
                    with self.fix(f):
                        self.fail('unsupported config entered')
                self.assertEqual(fixtures.file_state(f.source), before)
        for missing in ('--offline-exclusive', '--offline-output-exclusive'):
            with self.subTest(missing=missing):
                f = self.fixture()
                args = self.compile_arguments(f)
                args.remove(missing)
                before = _tree_state(f.base)
                result = self.source_cli(args)
                self.assertEqual(result['status'], 2, result)
                self.assertEqual(_tree_state(f.base), before)

    def test_independent_replay_and_transaction_only_candidate_cannot_authorize_new_profile(self):
        for kind in ('transaction-only', 'changed-replay'):
            with self.subTest(kind=kind):
                f = self.fixture()
                before = fixtures.file_state(f.source)
                with self.fix(f) as fix:
                    if kind == 'transaction-only':
                        token = fix.transaction.validate(fix._analyze_graph, required_rules=bridge.CORE_RULE_IDS)
                        with self.assertRaises(bridge.ProtectedCoreError):
                            fix.apply(token)
                    else:
                        replay = fix._independent_replay(fix.transaction.snapshot)
                        with mock.patch.object(fix, '_independent_replay', return_value=replace(replay, content=ORIGINAL)):
                            with self.assertRaises(bridge.ProtectedCoreError):
                                fix.validate()
                self.assertEqual(fixtures.file_state(f.source), before)
                self.assertEqual(list(f.source_store.glob('root-*/*/manifest.json')), [])

    def test_session_profile_grammar_and_selected_proof_confusion_refuse_before_apply(self):
        for kind in ('profile', 'grammar', 'proof-profile', 'proof-grammar'):
            with self.subTest(kind=kind):
                f = self.fixture()
                before = fixtures.file_state(f.source)
                with self.fix(f) as fix:
                    candidate = fix.validate()
                    if kind == 'profile':
                        fix.source_profile = V1
                    elif kind == 'grammar':
                        fix.grammar_version = V1_GRAMMAR
                    else:
                        proof = fix._compile_validation.proof
                        object.__setattr__(proof, 'source_profile' if kind == 'proof-profile' else 'grammar_version',
                                           V1 if kind == 'proof-profile' else V1_GRAMMAR)
                    with self.assertRaises((bridge.ProtectedCoreError, ClosedCoreError)):
                        fix.apply(candidate)
                self.assertEqual(fixtures.file_state(f.source), before)
                self.assertEqual(list(f.source_store.glob('root-*/*/manifest.json')), [])

    def test_snapshot_profile_grammar_proof_and_cross_session_authority_refuse_before_launch(self):
        for kind in ('profile', 'grammar', 'proof', 'foreign-snapshot', 'foreign-candidate', 'report'):
            with self.subTest(kind=kind):
                left, right = self.fixture(), self.fixture()
                with self.fix(left) as fix, self.fix(right) as other:
                    candidate, foreign_candidate = fix.validate(), other.validate()
                    if kind == 'foreign-candidate':
                        with self.assertRaises(bridge.ProtectedCoreError):
                            fix.apply(foreign_candidate)
                        self.assertEqual(left.source.read_bytes(), ORIGINAL)
                        continue
                    fix.apply(candidate)
                    other.apply(foreign_candidate)
                    snapshot = fix.compile_snapshot()
                    selected = snapshot
                    if kind == 'profile':
                        object.__setattr__(snapshot, 'source_profile', V1)
                    elif kind == 'grammar':
                        object.__setattr__(snapshot, 'grammar_version', V1_GRAMMAR)
                    elif kind == 'proof':
                        object.__setattr__(snapshot._validation.proof, 'grammar_version', V1_GRAMMAR)
                    elif kind == 'foreign-snapshot':
                        selected = other.compile_snapshot()
                    else:
                        selected = fix.summary
                    before = fixtures.file_state(left.source), fixtures.file_state(left.output)
                    with mock.patch.object(cc, '_capture_compiler', side_effect=AssertionError('must not capture')):
                        with self.assertRaises(cc.CompileSafetyError):
                            cc.compile_committed_snapshot(fix, selected, profile=KOREAN)
                    self.assertEqual((fixtures.file_state(left.source), fixtures.file_state(left.output)), before)

    def test_proof_profile_is_exact_even_for_source_admitted_under_both_grammars(self):
        tree = parse(b'Plain $x_(i)$\n')
        for selected, wrong in ((PROFILE, V1), (V1, PROFILE)):
            with self.subTest(selected=selected):
                proof = certify(tree, TARGET, source_profile=selected)
                verify_proof(proof, tree, TARGET, source_profile=selected)
                with self.assertRaises(ClosedCoreError):
                    verify_proof(proof, tree, TARGET, source_profile=wrong)

    def test_explicit_compile_cli_retains_resource_facts_and_truth_for_both_profiles(self):
        for resource, source in ((ASCII, ASCII_ORIGINAL), (KOREAN, ORIGINAL)):
            with self.subTest(resource=resource):
                f = self.fixture(source=source)
                captured, dependencies = [], []
                compile_snapshot, run = cc.compile_committed_snapshot, cc._run

                def record(*args, **kwargs):
                    artifact = compile_snapshot(*args, **kwargs)
                    self.assertEqual(artifact.dependency_paths, (f.target,))
                    captured.append(artifact.report)
                    return artifact

                def observe(argv, **kwargs):
                    invocation = run(argv, **kwargs)
                    if kwargs['purpose'] == 'compile':
                        dependencies.append(Path(argv[argv.index('--deps') + 1]).read_bytes())
                        self.assertIn('--ignore-system-fonts', argv)
                        self.assertEqual(argv.count('--font-path'), 1 if resource == KOREAN else 0)
                    return invocation

                with self.resources(f), mock.patch.object(bridge, 'CATALOG', f.catalog), \
                        mock.patch.object(cc, 'compile_committed_snapshot', side_effect=record), \
                        mock.patch.object(cc, '_run', side_effect=observe):
                    result = self.run_cli(self.compile_arguments(f, resource))
                self.assertEqual(result['status'], 0, result)
                self.assert_truth(result['source_summary'])
                self.assertEqual(result['profile'], PROFILE)
                self.assertEqual(dependencies, [os.fsencode(f.target) + b'\0'])
                retained = cli.resource_report_json(captured[0].resource_report)
                self.assertEqual(result['compile_report']['resource_report'], retained)
                self.assertEqual(result['publication_report']['resource_report'], retained)
                self.assertEqual(retained['profile'], resource)
                self.assertEqual(len(retained['fonts']), 1 if resource == KOREAN else 0)
                for font in retained['fonts']:
                    self.assertFalse(Path(font['private']['path']).exists())
                    self.assertEqual(font['installed']['sha256'], _hash(korean.FONT_BYTES))
                self.assertNotIn(korean.FONT_BYTES.hex(), json.dumps(result))

    def test_compile_cli_v1_refuses_structured_input_before_compiler_and_preserves_old_pdf(self):
        f = self.fixture()
        arguments = self.compile_arguments(f)
        arguments[arguments.index('--profile') + 1] = V1
        before = fixtures.file_state(f.source), fixtures.file_state(f.output)
        with mock.patch.object(bridge, 'CATALOG', f.catalog), \
                mock.patch.object(cc, '_capture_compiler', side_effect=AssertionError('must not capture')):
            result = self.run_cli(arguments)
        self.assertEqual(result['status'], 2, result)
        self.assertFalse(result['compiler_invoked'])
        self.assertIsNone(result['source_summary'])
        self.assertEqual((fixtures.file_state(f.source), fixtures.file_state(f.output)), before)

    def test_interrupted_structured_source_uses_unchanged_byte_recovery_without_parser(self):
        class OwnedInterruption(BaseException):
            pass

        f = self.fixture()

        def interrupt(point, path):
            if point == 'after_replace':
                raise OwnedInterruption()

        with self.assertRaises(OwnedInterruption):
            with self.fix(f, fault=interrupt) as fix:
                fix.apply(fix.validate())
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        manifest, = f.source_store.glob('root-*/*/manifest.json')
        data = json.loads(manifest.read_bytes())
        self.assertEqual(data['schema'], 1)
        self.assertNotIn('profile', data)
        f.config.unlink()
        f.catalog.unlink()
        with mock.patch.object(bridge, 'parse', side_effect=AssertionError('recovery must not parse')), \
                ProtectedTransaction(f.root, (f.target,), journal_directory=f.source_store,
                    lease=f.source_lease, recovery_only=True) as recovery:
            report = recovery.recover(manifest.parent.name)
        self.assertTrue(report.complete)
        self.assertEqual(report.restored, (f.target,))
        self.assertEqual(f.source.read_bytes(), ORIGINAL)
        self.assertEqual(f.output.read_bytes(), fixtures.OLD_OUTPUT)

    def test_interrupted_structured_pdf_recovers_without_live_source_or_resources(self):
        f = self.fixture()

        def interrupt(point, value):
            if point == 'before_event' and value == 'published':
                raise OSError('owned interrupted completion')

        with self.resources(f) as compiler, self.fix(f) as fix:
            with self.session(fix, f, fault=interrupt) as pub:
                fix.apply(fix.validate())
                with self.assertRaises(korean.store.PublicationError):
                    pub.compile_and_publish(fix.compile_snapshot(), profile=KOREAN)
        self.assertTrue(pub.report.recovery_required)
        self.remove_live_inputs(f, compiler)
        with self.no_live_resources():
            entry, = korean.store.inventory_publications(f.root)
            self.assertEqual((entry.state, entry.pending), ('replaced', True))
            recovered = korean.store.recover_publication(f.root, destination=f.destination,
                journal_id=pub.report.journal_id, lease=f.output_lease)
        self.assertEqual(recovered.output_disposition, 'restored')
        self.assertIsNone(recovered.resource_report)
        self.assertEqual(f.output.read_bytes(), fixtures.OLD_OUTPUT)

    def test_ordinary_routes_remain_readonly_two_without_inferring_source_profile(self):
        for command, extra, gate in (('check', (), False), ('fix', (), False),
                                     ('fix', ('--dry-run',), False),
                                     ('compile', (), False), ('compile', (), True)):
            with self.subTest(command=command, extra=extra, gate=gate):
                f = self.fixture(source=ASCII_ORIGINAL)
                before = _tree_state(f.base)
                result = self.source_cli([command, '--root', str(f.root), '--format', 'json', *extra], gate=gate)
                self.assertEqual(result['status'], 2, result)
                self.assertFalse(result['compiler_invoked'])
                self.assertEqual(_tree_state(f.base), before)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
                     'Opt in to private native context checks with TYPST_LINT_RENDER_TESTS=1')
@unittest.skipUnless(sys.platform == 'darwin' and EXECUTABLE.is_file()
                     and fixtures.PINNED.is_file() and korean.FONT_PATH.is_file()
                     and all(shutil.which(name) for name in ('pdftotext', 'pdffonts', 'pdftoppm', 'pdftohtml')),
                     'Requires pinned Mac resources, syntax adapter and local Poppler test helpers')
class NativeStructuredPublicationTests(_StructuredFixtures):
    def helper(self, name, *arguments):
        result = subprocess.run([shutil.which(name), *map(str, arguments)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def bbox(self, pdf):
        raw = self.helper('pdftotext', '-bbox', pdf, '-')
        root = ET.fromstring(raw)
        return tuple((node.text, tuple((key, node.attrib[key]) for key in ('xMin', 'yMin', 'xMax', 'yMax')))
                     for node in root.iter() if node.tag.endswith('}word'))

    def native_context(self, resource, source):
        f = self.fixture(source=source)
        before = fixtures.file_state(f.unrelated)
        comparison = f.base / 'bare comparator'
        comparison.mkdir(mode=0o700)
        bare, bare_pdf, bare_deps = comparison / 'original.typ', comparison / 'bare.pdf', comparison / 'deps.zero'
        bare.write_bytes(source)
        calls, run = [], cc._run

        def compare_in_same_private_environment(argv, **kwargs):
            result = run(argv, **kwargs)
            if kwargs['purpose'] == 'compile':
                self.assertIn('--ignore-system-fonts', argv)
                self.assertEqual(argv.count('--font-path'), 1 if resource == KOREAN else 0)
                self.assertEqual(Path(argv[0]).read_bytes(), fixtures.PINNED.read_bytes())
                if resource == KOREAN:
                    font_directory = Path(argv[argv.index('--font-path') + 1])
                    self.assertEqual([p.name for p in font_directory.iterdir()], ['AppleGothic.ttf'])
                    self.assertEqual(_hash((font_directory / 'AppleGothic.ttf').read_bytes()), korean.FONT_SHA256)
                self.assertEqual(Path(argv[argv.index('--deps') + 1]).read_bytes(), os.fsencode(f.target) + b'\0')
                # Only source/root/output/deps differ. In particular the already
                # captured compiler, private font path and sealed env are reused.
                args = list(argv)
                args[args.index('--root') + 1] = str(comparison)
                args[args.index('--deps') + 1] = str(bare_deps)
                args[-2:] = [str(bare), str(bare_pdf)]
                compared = subprocess.run(args, cwd=comparison, env=kwargs['environment'],
                                          capture_output=True, timeout=60)
                self.assertEqual((compared.returncode, compared.stderr), (0, b''))
                self.assertEqual(bare_deps.read_bytes(), b'original.typ\0')
                calls.append((result, tuple(args)))
            return result

        with self.fix(f) as fix:
            with self.session(fix, f) as pub, mock.patch.object(cc, '_run', side_effect=compare_in_same_private_environment):
                candidate = fix.validate()
                self.assertEqual(candidate.passes, 1)
                fix.apply(candidate)
                snapshot = fix.compile_snapshot()
                self.assertEqual((snapshot.source_profile, snapshot.grammar_version), (PROFILE, GRAMMAR))
                report = pub.compile_and_publish(snapshot, profile=resource, deadline_seconds=120)
                self.assertEqual(pub._artifact.dependency_paths, (f.target,))
                self.assertEqual(pub._artifact.compiler.sha256, fixtures.PINNED_SHA256)
                self.assertEqual(pub._artifact.compilation.stderr, b'')
        self.assertEqual(len(calls), 1)
        self.assertEqual((report.phase, report.gate_status, report.compiler_status), ('published', 0, 0))
        self.assertEqual(report.resource_report.profile, resource)
        self.assertEqual(report.resource_report, report.compile_report.resource_report)
        self.assertEqual(fixtures.file_state(f.unrelated), before)
        self.assertEqual(self.bbox(f.output), self.bbox(bare_pdf))
        for pdf, stem in ((f.output, 'fixed'), (bare_pdf, 'bare')):
            self.helper('pdftoppm', '-singlefile', '-r', '110', pdf, f.base / stem)
        # Raw RGB raster equality detects missing/moved glyphs beyond extraction.
        self.assertEqual((f.base / 'fixed.ppm').read_bytes(), (f.base / 'bare.ppm').read_bytes())
        self.assertFalse(korean.store.inventory_publications(f.root)[0].pending)
        return f, report

    def test_native_structured_ascii_publication_matches_bare_script_text_and_raster(self):
        f, report = self.native_context(ASCII, ASCII_ORIGINAL)
        self.assertEqual(report.resource_report.fonts, ())
        text = self.helper('pdftotext', '-layout', f.output, '-').decode()
        for token in ('Heading', 'Strong', 'Emphasis', 'Bullet', 'Number', 'Boldbullet', 'Emphheading'):
            self.assertIn(token, text)
        self.assertNotIn(b'AppleGothic', self.helper('pdffonts', f.output))

    def test_native_structured_korean_contexts_keep_glyphs_font_and_bare_script_raster(self):
        f, report = self.native_context(KOREAN, KOREAN_ORIGINAL)
        font, = report.resource_report.fonts
        self.assertEqual((font.installed.path, font.installed.sha256, font.installed.size, font.face_index),
                         (str(korean.FONT_PATH), korean.FONT_SHA256, 15253080, 0))
        self.assertEqual(font.identity.family, 'AppleGothic')
        self.assertEqual(font.private.sha256, korean.FONT_SHA256)
        self.assertFalse(Path(font.private.path).exists())
        text = self.helper('pdftotext', '-layout', f.output, '-').decode()
        for token in KOREAN_CONTEXTS:
            self.assertIn(token, text)
        self.assertNotIn('\ufffd', text)
        words = dict(self.bbox(f.output))
        for token in KOREAN_CONTEXTS:
            self.assertIn(token, words)
            box = dict(words[token])
            self.assertGreater(float(box['xMax']) - float(box['xMin']), 0)
            self.assertGreater(float(box['yMax']) - float(box['yMin']), 0)
        plain, heading = dict(words['평문가나다']), dict(words['제목한글'])
        self.assertGreater(float(heading['yMax']) - float(heading['yMin']),
                           1.15 * (float(plain['yMax']) - float(plain['yMin'])))
        fonts = self.helper('pdffonts', f.output)
        apple_rows = [line for line in fonts.splitlines() if b'AppleGothic' in line]
        self.assertTrue(apple_rows, fonts)
        self.assertTrue(all(row.split()[-5:-2] == [b'yes', b'yes', b'yes'] for row in apple_rows))
        # Map every distinct Korean context back to its actual PDF font. This
        # catches silent fallback to another font in strong/emphasis/list/heading.
        xml = ET.fromstring(self.helper('pdftohtml', '-xml', '-hidden', '-stdout', f.output))
        specs = {node.attrib['id']: node.attrib for node in xml.iter('fontspec')}
        for token in KOREAN_CONTEXTS:
            spans = [node for node in xml.iter('text') if token in ''.join(node.itertext())]
            self.assertTrue(spans, token)
            self.assertTrue(all('AppleGothic' in specs[node.attrib['font']]['family'] for node in spans), token)
        repeated = [node for node in xml.iter('text') if ''.join(node.itertext()) == '동일글자']
        self.assertEqual(len(repeated), 4)
        self.assertEqual(len({node.attrib['font'] for node in repeated}), 1)
        self.assertTrue(all(not list(node) for node in repeated))
        # Characterize the actual single-face limit, rather than inferring lack
        # of synthetic bold/italic merely from the font's Regular name. The last
        # four runs are the same Korean word in plain/strong/emph/both contexts.
        # Their glyphs, font/size, fill rendering and unskewed matrices coincide.
        content = []
        for match in re.finditer(rb'stream\r?\n(.*?)\r?\nendstream', f.output.read_bytes(), re.S):
            try:
                decoded = zlib.decompress(match[1])
            except zlib.error:
                continue
            if b'BT' in decoded and b'Tf' in decoded:
                content.append(decoded)
        self.assertEqual(len(content), 1)
        runs = re.findall(rb'\bq\s+(.*?)\s+Q\b', content[0], re.S)[-4:]
        self.assertEqual(len(runs), 4)
        paint = []
        for run in runs:
            matrix, operations = run.split(b' cm', 1)
            self.assertEqual(matrix.split()[:4], [b'1', b'0', b'0', b'-1'])
            self.assertIn(b'BT 0 Tr', operations)
            self.assertIn(b'11 Tf 1 0 0 -1 0 0 Tm', operations)
            paint.append((matrix.split()[4], operations))
        self.assertTrue(all(item == paint[0] for item in paint))


if __name__ == '__main__':
    unittest.main()
