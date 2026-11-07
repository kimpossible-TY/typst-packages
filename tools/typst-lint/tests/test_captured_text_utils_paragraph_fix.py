"""Independent real-parser/transaction tests for INTERNAL paragraph repair.

Only temporary consumers are touched. No native Typst/font/compiler process,
public profile, consumer Agent file, installation or deployment is involved.
"""
from contextlib import ExitStack
import base64
import copy
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import capitalization
import captured_text_utils_paragraph_fix as bridge
import captured_text_utils_paragraph_core as core
import captured_text_utils_paragraph_inputs as inputs
from edit_plan import Edit
import paragraph_rules
import source_profiles
from syntax_client import EXECUTABLE, parse
import transactions
from transactions import (OfflineExclusive, ProtectedTransaction, ReadLimits, RecoveryRequired,
                          SourceEdit, TransactionError, TransactionFailure)
import typst_lint as lint
from paragraph_fix_test_support import (ROOT, EXPECTED, PREFIX, CONFIG, changed, context,
                                       evidence, fixture, same_byte_replace, set_fixture_xattr, state)

WORKER = Path(__file__).with_name('captured_text_utils_paragraph_process_worker.py')
CAP = capitalization.RULE_ID
ACTUAL = frozenset(('core.parenthesized-scripts', CAP, 'pde.paragraph-marker-gap',
                    'pde.paragraph-marker-target', 'pde.prose-paragraph-marker'))


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX protected transactions')
class CapturedTextUtilsParagraphFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='typst-paragraph-fix-integration-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.sequence = 0
        self.f = self.fixture()

    def fixture(self, **kwargs):
        self.sequence += 1
        return fixture(self.base, self.sequence, **kwargs)

    def context(self, f=None, **kwargs):
        return context(f or self.f, **kwargs)

    def no_manifest(self, f=None):
        self.assertFalse(list((f or self.f).store.glob('root-*/*/manifest.json')))

    def apply(self, f=None, **kwargs):
        with self.context(f, **kwargs) as fix:
            return fix.apply(fix.validate())

    def refused(self, fix, candidate, f=None):
        f = f or self.f
        with self.assertRaises(TransactionError):
            fix.apply(candidate)
        self.assertEqual(f.source.read_bytes(), f.original)
        self.no_manifest(f)

    def test_real_backend_one_byte_diff_one_owned_transaction_and_four_evidence_files(self):
        f = self.f
        originals = tuple(state(p) for p in evidence(f))
        points = []
        with self.context(fault=lambda point, path: points.append((point, path))) as fix:
            self.assertIs(fix._tx, fix._session._tx)
            self.assertEqual(fix._tx.targets, ('main.typ',))
            self.assertEqual(set(fix._tx.snapshot.evidence),
                             {str(p) for p in evidence(f)[:-1]})
            candidate = fix.validate()
            self.assertEqual(candidate.sources, {f.target: EXPECTED})
            self.assertEqual((candidate.passes, candidate.findings), (1, frozenset()))
            self.assertEqual(f.source.read_bytes(), ROOT)
            self.no_manifest()
            result = fix.apply(candidate)
            summary = fix.summary
            self.assertEqual(summary.profile, 'captured-text-utils-paragraph-v1')
            self.assertEqual(summary.target, f.target)
            self.assertEqual(summary.original_sha256, lint.digest(ROOT))
            self.assertEqual(summary.final_sha256, lint.digest(EXPECTED))
            self.assertEqual(set(summary.evaluated_rule_ids), ACTUAL)
            self.assertEqual(len(summary.required_rule_ids), 33)
            self.assertEqual(len(summary.structural_reasons), 28)
            self.assertEqual(summary.modified_paths, (f.target,))
            self.assertEqual(summary.journal_id, result.journal)
            self.assertEqual(len(summary.applied_edits), 1)
            edit = summary.applied_edits[0]
            start = ROOT.index(b'\nthis ') + 1
            self.assertEqual((edit.path, edit.start, edit.end, edit.replacement, edit.rule_id),
                             (f.target, start, start + 1, b'T', CAP))
        self.assertEqual(result.modified, (f.target,))
        self.assertFalse(result.unresolved_findings)
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertEqual([i for i, (a, b) in enumerate(zip(ROOT, EXPECTED)) if a != b], [start])
        self.assertEqual(tuple(state(p) for p in evidence(f)), originals)
        self.assertIn(('after_replace', f.target), points)
        manifest = json.loads(next(f.store.glob('root-*/*/manifest.json')).read_bytes())
        self.assertEqual(manifest['schema'], 1)
        self.assertEqual([record['path'] for record in manifest['files']], [f.target])
        self.assertEqual({record['path'] for record in manifest['evidence']},
                         {str(p) for p in evidence(f)[:-1]})
        self.assertEqual(base64.b64decode(manifest['files'][0]['after']), EXPECTED)

    def test_every_allowlisted_starter_gets_only_its_first_ascii_byte_uppercase(self):
        for word in sorted(capitalization.WORDS):
            with self.subTest(word=word):
                source = PREFIX + b'\n#paragraph_tab\n' + word + b' keeps iPhone eBay NASA lowerCase.\n'
                start = source.index(b'\n' + word + b' ') + 1
                expected = source[:start] + word[:1].upper() + source[start + 1:]
                f = self.fixture(source=source)
                self.apply(f)
                self.assertEqual(f.source.read_bytes(), expected)

    def test_multiple_marked_paragraphs_initial_unmarked_prose_and_clean_inline_math(self):
        source = (PREFIX + b'Initial prose preserves lowerCase.\n\n#paragraph_tab\n'
                  b'this has $x_(1) + y^(2)$ and iPhone.\n\n#paragraph_tab\n'
                  b'These are already clean.\n\n#paragraph_tab\n'
                  b'we keep eBay in the middle.\n')
        expected = source.replace(b'\nthis ', b'\nThis ').replace(b'\nwe ', b'\nWe ')
        f = self.fixture(source=source)
        with self.context(f) as fix:
            candidate = fix.validate()
            self.assertEqual(candidate.sources[f.target], expected)
            fix.apply(candidate)
            self.assertEqual(len(fix.summary.applied_edits), 2)
        self.assertEqual(f.source.read_bytes(), expected)

    def test_lf_crlf_bom_and_horizontal_space_are_preserved_byte_for_byte(self):
        for newline in (b'\n', b'\r\n'):
            for bom in (b'', b'\xef\xbb\xbf'):
                with self.subTest(newline=newline, bom=bom):
                    source = bom + (PREFIX + b' \t\n \t#paragraph_tab \t\n\t this preserves eBay.\n').replace(b'\n', newline)
                    expected = source.replace(b'this preserves', b'This preserves')
                    f = self.fixture(source=source, target='chapters/main.typ')
                    self.apply(f)
                    self.assertEqual(f.source.read_bytes(), expected)

    def test_repeat_and_initial_clean_noop_keep_inode_and_do_not_create_journal(self):
        for initial in (ROOT, EXPECTED, ROOT.replace(b'this ', b'THIS ')):
            with self.subTest(initial=initial):
                f = self.fixture(source=initial)
                self.apply(f)
                before = state(f.source), tuple(state(p) for p in evidence(f))
                manifests = tuple(f.store.glob('root-*/*/manifest.json'))
                with self.context(f) as fix:
                    candidate = fix.validate()
                    self.assertEqual((candidate.passes, candidate.sources[f.target]), (0, f.source.read_bytes()))
                    result = fix.apply(candidate)
                    self.assertEqual(fix.summary.modified_paths, ())
                    self.assertEqual(fix.summary.applied_edits, ())
                self.assertEqual((result.modified, result.journal), ((), None))
                self.assertEqual((state(f.source), tuple(state(p) for p in evidence(f))), before)
                self.assertEqual(tuple(f.store.glob('root-*/*/manifest.json')), manifests)

    def test_retained_catalog_never_reads_live_paragraph_or_capitalization_metadata(self):
        with self.context() as fix, ExitStack() as stack:
            for module in ('paragraph_rules', 'capitalization', 'script_rule', 'prose_marker_rules'):
                stack.enter_context(mock.patch(module + '.load_rules', side_effect=AssertionError('live rule catalog read')))
            stack.enter_context(mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('ambient byte read')))
            candidate = fix.validate()
            self.assertEqual(candidate.sources['main.typ'], EXPECTED)

    def test_unsupported_grammar_and_other_required_repairs_never_write(self):
        variants = (
            b'', PREFIX + b'Initial prose only.\n',
            ROOT.replace(b'paragraph_tab, apply-paragraph-tabs', b'paragraph_tab as tab, apply-paragraph-tabs'),
            ROOT.replace(b'#paragraph_tab\n', b'#paragraph_tab()\n'),
            ROOT.replace(b'#show: apply-paragraph-tabs', b'#show: text'),
            ROOT.replace(b'#show: apply-paragraph-tabs\n', b''),
            ROOT.replace(b'#show: apply-paragraph-tabs\n', b'#show: apply-paragraph-tabs\n#set text(lang:"en")\n'),
            ROOT.replace(b'\n#paragraph_tab', b'\n// comment\n#paragraph_tab'),
            ROOT.replace(b'\n#paragraph_tab\n', b'\n#paragraph_tab\n\n'),
            ROOT.replace(b'this preserves', b'$x_(1)$ this preserves'),
            ROOT.replace(b'this preserves', b'*this* preserves'),
            ROOT.replace(b'this preserves', b'= this preserves'),
            ROOT.replace(b'this preserves', b'"this" preserves'),
            ROOT.replace(b'this preserves', b'tHiS preserves'),
            ROOT.replace(b'this preserves', b'unknown preserves'),
            ROOT.replace(b'this preserves', b'i preserves'),
            ROOT.replace(b'this preserves', '이것 preserves'.encode()),
            ROOT.replace(b'this preserves', b'this, preserves'),
            ROOT.replace(b'this preserves', b'this\npreserves'),
            ROOT.replace(b'this preserves', b'this $x_1$ preserves'),
            ROOT + b'\nLater unmarked paragraph.\n',
            ROOT + b'#read("never")\n', ROOT + b'// typst-lint: disable\n',
            ROOT + b'#let alias = paragraph_tab\n', ROOT + b'#include "never.typ"\n',
            ROOT + b'#{broken',
        )
        for source in variants:
            with self.subTest(source=source):
                f = self.fixture(source=source)
                with self.assertRaises((lint.InputError, TransactionError)):
                    with self.context(f) as fix:
                        fix.validate()
                self.assertEqual(f.source.read_bytes(), source)
                self.no_manifest(f)

    def test_exact_config_package_and_catalog_required_not_matching_names(self):
        changes = (
            ('config', b'rule-set="core"\ninclude=["main.typ"]\n'),
            ('config', CONFIG.replace(b'language="en"', b'language="en-US"')),
            ('config', CONFIG.replace(b'language="en"', b'language="ko"')),
            ('config', CONFIG + b'unknown=true\n'),
            ('config', CONFIG.replace(b'include=["main.typ"]', b'include=["*.typ"]')),
            ('manifest', self.f.manifest.read_bytes() + b'\n'),
            ('package', self.f.package.read_bytes() + b'\n'),
            ('catalog', self.f.catalog.read_bytes() + b'\n'),
        )
        for role, raw in changes:
            with self.subTest(role=role, raw=raw[:80]):
                f = self.fixture()
                path = getattr(f, role)
                path.chmod(0o640)
                path.write_bytes(raw)
                with self.assertRaises((lint.InputError, TransactionError)):
                    with self.context(f) as fix:
                        fix.validate()
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.assertEqual(path.read_bytes(), raw)
                self.no_manifest(f)

    def test_missing_initial_inputs_refuse_without_creation(self):
        for role in ('source', 'config', 'catalog', 'manifest', 'package'):
            with self.subTest(role=role):
                f = self.fixture()
                getattr(f, role).unlink()
                with self.assertRaises((lint.InputError, TransactionError)):
                    with self.context(f) as fix:
                        fix.validate()
                self.assertFalse(getattr(f, role).exists())
                self.no_manifest(f)

    def test_paragraph_offline_profile_stays_out_of_single_source_registry(self):
        self.assertTrue(source_profiles.is_offline_source_profile(core.SOURCE_PROFILE))
        with self.assertRaises(ValueError):
            source_profiles.grammar_for_source_profile(core.SOURCE_PROFILE)
        self.assertNotIn(core.SOURCE_PROFILE, source_profiles.KNOWN_SOURCE_PROFILES)
        self.assertIn(core.SOURCE_PROFILE, source_profiles.KNOWN_OFFLINE_SOURCE_PROFILES)
        with self.context() as fix:
            candidate = fix.validate()
            self.assertFalse(hasattr(fix, 'committed_graph_snapshot'))
            with self.assertRaises(TransactionError):
                fix.committed_snapshot()
            self.assertIsNot(candidate, fix._capture)

    def test_copied_bridge_cannot_close_original_session_or_acquire_its_authority(self):
        with self.context() as fix:
            candidate = fix.validate()
            copied = copy.copy(fix)
            with self.assertRaises(TransactionError):
                copied.__exit__(None, None, None)
            with self.assertRaises(TransactionError):
                copied.apply(candidate)
            self.assertTrue(fix._session._active)
            self.assertTrue(fix._tx._active)
            fix.apply(candidate)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)

    def test_initial_proof_authority_failure_is_safety_not_unsupported_input(self):
        # Initial admission now has its own repair grammar and verifier.
        with mock.patch.object(core, 'verify_repair_proof', side_effect=
                core.CapturedTextUtilsParagraphAuthorityError('owned verifier refusal')):
            with self.assertRaises(TransactionError) as caught:
                with self.context():
                    self.fail('Invalid proof must not open a repair session')
        self.assertNotIsInstance(caught.exception, lint.InputError)
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        self.no_manifest()
        self.assertEqual(self.apply().modified, ('main.typ',))  # Failed enter released the real lock.

    def test_ordinary_report_remains_partial_readonly_before_and_after_internal_repair(self):
        for repaired in (False, True):
            with self.subTest(repaired=repaired):
                f = self.fixture()
                if repaired:
                    self.apply(f)
                before = tuple(state(p) for p in (f.source, *evidence(f)))
                manifests = tuple(f.store.glob('root-*/*/manifest.json'))
                report = lint.report(f.root, 'fix', package_path=f.package_path)
                self.assertEqual(report['status'], 2)
                self.assertFalse(report['coverage']['complete'])
                self.assertEqual(report['proposal_status'], 'partial-not-applicable-transaction')
                self.assertFalse(report['compiler_invoked'])
                self.assertEqual(report['applied_fixes'], [])
                self.assertTrue(report['coverage']['unsupported'])
                self.assertEqual(tuple(state(p) for p in (f.source, *evidence(f))), before)
                self.assertEqual(tuple(f.store.glob('root-*/*/manifest.json')), manifests)

    def test_deleted_bridge_selection_and_session_fields_are_typed_refusals(self):
        with self.context() as fix:
            candidate = fix.validate()
            for field in ('target', 'source_profile', 'grammar_version', 'parser_contract', '_session'):
                with self.subTest(field=field):
                    original = getattr(fix, field)
                    delattr(fix, field)
                    try:
                        self.refused(fix, candidate)
                    finally:
                        setattr(fix, field, original)
            fix.apply(candidate)

    def test_no_capture_transaction_or_report_injection_constructor_parameters(self):
        for option in ('capture', 'session', 'transaction', 'proof', 'candidate'):
            with self.subTest(option=option), self.assertRaises(TypeError):
                with self.context(**{option: object()}):
                    pass
        self.no_manifest()

    def test_invalid_lease_or_widened_read_limits_refuses_before_capture(self):
        for options in ({'lease': None}, {'limits': ReadLimits(max_source_bytes=1024 * 1024 + 1)}):
            with self.subTest(options=options), self.assertRaises(TransactionError):
                with self.context(**options):
                    pass
        self.no_manifest()

    def test_opaque_candidate_copy_foreign_expiry_token_and_double_use_refuse(self):
        other_f = self.fixture()
        with self.context() as fix, self.context(other_f) as other:
            candidate, foreign = fix.validate(), other.validate()
            with self.assertRaises(TypeError):
                type(candidate)()
            with self.assertRaises(TypeError):
                candidate.sources['other.typ'] = b'forged'
            with self.assertRaises(FrozenInstanceError):
                candidate.passes = 0
            for value in (None, copy.copy(candidate), foreign, candidate._token,
                          fix._capture, fix._capture.report, candidate.sources):
                with self.subTest(value=type(value)):
                    self.refused(fix, value)
            fix.apply(candidate)
            with self.assertRaises(TransactionError):
                fix.apply(candidate)
        with self.assertRaises(TransactionError):
            fix.apply(candidate)
        self.assertEqual(other_f.source.read_bytes(), ROOT)

    def test_nested_candidate_and_capture_proof_mutation_refuse_before_write(self):
        with self.context() as fix:
            candidate = fix.validate()
            v = fix._validation
            variants = (
                (candidate, 'sources', MappingProxyType(dict(candidate.sources))),
                (candidate._token, 'sources', MappingProxyType(dict(candidate.sources))),
                (candidate, 'passes', True), (candidate._token, 'passes', True),
                (candidate, 'findings', frozenset(('forged',))),
                (candidate, '_owner', object()), (candidate, '_seal', object()),
                (v.replay, 'content', b'other'), (v.replay, 'baseline', b'other'),
                (v.replay, 'passes', True), (v.replay, 'steps', ()),
                (v.replay.final_tree, 'source', b'other'),
                (v.replay.final_tree.nodes[0], 'errors', 0),
                (v, 'proof', copy.copy(v.proof)),
                (v.summary, 'final_sha256', '0' * 64),
                (fix._capture.report, 'target', 'other.typ'),
                (fix._capture.tree, 'source', ROOT + b'\n'),
                (fix._capture.package_tree, 'source', b'fake package'),
                (fix._capture, 'initializer', copy.copy(fix._capture.initializer)),
            )
            for obj, name, value in variants:
                with self.subTest(record=type(obj).__name__, field=name), changed(obj, name, value):
                    self.refused(fix, candidate)
            fix.apply(candidate)

    def test_deleted_proof_source_and_candidate_fields_are_typed_refusals(self):
        for record, field in (('candidate', 'sources'), ('replay', 'final_tree'),
                              ('tree', 'source'), ('node', 'errors'), ('proof', 'source_sha256')):
            with self.subTest(record=record):
                f = self.fixture()
                with self.context(f) as fix:
                    candidate = fix.validate()
                    objects = dict(candidate=candidate, replay=fix._validation.replay,
                        tree=fix._validation.replay.final_tree,
                        node=fix._validation.replay.final_tree.nodes[0], proof=fix._validation.proof)
                    object.__delattr__(objects[record], field)
                    self.refused(fix, candidate, f)

    def test_direct_transaction_apply_never_creates_successful_bridge_summary(self):
        with self.context() as fix:
            candidate = fix.validate()
            result = fix._tx.apply(candidate._token)
            self.assertEqual(result.modified, ('main.typ',))
            with self.assertRaises(TransactionError):
                _ = fix.summary
            with self.assertRaises(TransactionError):
                fix.apply(candidate)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)

    def test_all_five_actual_rule_results_and_full_coverage_are_required(self):
        for hook in ('paragraph', 'script', 'boundary'):
            for alteration in ('skips', 'suppressed', 'parse_errors', 'missing', 'other_edit'):
                with self.subTest(hook=hook, alteration=alteration):
                    f = self.fixture()
                    with self.context(f) as fix:
                        actual = {'paragraph': bridge.analyze_paragraphs,
                                  'script': bridge.analyze_script,
                                  'boundary': bridge.analyze_bounded_paragraphs}[hook]
                        def lie(*args, **kwargs):
                            result = actual(*args, **kwargs)
                            # Root prose-marker coverage now comes from the
                            # real retained-metadata bounded rule backend.
                            value = (result[0][bridge.PROSE_MARKER] if hook == 'boundary'
                                     else result[CAP] if hook == 'paragraph' else result)
                            if alteration == 'skips':
                                altered = replace(value, skips=('unchecked actual rule',))
                            elif alteration == 'suppressed':
                                altered = replace(value, suppressed=(object(),))
                            elif alteration == 'parse_errors':
                                altered = replace(value, parse_errors=((0, 1),))
                            elif alteration == 'missing':
                                if hook == 'paragraph':
                                    return {rid: item for rid, item in result.items() if rid != CAP}
                                if hook == 'boundary':
                                    return ({rid: item for rid, item in result[0].items()
                                             if rid != bridge.PROSE_MARKER}, result[1], result[2])
                                return None
                            else:
                                altered = replace(value, edits=(Edit(0, 0, b'FORGED'),))
                            if hook == 'boundary':
                                return (dict(result[0], **{bridge.PROSE_MARKER: altered}), result[1], result[2])
                            return dict(result, **{CAP: altered}) if hook == 'paragraph' else altered
                        owner, name = (bridge, 'analyze_bounded_paragraphs') if hook == 'boundary' else (
                            bridge, 'analyze_paragraphs' if hook == 'paragraph' else 'analyze_script')
                        with mock.patch.object(owner, name, side_effect=lie):
                            with self.assertRaises(TransactionError):
                                fix.validate()
                    self.assertEqual(f.source.read_bytes(), ROOT)
                    self.no_manifest(f)

    def test_wrong_missing_extra_overlapping_and_second_pass_actual_edits_refuse(self):
        for alteration in ('diagnostic', 'missing', 'wrong_byte', 'wrong_location',
                           'extra', 'overlap', 'second_pass'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with self.context(f) as fix:
                    actual = bridge.analyze_paragraphs
                    def lie(tree, *args, **kwargs):
                        result = actual(tree, *args, **kwargs)
                        value = result[CAP]
                        if alteration == 'second_pass':
                            if not value.edits:
                                return dict(result, **{CAP: replace(value,
                                    edits=(Edit(len(PREFIX), len(PREFIX) + 1, b'X'),))})
                            return result
                        if not value.edits:
                            return result
                        edit = value.edits[0]
                        if alteration == 'diagnostic':
                            changed_value = replace(value, diagnostics=())
                        elif alteration == 'missing':
                            changed_value = replace(value, edits=())
                        elif alteration == 'wrong_byte':
                            changed_value = replace(value, edits=(replace(edit, replacement=b'Z'),))
                        elif alteration == 'wrong_location':
                            changed_value = replace(value, edits=(replace(edit,
                                start=edit.start + 1, end=edit.end + 1),))
                        elif alteration == 'extra':
                            changed_value = replace(value, edits=value.edits + (Edit(0, 0, b'X'),))
                        else:
                            changed_value = replace(value, edits=value.edits + (edit,))
                        return dict(result, **{CAP: changed_value})
                    with mock.patch.object(bridge, 'analyze_paragraphs', side_effect=lie):
                        with self.assertRaises(TransactionError):
                            fix.validate()
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest(f)

    def test_incomplete_graph_coverage_findings_evidence_and_foreign_edits_refuse(self):
        for alteration in ('implemented', 'evaluated', 'parsed', 'evidence', 'findings', 'foreign_edit'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with self.context(f) as fix:
                    actual = fix._analyze_graph
                    def lie(graph):
                        result = actual(graph)
                        if alteration == 'implemented':
                            return replace(result, implemented_rules=ACTUAL)
                        if alteration == 'evaluated':
                            return replace(result, evaluated_rules=ACTUAL)
                        if alteration == 'parsed':
                            return replace(result, parsed_sources=frozenset())
                        if alteration == 'evidence':
                            return replace(result, examined_evidence=frozenset())
                        if alteration == 'findings':
                            return replace(result, findings=frozenset(('forged finding',)))
                        return replace(result, edits=result.edits + (
                            SourceEdit('local/text-utils/0.1.2/lib.typ', Edit(0, 0, b'X')),))
                    with mock.patch.object(fix, '_analyze_graph', side_effect=lie):
                        with self.assertRaises(TransactionError):
                            fix.validate()
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest(f)

    def test_callback_requires_exact_membership_root_and_evidence_byte_objects(self):
        with self.context() as fix:
            graph = fix._tx.snapshot
            variants = (
                replace(graph, sources=MappingProxyType({})),
                replace(graph, sources=dict(graph.sources)),
                replace(graph, sources=MappingProxyType({**graph.sources, 'hidden.typ': b''})),
                replace(graph, evidence=MappingProxyType({})),
                replace(graph, evidence=MappingProxyType({**graph.evidence, 'hidden': b''})),
                replace(graph, evidence=MappingProxyType({path: bytes(bytearray(raw))
                    for path, raw in graph.evidence.items()})),
                replace(graph, root=Path(str(graph.root))),
            )
            for variant in variants:
                with self.subTest(variant=variant), self.assertRaises(TransactionError):
                    fix._analyze_graph(variant)
        self.no_manifest()

    def test_independent_replay_bytes_pass_results_and_attribution_must_agree(self):
        for alteration in ('content', 'passes', 'initial_results', 'final_results', 'attribution',
                           'top_edits_empty', 'top_edit_rule', 'contribution_rule', 'contribution_pass_bool'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with self.context(f) as fix:
                    replay = fix._independent_replay()
                    if alteration == 'content':
                        replay = replace(replay, content=replay.content.replace(b'iPhone', b'iphone'))
                    elif alteration == 'passes':
                        replay = replace(replay, passes=0)
                    elif alteration == 'initial_results':
                        replay = replace(replay, initial_results=MappingProxyType({}))
                    elif alteration == 'final_results':
                        replay = replace(replay, final_results=MappingProxyType({}))
                    elif alteration == 'attribution':
                        replay = replace(replay, steps=(replace(replay.steps[0], applied=()),))
                    elif alteration == 'top_edits_empty':
                        replay = replace(replay, edits=())
                    elif alteration == 'top_edit_rule':
                        replay = replace(replay, edits=(replace(replay.edits[0],
                            primary_rule_id='core.parenthesized-scripts'),))
                    else:
                        item = replay.edits[0]
                        contribution = item.contributions[0]
                        contribution = replace(contribution, **({'rule_id': 'core.parenthesized-scripts'}
                            if alteration == 'contribution_rule' else {'pass_number': True}))
                        replay = replace(replay, edits=(replace(item, contributions=(contribution,)),))
                    with mock.patch.object(fix, '_independent_replay', return_value=replay):
                        with self.assertRaises(TransactionError):
                            fix.validate()
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest(f)

    def test_genuine_same_transaction_revalidation_and_token_substitution_grant_no_bridge_authority(self):
        with self.context() as fix:
            first = fix.validate()
            second = fix.validate()
            self.refused(fix, first)
            token = fix._tx.validate(fix._analyze_graph, required_rules=bridge.REQUIRED_RULE_IDS)
            self.assertIs(type(token), type(second._token))
            self.refused(fix, token)
            with changed(second, '_token', token), changed(second, 'sources', token.sources):
                self.refused(fix, second)
            self.refused(fix, second)  # The tx no longer recognizes its older issued token.

    def test_preissuance_cycle_growth_and_history_record_substitution_are_bounded_refusals(self):
        for alteration in ('proof_cycle', 'proof_growth', 'context_growth', 'copied_history_record',
                           'proof_copy', 'results_copy', 'analysis_change', 'returned_tree_cycle'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with self.context(f) as fix:
                    actual = fix._independent_replay
                    def mutate_after_replay():
                        replay = actual()
                        first = fix._history[0]
                        if alteration == 'proof_cycle':
                            object.__setattr__(first.proof, 'paragraphs', (first.proof,))
                        elif alteration == 'proof_growth':
                            object.__setattr__(first.proof, 'paragraphs', first.proof.paragraphs * 100_000)
                        elif alteration == 'context_growth':
                            object.__setattr__(first, 'contexts', first.contexts * 100_000)
                        elif alteration == 'copied_history_record':
                            fix._history[0] = replace(first)
                        elif alteration == 'proof_copy':
                            object.__setattr__(first, 'proof', copy.copy(first.proof))
                        elif alteration == 'results_copy':
                            object.__setattr__(first, 'results', MappingProxyType(dict(first.results)))
                        elif alteration == 'analysis_change':
                            object.__setattr__(first, 'analysis', replace(first.analysis,
                                findings=frozenset(('forged finding',))))
                        else:
                            object.__setattr__(replay.final_tree, 'nodes', (replay.final_tree,))
                        return replay
                    with mock.patch.object(fix, '_independent_replay', side_effect=mutate_after_replay):
                        with self.assertRaises(TransactionError):
                            fix.validate()
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest(f)

    def test_same_bytes_new_inode_mode_bytes_and_xattr_drift_before_validate_and_apply(self):
        for when in ('validate', 'apply'):
            for role in ('source', 'config', 'catalog', 'manifest', 'package'):
                for operation in ('bytes', 'inode', 'mode', 'xattr'):
                    with self.subTest(when=when, role=role, operation=operation):
                        f = self.fixture()
                        with self.context(f) as fix:
                            candidate = fix.validate() if when == 'apply' else None
                            path = getattr(f, role)
                            if operation == 'bytes':
                                path.chmod(0o640)
                                path.write_bytes(path.read_bytes() + b'\n')
                            elif operation == 'inode':
                                same_byte_replace(path)
                            elif operation == 'mode':
                                path.chmod(stat.S_IMODE(path.stat().st_mode) ^ 0o040)
                            else:
                                set_fixture_xattr(path, b'test-owned-drift')
                            current = tuple(state(p) for p in (f.source, *evidence(f)))
                            with self.assertRaises(TransactionError):
                                fix.apply(candidate) if when == 'apply' else fix.validate()
                        self.assertEqual(tuple(state(p) for p in (f.source, *evidence(f))), current)
                        self.no_manifest(f)

    def test_transient_ancestor_replacement_with_unchanged_immediate_parent_refuses(self):
        for when in ('validate', 'apply'):
            for role in ('source', 'package', 'catalog'):
                with self.subTest(when=when, role=role):
                    f = self.fixture(target='doc/main.typ')
                    if role == 'catalog':
                        directory = f.base / 'catalog-parent' / 'sub'
                        directory.mkdir(parents=True)
                        f.catalog.rename(directory / 'catalog.json')
                        f.catalog = directory / 'catalog.json'
                    with self.context(f) as fix:
                        candidate = fix.validate() if when == 'apply' else None
                        if role == 'source':
                            ancestor, child = f.base, 'consumer'
                        elif role == 'package':
                            ancestor, child = f.package_path / 'local', 'text-utils'
                        else:
                            ancestor, child = f.base / 'catalog-parent', 'sub'
                        old = ancestor.with_name(ancestor.name + '-old')
                        inode = (ancestor / child).stat().st_ino
                        ancestor.rename(old)
                        ancestor.mkdir()
                        (old / child).rename(ancestor / child)
                        if role == 'source':
                            for remaining in tuple(old.iterdir()):
                                remaining.rename(ancestor / remaining.name)
                        self.assertEqual((ancestor / child).stat().st_ino, inode)
                        with self.assertRaises(TransactionError):
                            fix.apply(candidate) if when == 'apply' else fix.validate()
                    self.assertEqual(f.source.read_bytes(), ROOT)

    def test_input_symlink_hardlink_and_storage_aliases_refuse(self):
        for role in ('source', 'config', 'catalog', 'manifest', 'package'):
            for alias in ('symlink', 'hardlink'):
                with self.subTest(role=role, alias=alias):
                    f = self.fixture()
                    path = getattr(f, role)
                    if alias == 'symlink':
                        real = path.with_name(path.name + '.real')
                        path.rename(real)
                        path.symlink_to(real)
                    else:
                        os.link(path, path.with_name(path.name + '.second-link'))
                    with self.assertRaises(TransactionError):
                        with self.context(f) as fix:
                            fix.validate()
                    self.assertEqual(f.source.read_bytes(), ROOT)
                    self.no_manifest(f)
        f = self.fixture()
        for store in (f.root, f.root / 'nested-store', f.package.parent):
            with self.subTest(store=store), self.assertRaises(TransactionError):
                with self.context(f, journal_directory=store):
                    pass

    def test_drift_during_staging_and_before_replace_preserves_readonly_evidence(self):
        for boundary in (('stage_create', 'main.typ'), ('before_replace', 'main.typ')):
            for role in ('source', 'config', 'catalog', 'manifest', 'package'):
                with self.subTest(boundary=boundary, role=role):
                    f = self.fixture()
                    reached = []
                    def mutate(point, path):
                        if (point, path) == boundary:
                            reached.append(True)
                            target = getattr(f, role)
                            target.chmod(0o640)
                            target.write_bytes(target.read_bytes() + b'\nUSER-OWNED-LATER\n')
                    before = {p: state(p) for p in evidence(f)}
                    with self.context(f, fault=mutate) as fix:
                        candidate = fix.validate()
                        with self.assertRaises(TransactionError):
                            fix.apply(candidate)
                        with self.assertRaises(TransactionError):
                            _ = fix.summary
                    self.assertTrue(reached)
                    if role == 'source':
                        self.assertIn(b'USER-OWNED-LATER', f.source.read_bytes())
                    else:
                        self.assertEqual(f.source.read_bytes(), ROOT)
                        self.assertIn(b'USER-OWNED-LATER', getattr(f, role).read_bytes())
                    for path, value in before.items():
                        if path != getattr(f, role):
                            self.assertEqual(state(path), value)

    def test_mutated_private_token_during_apply_cannot_change_journal_or_postimage(self):
        for boundary in (('journal_parent_fsync', None), ('stage_create', 'main.typ'),
                         ('before_replace', 'main.typ')):
            with self.subTest(boundary=boundary):
                f = self.fixture()
                holder, reached = {}, []
                def mutate(point, path):
                    if point == boundary[0] and (boundary[1] is None or path == boundary[1]):
                        reached.append(True)
                        object.__setattr__(holder['candidate']._token, 'sources',
                            MappingProxyType({'main.typ': b'UNPROVED POSTIMAGE\n'}))
                with self.context(f, fault=mutate) as fix:
                    candidate = fix.validate()
                    holder['candidate'] = candidate
                    try:
                        fix.apply(candidate)
                    except TransactionError:
                        pass
                    self.assertTrue(reached)
                    self.assertIn(f.source.read_bytes(), (ROOT, EXPECTED))
                    if f.source.read_bytes() == EXPECTED:
                        self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
                for path in f.store.glob('root-*/*/manifest.json'):
                    manifest = json.loads(path.read_bytes())
                    self.assertEqual(base64.b64decode(manifest['files'][0]['after']), EXPECTED)

    def test_post_apply_filesystem_drift_does_not_hide_true_source_commit(self):
        actual = ProtectedTransaction.apply
        def apply_then_drift(tx, token):
            result = actual(tx, token)
            self.f.package.chmod(0o640)
            self.f.package.write_bytes(self.f.package.read_bytes() + b'\nLATER\n')
            return result
        with self.context() as fix, mock.patch.object(ProtectedTransaction, 'apply', new=apply_then_drift):
            candidate = fix.validate()
            try:
                fix.apply(candidate)
            except TransactionError:
                pass
            self.assertEqual(fix.summary.modified_paths, ('main.typ',))
            self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)

    def test_success_summary_remains_truthful_after_report_mutation_and_context_close(self):
        with self.context() as fix:
            candidate = fix.validate()
            fix.apply(candidate)
            first = fix.summary
            original_edits = tuple((e.path, e.start, e.end, e.replacement, e.rule_id)
                                   for e in first.applied_edits)
            object.__setattr__(first, 'final_sha256', '0' * 64)
            object.__setattr__(first.applied_edits[0], 'replacement', b'FORGED')
            self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
            self.assertEqual(tuple((e.path, e.start, e.end, e.replacement, e.rule_id)
                                   for e in fix.summary.applied_edits), original_edits)
        self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))

    def test_mid_apply_summary_mutation_preserves_true_commit_even_if_later_guard_refuses(self):
        for field in ('hash', 'edit'):
            with self.subTest(field=field):
                f = self.fixture()
                holder = {}
                def mutate(point, path):
                    if (point, path) == ('before_final_validation', None):
                        summary = holder['fix']._validation.summary
                        if field == 'hash':
                            object.__setattr__(summary, 'final_sha256', '0' * 64)
                        else:
                            object.__setattr__(summary.applied_edits[0], 'replacement', b'FORGED')
                with self.context(f, fault=mutate) as fix:
                    holder['fix'] = fix
                    candidate = fix.validate()
                    try:
                        fix.apply(candidate)
                    except TransactionError:
                        pass
                    self.assertEqual(f.source.read_bytes(), EXPECTED)
                    self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
                    self.assertEqual(fix.summary.applied_edits[0].replacement, b'T')
                    self.assertEqual(fix.summary.modified_paths, ('main.typ',))

    def test_existing_failure_phase_recovery_changes_only_root(self):
        for boundary in (('stage_create', 'main.typ'), ('before_replace', 'main.typ'),
                         ('after_replace', 'main.typ'), ('before_final_validation', None)):
            with self.subTest(boundary=boundary):
                f = self.fixture()
                before = tuple(state(p) for p in evidence(f))
                reached = []
                def fail(point, path):
                    if (point, path) == boundary:
                        reached.append(True)
                        raise OSError('owned paragraph test failure')
                with self.assertRaises(TransactionFailure) as caught:
                    self.apply(f, fault=fail)
                self.assertTrue(reached)
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.assertEqual(tuple(state(p) for p in evidence(f)), before)
                with ProtectedTransaction(f.root, (f.target,), journal_directory=f.store,
                        lease=f.lease, recovery_only=True) as tx:
                    self.assertTrue(tx.recover(caught.exception.report.journal).complete)
                self.assertEqual(self.apply(f).modified, (f.target,))

    def test_keyboard_interrupt_after_rename_needs_existing_recovery_and_no_success_summary(self):
        f = self.f
        before = tuple(state(p) for p in evidence(f))
        def interrupt(point, path):
            if (point, path) == ('after_replace', f.target):
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                raise KeyboardInterrupt('test-owned interruption')
        with self.context(fault=interrupt) as fix:
            with self.assertRaises((KeyboardInterrupt, TransactionFailure)):
                fix.apply(fix.validate())
            with self.assertRaises(TransactionError):
                _ = fix.summary
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertEqual(tuple(state(p) for p in evidence(f)), before)
        journal = next(f.store.glob('root-*/*/manifest.json')).parent.name
        with self.assertRaises(RecoveryRequired):
            self.apply(f)
        with ProtectedTransaction(f.root, (f.target,), journal_directory=f.store,
                lease=f.lease, recovery_only=True) as tx:
            report = tx.recover(journal)
            self.assertTrue(report.complete)
            self.assertEqual(report.restored, (f.target,))
        self.assertEqual(f.source.read_bytes(), ROOT)

    def request(self, action, **kwargs):
        f = self.f
        return dict(root=str(f.root), target=f.target, store=str(f.store),
                    catalog=str(f.catalog), package_path=str(f.package_path), action=action, **kwargs)

    def worker(self, action, **kwargs):
        result = subprocess.run([sys.executable, str(WORKER), json.dumps(self.request(action, **kwargs))],
            capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def kill_worker(self, action, boundary, **kwargs):
        child = subprocess.Popen([sys.executable, str(WORKER),
            json.dumps(self.request(action, boundary=boundary, **kwargs))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=30), 'Owned worker did not reach barrier')
                line = child.stdout.readline()
            if not line:
                _, error = child.communicate(timeout=5)
                self.fail('Owned worker exited before barrier: ' + error)
            self.assertEqual(json.loads(line), {'paused': list(boundary)})
            child.kill()  # Only this exact owned PID; deliberately no Python unwinding.
            _, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGKILL, error)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.assertIsNotNone(child.poll(), 'Owned worker must be reaped')

    def pending(self):
        result = self.worker('probe')
        self.assertEqual(set(result), {'pending'}, result)
        self.assertEqual(len(result['pending']), 1)
        return result['pending'][0]

    def test_sigkill_before_rename_recovery_apply_then_noop_restart(self):
        original_evidence = tuple(state(p) for p in evidence(self.f))
        self.kill_worker('apply', ('before_replace', 'main.typ'))
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        result = self.worker('recover', journal=self.pending())
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['unchanged'], ['main.typ'])
        result = self.worker('apply')
        self.assertEqual((result['passes'], result['modified']), (1, ['main.typ']))
        self.assertEqual(result['final_sha256'], lint.digest(EXPECTED))
        fixed = state(self.f.source)
        self.assertEqual(self.worker('apply')['modified'], [])
        self.assertEqual(state(self.f.source), fixed)
        self.assertEqual(tuple(state(p) for p in evidence(self.f)), original_evidence)

    def test_sigkill_after_rename_and_restore_recovery_does_not_need_live_package(self):
        self.kill_worker('apply', ('after_replace', 'main.typ'))
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        journal = self.pending()
        for path in evidence(self.f)[:-1]:
            path.unlink()
        self.kill_worker('recover', ('after_restore', 'main.typ'), journal=journal)
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        restored = state(self.f.source)
        result = self.worker('recover', journal=journal)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['unchanged'], ['main.typ'])
        self.assertEqual(state(self.f.source), restored)
        self.assertEqual(self.worker('probe'), {'open': True})
        self.assertTrue(all(not p.exists() for p in evidence(self.f)[:-1]))

    def test_sigkill_recovery_preserves_later_user_edits_and_stale_acknowledgment(self):
        original_evidence = tuple(state(p) for p in evidence(self.f))
        self.kill_worker('apply', ('after_replace', 'main.typ'))
        journal = self.pending()
        self.f.source.write_bytes(b'Later user work.\n')
        saved = state(self.f.source)
        result = self.worker('recover', journal=journal)
        self.assertFalse(result['complete'])
        self.assertEqual(result['changed'], ['main.typ'])
        self.assertEqual(state(self.f.source), saved)
        self.f.source.write_bytes(b'Even later user work.\n')
        latest = state(self.f.source)
        stale = self.worker('recover', journal=journal, preserve=result['conflicts'])
        self.assertFalse(stale['complete'])
        self.assertEqual(state(self.f.source), latest)
        final = self.worker('recover', journal=journal, preserve=stale['conflicts'])
        self.assertTrue(final['complete'], final)
        self.assertEqual(final['preserved'], ['main.typ'])
        self.assertEqual(state(self.f.source), latest)
        self.assertEqual(tuple(state(p) for p in evidence(self.f)), original_evidence)

    def test_sigkill_postimage_same_bytes_new_inode_requires_exact_conflict_ack(self):
        self.kill_worker('apply', ('after_replace', 'main.typ'))
        journal = self.pending()
        same_byte_replace(self.f.source)
        saved = state(self.f.source)
        result = self.worker('recover', journal=journal)
        self.assertFalse(result['complete'])
        self.assertEqual(result['changed'], ['main.typ'])
        self.assertEqual(state(self.f.source), saved)
        final = self.worker('recover', journal=journal, preserve=result['conflicts'])
        self.assertTrue(final['complete'], final)
        self.assertEqual(final['preserved'], ['main.typ'])
        self.assertEqual(state(self.f.source), saved)


if __name__ == '__main__':
    unittest.main()
