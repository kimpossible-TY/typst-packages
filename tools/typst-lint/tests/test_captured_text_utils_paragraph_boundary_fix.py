"""Protected multi-pass boundary repair and adversarial replay integration.

Only private temporary consumers and the pinned syntax parser are used. These
are not generic proposal/renderer tests or native compiler/process tests.
"""
from contextlib import ExitStack
import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import capitalization
import bounded_paragraph_rules
import captured_text_utils_paragraph_core as core
import captured_text_utils_paragraph_fix as bridge
import captured_text_utils_paragraph_compile as compiler
import captured_text_utils_paragraph_inputs as inputs
import candidate_convergence
from edit_plan import Edit, plan
import package_loader
import paragraph_rules
import prose_marker_rules
from static_resolver import resolve
from syntax_client import AdapterError, EXECUTABLE, MAX_INPUT, MAX_NODES, parse
from transactions import TransactionError
import typst_lint as lint
from paragraph_fix_test_support import (PREFIX, ROOT, EXPECTED, changed, context,
                                       evidence, fixture, same_byte_replace, state)

CAP = capitalization.RULE_ID
GAP = paragraph_rules.MARKER_GAP
MARKER = prose_marker_rules.RULE_ID
ACTUAL = frozenset(core.EVALUATED_RULE_IDS)
# Both unsupported-by-old-profile boundary forms, two inserted marker owners,
# two missing gaps, pre-existing marker case work and already-clean inline math.
DIRTY = (PREFIX + b'unknown first remains exempt and keeps iPhone.\n'
         b'#paragraph_tab\nthis marked paragraph keeps eBay.\n\n'
         b'we need an inserted marker and $x_(1) + y^(2)$.\n\n'
         b'#paragraph_tab\nthese marked words keep NASA.\n'
         b'#paragraph_tab\nit needs its missing blank source line.\n\n'
         b'therefore this final paragraph needs its own marker.\n')
PASS_ONE = (PREFIX + b'unknown first remains exempt and keeps iPhone.\n\n'
            b'#paragraph_tab\nThis marked paragraph keeps eBay.\n\n'
            b'#paragraph_tab\nwe need an inserted marker and $x_(1) + y^(2)$.\n\n'
            b'#paragraph_tab\nThese marked words keep NASA.\n\n'
            b'#paragraph_tab\nIt needs its missing blank source line.\n\n'
            b'#paragraph_tab\ntherefore this final paragraph needs its own marker.\n')
FINAL = PASS_ONE.replace(b'\nwe ', b'\nWe ').replace(b'\ntherefore ', b'\nTherefore ')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX protected transactions')
class CapturedTextUtilsParagraphBoundaryFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='typst-paragraph-boundary-fix-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.sequence = 0

    def fixture(self, source=DIRTY, **kwargs):
        self.sequence += 1
        return fixture(self.base, self.sequence, source=source, **kwargs)

    def no_manifest(self, f):
        self.assertFalse(list(f.store.glob('root-*/*/manifest.json')))

    def refused(self, f, fix, candidate):
        with self.assertRaises(TransactionError):
            fix.apply(candidate)
        self.assertEqual(f.source.read_bytes(), f.original)
        self.no_manifest(f)

    def bad_replay(self, mutate):
        f = self.fixture()
        with context(f) as fix:
            actual = fix._independent_replay
            def altered():
                return mutate(actual(), fix)
            with mock.patch.object(fix, '_independent_replay', side_effect=altered):
                with self.assertRaises(TransactionError):
                    fix.validate()
        self.assertEqual(f.source.read_bytes(), DIRTY)
        self.no_manifest(f)

    def test_mixed_boundary_repairs_use_real_separate_passes_and_one_root_transaction(self):
        f = self.fixture(target='chapters/main.typ')
        before = tuple(state(p) for p in evidence(f))
        with context(f) as fix:
            self.assertIs(fix._tx, fix._session._tx)
            candidate = fix.validate()
            replay = fix._validation.replay
            self.assertEqual((candidate.passes, candidate.sources[f.target]), (2, FINAL))
            self.assertEqual(tuple(record.tree.source for record in fix._history),
                             (DIRTY, PASS_ONE, FINAL, FINAL))
            self.assertEqual(tuple((s.before, s.after) for s in replay.steps),
                             ((DIRTY, PASS_ONE), (PASS_ONE, FINAL)))
            self.assertEqual({c.rule_id for item in replay.steps[0].applied
                              for c in item.contributions}, {GAP, MARKER, CAP})
            self.assertEqual({c.rule_id for item in replay.steps[1].applied
                              for c in item.contributions}, {CAP})
            for number, step in enumerate(replay.steps, 1):
                self.assertFalse(step.moves or step.deferred)
                self.assertTrue(step.applied)
                self.assertTrue(all(c.pass_number == number and type(c.pass_number) is int
                                    for item in step.applied for c in item.contributions))
            self.assertEqual(f.source.read_bytes(), DIRTY)
            self.no_manifest(f)
            result = fix.apply(candidate)
            self.assertEqual(result.modified, (f.target,))
            self.assertEqual(fix._tx.targets, (f.target,))
            self.assertEqual(set(fix._tx.snapshot.evidence), {str(p) for p in evidence(f)[:-1]})
        self.assertEqual(f.source.read_bytes(), FINAL)
        self.assertEqual(tuple(state(p) for p in evidence(f)), before)
        manifest = json.loads(next(f.store.glob('root-*/*/manifest.json')).read_bytes())
        self.assertEqual(manifest['schema'], 1)
        self.assertEqual([item['path'] for item in manifest['files']], [f.target])

    def test_initial_zero_markers_insert_only_later_paragraphs_and_preserve_first_exemption(self):
        for first in (b'unknown first stays lowercase.', b'this first stays lowercase.'):
            for second in (b'this later becomes uppercase.', b'This later is already clean.'):
                with self.subTest(first=first, second=second):
                    source = PREFIX + first + b'\n\n' + second + b'\n\nwe keep the third.\n'
                    expected = (PREFIX + first + b'\n\n#paragraph_tab\n'
                                + second[:1].upper() + second[1:]
                                + b'\n\n#paragraph_tab\nWe keep the third.\n')
                    f = self.fixture(source)
                    with context(f) as fix:
                        candidate = fix.validate()
                        self.assertEqual(candidate.passes, 2)
                        self.assertEqual(candidate.sources[f.target], expected)
                        first_step = fix._validation.replay.steps[0]
                        self.assertEqual(first_step.after.count(b'#paragraph_tab'), 2)
                        self.assertIn(b'\n' + second + b'\n', first_step.after)
                        self.assertNotIn(CAP, {c.rule_id for a in first_step.applied for c in a.contributions})
                        fix.apply(candidate)
                    self.assertEqual(f.source.read_bytes(), expected)

    def test_uppercase_insertion_and_existing_marker_gap_need_only_one_pass(self):
        for source, expected in (
            (PREFIX + b'Initial paragraph.\n\nThis later is clean.\n',
             PREFIX + b'Initial paragraph.\n\n#paragraph_tab\nThis later is clean.\n'),
            (ROOT.replace(b'\n\n#paragraph_tab', b'\n#paragraph_tab'), EXPECTED),
            (PREFIX + b'Initial paragraph.\n#paragraph_tab\nThis stays clean.\n',
             PREFIX + b'Initial paragraph.\n\n#paragraph_tab\nThis stays clean.\n'),
            (PREFIX + b'Initial paragraph with $x_(1) + y^(2)$.\n#paragraph_tab\nthis needs a gap.\n',
             PREFIX + b'Initial paragraph with $x_(1) + y^(2)$.\n\n#paragraph_tab\nThis needs a gap.\n'),
        ):
            with self.subTest(source=source):
                f = self.fixture(source)
                with context(f) as fix:
                    candidate = fix.validate()
                    self.assertEqual((candidate.passes, candidate.sources[f.target]), (1, expected))
                    fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), expected)

    def test_lf_crlf_bom_indentation_and_blank_hspace_are_exact_not_normalized(self):
        raw = (PREFIX + b'\tInitial iPhone prose. \t\n \t#paragraph_tab \t\n'
               b'\t this marked paragraph keeps eBay.\n \t\n\t we keep $x_(1)$. \t\n')
        expected = (PREFIX + b'\tInitial iPhone prose. \t\n\n \t#paragraph_tab \t\n'
                    b'\t This marked paragraph keeps eBay.\n \t\n'
                    b'#paragraph_tab\n\t We keep $x_(1)$. \t\n')
        for newline in (b'\n', b'\r\n'):
            for bom in (b'', b'\xef\xbb\xbf'):
                with self.subTest(newline=newline, bom=bom):
                    source = bom + raw.replace(b'\n', newline)
                    final = bom + expected.replace(b'\n', newline)
                    f = self.fixture(source)
                    with context(f) as fix:
                        candidate = fix.validate()
                        self.assertEqual(candidate.sources[f.target], final)
                        insertions = [c.edit.replacement for s in fix._validation.replay.steps
                                      for a in s.applied for c in a.contributions
                                      if c.rule_id in (GAP, MARKER)]
                        self.assertCountEqual(insertions, (newline, b'#paragraph_tab' + newline))
                        fix.apply(candidate)
                    self.assertEqual(f.source.read_bytes(), final)

        mixed = (PREFIX + b'Initial prose.\r\n \t#paragraph_tab\n'
                 b'\t this marked line.\r\n \t\r\n\twe keep the final LF.\n')
        expected_mixed = (PREFIX + b'Initial prose.\r\n\r\n \t#paragraph_tab\n'
                          b'\t This marked line.\r\n \t\r\n'
                          b'#paragraph_tab\r\n\tWe keep the final LF.\n')
        f = self.fixture(mixed)
        with context(f) as fix:
            candidate = fix.validate()
            self.assertEqual(candidate.sources[f.target], expected_mixed)
            fix.apply(candidate)
        self.assertEqual(f.source.read_bytes(), expected_mixed)

    def test_every_new_allowlisted_owner_is_lowercase_until_its_later_case_pass(self):
        for word in sorted(capitalization.WORDS):
            with self.subTest(word=word):
                source = PREFIX + b'Initial paragraph.\n\n' + word + b' keeps iPhone eBay.\n'
                f = self.fixture(source)
                with context(f) as fix:
                    candidate = fix.validate()
                    replay = fix._validation.replay
                    self.assertEqual(replay.passes, 2)
                    self.assertEqual(replay.steps[0].after,
                                     source.replace(b'\n\n', b'\n\n#paragraph_tab\n', 1))
                    self.assertEqual(replay.steps[1].applied[0].contributions[0].rule_id, CAP)
                    fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), replay.content)

    def test_final_proof_is_canonical_and_repeat_is_noop_without_new_inode_or_journal(self):
        f = self.fixture()
        with context(f) as fix:
            candidate = fix.validate()
            proof = fix._validation.proof
            clean = core.certify(fix._validation.replay.final_tree, f.target, **fix._authority())
            self.assertEqual(proof.grammar_version, core.GRAMMAR_VERSION)
            self.assertEqual(proof.source_sha256, clean.source_sha256)
            self.assertEqual(len(proof.obligations), 33)
            self.assertEqual(set(proof.evaluated_rule_ids), ACTUAL)
            self.assertEqual(len(proof.structural_reasons), 28)
            fix.apply(candidate)
        before = state(f.source), tuple(state(p) for p in evidence(f))
        manifests = tuple(f.store.glob('root-*/*/manifest.json'))
        with context(f) as fix:
            candidate = fix.validate()
            self.assertEqual((candidate.passes, candidate.sources[f.target]), (0, FINAL))
            self.assertEqual(fix._validation.replay.steps, ())
            result = fix.apply(candidate)
            self.assertEqual((result.modified, result.journal), ((), None))
            self.assertEqual(fix.summary.applied_edits, ())
        self.assertEqual((state(f.source), tuple(state(p) for p in evidence(f))), before)
        self.assertEqual(tuple(f.store.glob('root-*/*/manifest.json')), manifests)

    def test_repaired_summary_preserves_original_coordinates_rule_causes_and_real_pass_numbers(self):
        f = self.fixture()
        with context(f) as fix:
            candidate = fix.validate()
            replay = fix._validation.replay
            fix.apply(candidate)
            summary = fix.summary
            self.assertEqual((summary.original_sha256, summary.final_sha256),
                             (lint.digest(DIRTY), lint.digest(FINAL)))
            self.assertEqual(summary.grammar_version, core.GRAMMAR_VERSION)
            self.assertEqual(summary.repair_passes, 2)
            self.assertNotEqual(summary.repair_grammar_version, summary.grammar_version)
            self.assertEqual(set(summary.evaluated_rule_ids), ACTUAL)
            self.assertEqual(len(summary.required_rule_ids), 33)
            self.assertEqual(len(summary.structural_reasons), 28)
            self.assertEqual(tuple((e.start, e.end, e.replacement) for e in summary.applied_edits),
                             tuple((a.edit.start, a.edit.end, a.edit.replacement) for a in replay.edits))
            for reported, actual in zip(summary.applied_edits, replay.edits):
                self.assertEqual(reported.path, f.target)
                self.assertEqual(reported.rule_id, actual.primary_rule_id)
                self.assertEqual(reported.rule_ids, actual.rule_ids)
                self.assertEqual(tuple((c.pass_number, c.rule_id, c.start, c.end, c.replacement)
                                       for c in reported.contributions),
                                 tuple((c.pass_number, c.rule_id, c.edit.start, c.edit.end, c.edit.replacement)
                                       for c in actual.contributions))
            self.assertEqual(plan(DIRTY, tuple(Edit(e.start, e.end, e.replacement)
                                              for e in summary.applied_edits)).candidate(), FINAL)
            self.assertEqual({c.pass_number for e in summary.applied_edits for c in e.contributions}, {1, 2})
            original = tuple(summary.applied_edits)
            object.__setattr__(summary, 'repair_grammar_version', 'forged')
            object.__setattr__(summary.applied_edits[0], 'contributions', ())
            restored = fix.summary
            self.assertNotEqual(restored.repair_grammar_version, 'forged')
            self.assertTrue(all(e.contributions for e in restored.applied_edits))
            self.assertEqual(len(restored.applied_edits), len(original))
        self.assertEqual(fix.summary.final_sha256, lint.digest(FINAL))

    def test_returned_replay_cannot_drop_or_reorder_real_second_pass_or_history(self):
        mutations = (
            lambda r, f: replace(r, passes=1),
            lambda r, f: replace(r, passes=True),
            lambda r, f: replace(r, steps=r.steps[:1]),
            lambda r, f: replace(r, steps=tuple(reversed(r.steps))),
            lambda r, f: replace(r, steps=(r.steps[0], r.steps[0])),
            lambda r, f: replace(r, steps=(replace(r.steps[0], after=r.content), r.steps[1])),
            lambda r, f: replace(r, steps=(r.steps[0], replace(r.steps[1], before=r.baseline))),
            lambda r, f: replace(r, content=PASS_ONE),
            lambda r, f: replace(r, initial_results=MappingProxyType({})),
            lambda r, f: replace(r, final_results=r.initial_results),
        )
        for number, mutate in enumerate(mutations):
            with self.subTest(mutation=number):
                self.bad_replay(mutate)

    def test_dropped_forged_rule_pass_or_edit_contributions_fail_independent_replay(self):
        def mutate(r, field, location):
            if location == 'top':
                values = list(r.edits)
            else:
                values = list(r.steps[location].applied)
            index = next(i for i, a in enumerate(values) if a.contributions)
            item = values[index]
            contribution = item.contributions[0]
            if field == 'drop':
                item = replace(item, contributions=())
            elif field == 'rule_ids':
                item = replace(item, rule_ids=(MARKER, GAP, CAP))
            elif field == 'primary':
                item = replace(item, primary_rule_id='core.parenthesized-scripts')
            else:
                altered = {'rule': {'rule_id': 'core.parenthesized-scripts'},
                           'pass': {'pass_number': 3}, 'bool': {'pass_number': True},
                           'edit': {'edit': Edit(0, 0, b'FORGED')},
                           'earlier': {'pass_number': 1}}[field]
                item = replace(item, contributions=(replace(contribution, **altered),)
                               + item.contributions[1:])
            values[index] = item
            if location == 'top':
                return replace(r, edits=tuple(values))
            steps = list(r.steps)
            steps[location] = replace(steps[location], applied=tuple(values))
            return replace(r, steps=tuple(steps))
        for location in ('top', 0, 1):
            for field in ('drop', 'rule_ids', 'primary', 'rule', 'pass', 'bool', 'edit'):
                with self.subTest(location=location, field=field):
                    self.bad_replay(lambda r, f: mutate(r, field, location))
        self.bad_replay(lambda r, f: mutate(r, 'earlier', 1))

    def test_arbitrary_gap_or_marker_strings_and_unowned_case_work_never_validate(self):
        for rid, replacement in ((GAP, b'\n\n'), (GAP, b'\r'), (GAP, b'\n#parbreak()\n'),
                                 (MARKER, b'#paragraph_tab()\n'), (MARKER, b'#paragraph_tab\n\n'),
                                 (MARKER, b'#paragraph_tab\r'), (MARKER, b'#other\n'),
                                 (MARKER, b'#paragraph_tab\nWe ')):
            with self.subTest(rid=rid, replacement=replacement):
                f = self.fixture()
                with context(f) as fix:
                    actual = bridge.analyze_bounded_paragraphs
                    def lie(tree, *args, **kwargs):
                        results, contexts, owners = actual(tree, *args, **kwargs)
                        result = results[rid]
                        if not result.edits:
                            return results, contexts, owners
                        edit = replace(result.edits[0], replacement=replacement)
                        results = dict(results, **{rid: replace(result, edits=(edit,) + result.edits[1:])})
                        return results, contexts, owners
                    with mock.patch.object(bridge, 'analyze_bounded_paragraphs', side_effect=lie):
                        with self.assertRaises(TransactionError):
                            fix.validate()
                self.assertEqual(f.source.read_bytes(), DIRTY)
                self.no_manifest(f)

    def test_missing_duplicate_or_foreign_actual_boundary_proposals_refuse(self):
        for rid in (GAP, MARKER, CAP):
            for alteration in ('drop', 'duplicate', 'foreign', 'diagnostic', 'diagnostic_rule', 'diagnostic_offset', 'diagnostic_metadata'):
                with self.subTest(rule=rid, alteration=alteration):
                    f = self.fixture()
                    with context(f) as fix:
                        actual = bridge.analyze_bounded_paragraphs
                        def lie(tree, *args, **kwargs):
                            results, contexts, owners = actual(tree, *args, **kwargs)
                            result = results[rid]
                            if result.edits:
                                edits = {'drop': (), 'duplicate': result.edits + result.edits[:1],
                                         'foreign': result.edits + (Edit(0, 0, b'X'),)}.get(alteration, result.edits)
                                diagnostics = result.diagnostics
                                if alteration == 'diagnostic':
                                    diagnostics = ()
                                elif alteration.startswith('diagnostic_'):
                                    difference = {'diagnostic_rule': {'rule_id': 'foreign.rule'},
                                                  'diagnostic_offset': {'column': diagnostics[0].column + 1},
                                                  'diagnostic_metadata': {'source': 'ambient-other-catalog'}}[alteration]
                                    diagnostics = (replace(diagnostics[0], **difference),) + diagnostics[1:]
                                result = replace(result, edits=edits, diagnostics=diagnostics)
                                results = dict(results, **{rid: result})
                            return results, contexts, owners
                        with mock.patch.object(bridge, 'analyze_bounded_paragraphs', side_effect=lie):
                            with self.assertRaises(TransactionError):
                                fix.validate()
                    self.assertEqual(f.source.read_bytes(), DIRTY)
                    self.no_manifest(f)

    def test_malformed_actual_backend_envelopes_contexts_and_owner_cycles_fail_boundedly(self):
        for kind in ('none', 'short', 'list', 'results', 'contexts_list', 'contexts_growth',
                     'contexts_cycle', 'owners_list', 'owners_growth', 'owner_cycle', 'owner_missing', 'owner_other_first', 'owner_copy_fields'):
            with self.subTest(kind=kind):
                f = self.fixture()
                with context(f) as fix:
                    actual = bridge.analyze_bounded_paragraphs
                    def malformed(*args, **kwargs):
                        values, contexts, owners = actual(*args, **kwargs)
                        if kind == 'none':
                            return None
                        if kind == 'short':
                            return (values, contexts)
                        if kind == 'list':
                            return [values, contexts, owners]
                        if kind == 'results':
                            return None, contexts, owners
                        if kind == 'contexts_list':
                            return values, list(contexts), owners
                        if kind == 'contexts_growth':
                            return values, contexts * 100_000, owners
                        if kind == 'contexts_cycle':
                            object.__setattr__(contexts[0], 'expected_first', contexts)
                        elif kind == 'owners_list':
                            return values, contexts, list(owners)
                        elif kind == 'owners_growth':
                            return values, contexts, owners * 100_000
                        elif kind == 'owner_cycle':
                            object.__setattr__(owners[0], 'content_nodes', (owners[0],))
                        elif kind == 'owner_missing':
                            object.__delattr__(owners[0], 'first_node')
                        elif kind == 'owner_other_first':
                            object.__setattr__(owners[1], 'first_node', owners[2].first_node)
                        else:
                            object.__setattr__(owners[0], 'first_node', True)
                        return values, contexts, owners
                    with mock.patch.object(bridge, 'analyze_bounded_paragraphs', side_effect=malformed):
                        with self.assertRaises(TransactionError):
                            fix.validate()
                self.assertEqual(f.source.read_bytes(), DIRTY)
                self.no_manifest(f)

    def test_original_prose_math_import_show_or_different_first_word_cannot_be_changed(self):
        for old, new in ((b'iPhone', b'iphone'), (b'x_(1)', b'x_(2)'),
                         (b'paragraph_tab, apply', b'paragraph_tab, other'),
                         (b'#show: apply-paragraph-tabs', b'#show: text'),
                         (b'keeps eBay', b'Keeps eBay'),
                         (b'unknown first', b'Unknown first')):
            with self.subTest(old=old):
                self.bad_replay(lambda r, f, old=old, new=new:
                                replace(r, content=r.content.replace(old, new, 1)))

    def test_offset_maps_preserve_original_bytes_but_are_not_protected_proof_authority(self):
        f = self.fixture()
        with context(f) as fix:
            candidate = fix.validate()
            replay = fix._validation.replay
            replaced = {c.edit.start for s in replay.steps for a in s.applied
                        for c in a.contributions if c.rule_id == CAP and c.pass_number == 1}
            replaced.update(DIRTY.index(b'\n' + word) + 1 for word in (b'we ', b'therefore '))
            for offset, byte in enumerate(DIRTY):
                mapped = replay.map_offset(offset)
                if offset in replaced:
                    self.assertIsNone(mapped)
                else:
                    self.assertIsNotNone(mapped)
                    self.assertEqual(replay.content[mapped], byte)
            self.assertEqual(replay.map_offset(len(DIRTY)), len(FINAL))
            for step in replay.steps:
                for item in step.applied:
                    edit = item.edit
                    if edit.start == edit.end:
                        self.assertEqual(step.map_boundary(edit.start, side='right')
                                         - step.map_boundary(edit.start, side='left'), len(edit.replacement))
            fix.apply(candidate)
        for owner, method in ((candidate_convergence.Candidate, 'map_offset'),
                              (candidate_convergence.Candidate, 'map_boundary'),
                              (candidate_convergence.Step, 'map_offset'),
                              (candidate_convergence.Step, 'map_boundary')):
            with self.subTest(owner=owner.__name__, method=method):
                # Protected ownership must derive coordinates independently,
                # rather than trust mutable Candidate/Step convenience methods.
                f = self.fixture()
                with mock.patch.object(owner, method, side_effect=AssertionError('untrusted coordinate method')):
                    with context(f) as fix:
                        candidate = fix.validate()
                        self.assertEqual(candidate.sources[f.target], FINAL)
                        fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), FINAL)

    def test_postvalidation_equal_copies_cycles_and_second_pass_proofs_have_no_authority(self):
        kinds = ('final_proof_copy', 'history_proof_copy', 'history_proof_stale',
                 'history_results_copy', 'step_copy', 'applied_copy', 'contribution_copy',
                 'history_proof_cycle', 'final_proof_cycle', 'replay_cycle')
        # Each mutation gets its own source/transaction. If an implementation
        # ever accepts a forged record, later cases must not become cascades.
        for kind in kinds:
            with self.subTest(kind=kind):
                f = self.fixture()
                with context(f) as fix:
                    candidate = fix.validate()
                    validation = fix._validation
                    replay = validation.replay
                    second = fix._history[1]
                    variants = dict(
                        final_proof_copy=(validation, 'proof', copy.copy(validation.proof)),
                        history_proof_copy=(second, 'proof', copy.copy(second.proof)),
                        history_proof_stale=(second, 'proof', fix._history[0].proof),
                        history_results_copy=(second, 'results', MappingProxyType(dict(second.results))),
                        step_copy=(replay, 'steps', tuple(replace(step) for step in replay.steps)),
                        applied_copy=(replay.steps[1], 'applied',
                                      tuple(replace(a) for a in replay.steps[1].applied)),
                        contribution_copy=(replay.steps[1].applied[0], 'contributions',
                                           tuple(replace(c) for c in replay.steps[1].applied[0].contributions)),
                        history_proof_cycle=(second.proof, 'paragraphs', (second.proof,)),
                        final_proof_cycle=(validation.proof, 'paragraphs', (validation.proof,)),
                        replay_cycle=(replay, 'steps', (replay,)))
                    obj, name, value = variants[kind]
                    with changed(obj, name, value):
                        self.refused(f, fix, candidate)
                    fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), FINAL)

    def test_preissuance_history_copies_stale_intermediate_or_extra_passes_refuse(self):
        def mutation(kind):
            def mutate(replay, fix):
                record = fix._history[1]
                if kind == 'copy':
                    fix._history[1] = replace(record)
                elif kind == 'stale':
                    object.__setattr__(record, 'proof', fix._history[0].proof)
                elif kind == 'cycle':
                    object.__setattr__(record.proof, 'paragraphs', (record.proof,))
                elif kind == 'growth':
                    object.__setattr__(record.proof, 'paragraphs', record.proof.paragraphs * 100_000)
                elif kind == 'extra':
                    fix._history.append(fix._history[-1])
                else:
                    fix._history[1], fix._history[2] = fix._history[2], fix._history[1]
                return replay
            return mutate
        for kind in ('copy', 'stale', 'cycle', 'growth', 'extra', 'reorder'):
            with self.subTest(kind=kind):
                self.bad_replay(mutation(kind))

    def test_maximum_paragraph_count_keeps_all_boundaries_and_one_more_refuses(self):
        source = PREFIX + b'Initial prose.\n\n' + b'this preserves iPhone.\n\n' * (core.MAX_PARAGRAPHS - 1)
        f = self.fixture(source)
        expected = PREFIX + b'Initial prose.\n\n' + b'#paragraph_tab\nThis preserves iPhone.\n\n' * (core.MAX_PARAGRAPHS - 1)
        with context(f) as fix:
            candidate = fix.validate()
            self.assertEqual((candidate.passes, candidate.sources[f.target]), (2, expected))
            self.assertEqual(len(fix._validation.proof.paragraphs), core.MAX_PARAGRAPHS)
            self.assertEqual(len(fix._validation.proof.boundaries), core.MAX_PARAGRAPHS - 1)
            self.assertEqual(sum(c.rule_id == MARKER for step in fix._validation.replay.steps
                                 for item in step.applied for c in item.contributions), core.MAX_PARAGRAPHS - 1)
            self.assertEqual(sum(c.rule_id == CAP for step in fix._validation.replay.steps
                                 for item in step.applied for c in item.contributions), core.MAX_PARAGRAPHS - 1)
        self.assertEqual(f.source.read_bytes(), source)
        self.no_manifest(f)
        f = self.fixture(source + b'this exceeds the paragraph count.\n')
        with self.assertRaises((lint.InputError, TransactionError)):
            with context(f) as fix:
                fix.validate()
        self.assertEqual(f.source.read_bytes(), f.original)
        self.no_manifest(f)

    def test_growth_in_candidate_source_or_node_count_is_a_typed_bounded_refusal(self):
        for field in ('source', 'nodes'):
            with self.subTest(field=field):
                f = self.fixture()
                with context(f) as fix:
                    actual = bridge.parse
                    def growing(raw, *args, **kwargs):
                        tree = actual(raw, *args, **kwargs)
                        if raw != DIRTY:
                            tree = replace(tree, **({'source': b'x' * (MAX_INPUT + 1)}
                                if field == 'source' else {'nodes': tree.nodes * (MAX_NODES // len(tree.nodes) + 1)}))
                        return tree
                    with mock.patch.object(bridge, 'parse', side_effect=growing):
                        with self.assertRaises(TransactionError):
                            fix.validate()
                self.assertEqual(f.source.read_bytes(), DIRTY)
                self.no_manifest(f)

    def test_initial_unsupported_new_starters_and_ambiguous_structures_never_write(self):
        starters = (b'unknown prose.', b'tHiS prose.', b'this, prose.', b'i prose.',
                    b'$x_(1)$ this prose.', b'*this* prose.', b'"this" prose.',
                    b'= this heading', b'@ref this prose.', '이것 prose.'.encode(),
                    b'this\ncontinues on another source line.', b'this $x_1$ prose.',
                    b'#read("never")', b'#{broken')
        variants = [PREFIX + b'Initial prose.\n\n' + starter + b'\n' for starter in starters]
        variants += [
            PREFIX + b'Initial prose.\nthis lacks an existing paragraph boundary.\n',
            PREFIX + b'Initial prose.\n// comment\n#paragraph_tab\nthis is ambiguous.\n',
            PREFIX + b'Initial prose.\n\n// comment\nthis is ambiguous.\n',
            PREFIX + b'Initial prose.\n\n#paragraph_tab()\nthis is a call.\n',
            PREFIX + b'Initial prose.\n\n#paragraph_tab\n\nthis is detached.\n',
            PREFIX + b'Initial prose.\n\n#paragraph_tab\n#paragraph_tab\nthis is doubled.\n',
            PREFIX + b'Initial prose.\n\n#let alias = paragraph_tab\nthis is dynamic.\n',
            DIRTY.replace(b'#show: apply-paragraph-tabs', b'#show: text'),
            DIRTY.replace(b'paragraph_tab, apply-paragraph-tabs', b'paragraph_tab as tab, apply-paragraph-tabs'),
            PREFIX + b'Initial prose only.\n',
        ]
        for source in variants:
            with self.subTest(source=source):
                f = self.fixture(source)
                with self.assertRaises((lint.InputError, TransactionError)):
                    with context(f) as fix:
                        fix.validate()
                self.assertEqual(f.source.read_bytes(), source)
                self.no_manifest(f)

    def test_retained_catalog_only_during_both_passes_and_no_generic_partial_skip_disappears(self):
        f = self.fixture()
        with context(f) as fix, ExitStack() as stack:
            for module in (paragraph_rules, capitalization, prose_marker_rules):
                stack.enter_context(mock.patch.object(module, 'load_rules',
                    side_effect=AssertionError('ambient catalog fallback')))
            stack.enter_context(mock.patch('script_rule.load_rules',
                side_effect=AssertionError('ambient script catalog fallback')))
            stack.enter_context(mock.patch.object(Path, 'read_bytes',
                side_effect=AssertionError('ambient byte read during retained repair')))
            candidate = fix.validate()
            self.assertEqual(candidate.sources[f.target], FINAL)
        # Ordinary source analysis still reports partial coverage, including
        # genuinely unsupported boundaries. Protected coverage is not reused.
        generic_source = PREFIX + b'Initial prose.\n\nthis new paragraph.\n'
        tree = parse(generic_source)
        with context(self.fixture(generic_source)) as fix:
            resolution = resolve(tree, fallback_language='en', packages=(fix._capture.package,))
            ordinary = prose_marker_rules.analyze(tree, 'main.typ', resolution)
            self.assertTrue(ordinary.skips)
        for source in (DIRTY, FINAL):
            f = self.fixture(source)
            before = tuple(state(p) for p in (f.source, *evidence(f)))
            report = lint.report(f.root, 'fix', package_path=f.package_path)
            self.assertEqual(report['status'], 2)
            self.assertFalse(report['coverage']['complete'])
            self.assertEqual(report['applied_fixes'], [])
            self.assertFalse(report['compiler_invoked'])
            self.assertEqual(tuple(state(p) for p in (f.source, *evidence(f))), before)
            self.no_manifest(f)

    def test_bounded_backend_requires_explicit_valid_metadata_and_uses_its_diagnostic_fields(self):
        f = self.fixture()
        with context(f) as fix:
            tree = fix._capture.tree
            resolution = resolve(tree, fallback_language='en', packages=(fix._capture.package,))
            catalog = fix._capture.catalog
            record = next(r for r in catalog if r['id'] == MARKER)
            for supplied in (None, (), {}, (None,), catalog + (record,),
                             tuple(r for r in catalog if r['id'] != GAP)):
                with self.subTest(supplied=type(supplied).__name__):
                    with mock.patch.object(prose_marker_rules, 'load_rules',
                                           side_effect=AssertionError('ambient fallback')):
                        with self.assertRaises((lint.InputError, lint.SafetyError)):
                            bounded_paragraph_rules.analyze(tree, f.target, resolution, catalog=supplied)
            records = tuple(dict(r, source='independent-retained-metadata', grade='error')
                            if r['id'] in (CAP, GAP, MARKER) else r for r in catalog)
            with mock.patch.object(prose_marker_rules, 'load_rules', side_effect=AssertionError('ambient marker catalog')), \
                    mock.patch.object(paragraph_rules, 'load_rules', side_effect=AssertionError('ambient paragraph catalog')), \
                    mock.patch.object(capitalization, 'load_rules', side_effect=AssertionError('ambient capitalization catalog')):
                results, contexts, owners = bounded_paragraph_rules.analyze(tree, f.target, resolution, catalog=records)
            self.assertEqual(len(owners), 6)
            self.assertEqual(len(contexts), 3)
            for rid in (CAP, GAP, MARKER):
                self.assertTrue(results[rid].diagnostics)
                self.assertTrue(all((d.grade, d.source) == ('error', 'independent-retained-metadata')
                                    for d in results[rid].diagnostics))
        self.assertEqual(f.source.read_bytes(), DIRTY)
        self.no_manifest(f)

    def test_multipass_source_config_catalog_and_package_inode_drift_refuse_before_commit(self):
        for role in ('source', 'config', 'catalog', 'manifest', 'package'):
            for when in ('validate', 'apply'):
                with self.subTest(role=role, when=when):
                    f = self.fixture()
                    with context(f) as fix:
                        candidate = fix.validate() if when == 'apply' else None
                        same_byte_replace(getattr(f, role))
                        before = tuple(state(p) for p in (f.source, *evidence(f)))
                        with self.assertRaises(TransactionError):
                            fix.apply(candidate) if when == 'apply' else fix.validate()
                        with self.assertRaises(TransactionError):
                            fix.committed_snapshot()
                    self.assertEqual(tuple(state(p) for p in (f.source, *evidence(f))), before)
                    self.no_manifest(f)

    def assert_true_commit_without_receipt(self, f, fix):
        self.assertEqual(f.source.read_bytes(), FINAL)
        self.assertEqual(fix.summary.final_sha256, lint.digest(FINAL))
        self.assertEqual(fix.summary.modified_paths, (f.target,))
        manifests = tuple(f.store.glob('root-*/*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        self.assertEqual(manifests[0].parent.name, fix.summary.journal_id)
        self.assertIsNone(fix._committed)
        self.assertNotIn(fix, bridge._COMMITTED_BINDINGS)
        with self.assertRaises(TransactionError):
            fix.committed_snapshot()

    def test_late_semantic_reparse_cannot_copy_history_before_or_after_source_commit(self):
        for when in ('before-commit', 'after-commit'):
            with self.subTest(when=when):
                f = self.fixture()
                with context(f) as fix:
                    candidate = fix.validate()
                    second, committed, touched = fix._history[1], [], []
                    original = second.proof
                    actual_parse, actual_apply = bridge.parse, fix._tx.apply
                    def apply_then_mark(*args, **kwargs):
                        result = actual_apply(*args, **kwargs)
                        committed.append(True)
                        return result
                    def mutate_during_reparse(*args, **kwargs):
                        tree = actual_parse(*args, **kwargs)
                        if not touched and bool(committed) == (when == 'after-commit'):
                            touched.append(True)
                            object.__setattr__(second, 'proof', copy.copy(original))
                        return tree
                    try:
                        with mock.patch.object(fix._tx, 'apply', side_effect=apply_then_mark), \
                                mock.patch.object(bridge, 'parse', side_effect=mutate_during_reparse):
                            if when == 'before-commit':
                                self.refused(f, fix, candidate)
                            else:
                                with self.assertRaises(TransactionError):
                                    fix.apply(candidate)
                                self.assert_true_commit_without_receipt(f, fix)
                        self.assertTrue(touched)
                    finally:
                        object.__setattr__(second, 'proof', original)
                    if when == 'before-commit':
                        fix.apply(candidate)
                        fix.assert_committed_current(fix.committed_snapshot())
                    else:
                        # Restoring genuine objects cannot turn a failed bridge
                        # apply into newly issued compiler authority.
                        self.assert_true_commit_without_receipt(f, fix)
                        with self.assertRaises(TransactionError):
                            fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), FINAL)

    def test_independent_parser_failure_before_or_after_source_commit_is_safety_error(self):
        for when in ('before-commit', 'after-commit'):
            with self.subTest(when=when):
                f = self.fixture()
                with context(f) as fix:
                    candidate = fix.validate()
                    actual_parse, actual_apply = bridge.parse, fix._tx.apply
                    committed, touched = [], []
                    def apply_then_mark(*args, **kwargs):
                        result = actual_apply(*args, **kwargs)
                        committed.append(True)
                        return result
                    def fail_during_reparse(*args, **kwargs):
                        if bool(committed) == (when == 'after-commit'):
                            touched.append(True)
                            raise AdapterError('owned parser failure')
                        return actual_parse(*args, **kwargs)
                    with mock.patch.object(fix._tx, 'apply', side_effect=apply_then_mark), \
                            mock.patch.object(bridge, 'parse', side_effect=fail_during_reparse):
                        with self.assertRaises(TransactionError) as caught:
                            fix.apply(candidate)
                    self.assertTrue(touched)
                    self.assertNotIsInstance(caught.exception, lint.InputError)
                    if when == 'before-commit':
                        self.assertEqual(f.source.read_bytes(), DIRTY)
                        self.no_manifest(f)
                        fix.apply(candidate)
                    else:
                        self.assert_true_commit_without_receipt(f, fix)

    def test_first_and_repeated_receipt_retrieval_never_reparse_or_reload_inputs(self):
        for source in (DIRTY, FINAL):
            with self.subTest(source=source):
                f = self.fixture(source)
                with context(f) as fix:
                    fix.apply(fix.validate())
                    original = fix._committed
                    self.assertIsNotNone(original)
                    with ExitStack() as patches:
                        for module, name in ((inputs, 'parse'), (bridge, 'parse'),
                                (package_loader, 'load_packages'), (core, 'certify'),
                                (core, 'verify_proof'), (core, 'compare_repair_candidate')):
                            patches.enter_context(mock.patch.object(module, name,
                                side_effect=AssertionError('fresh committed input evaluation')))
                        self.assertIs(fix.committed_snapshot(), original)
                        self.assertIs(fix.committed_snapshot(), original)
                        fix.assert_committed_current(original)
                        compiler._guard(fix, original)
                    self.assertIs(original.tree, fix._validation.replay.final_tree)
                    self.assertIs(original.proof, fix._validation.proof)
                    self.assertEqual(original.sources[f.target], FINAL)

    def test_transaction_currentness_substitution_cannot_issue_after_true_commit(self):
        for method in ('committed_snapshot', 'assert_current'):
            with self.subTest(method=method):
                f = self.fixture()
                with context(f) as fix:
                    candidate = fix.validate()
                    second, touched = fix._history[1], []
                    original = second.proof
                    actual = getattr(fix._tx, method)
                    def substitute_after_currentness(*args, **kwargs):
                        result = actual(*args, **kwargs)
                        if not touched:
                            touched.append(True)
                            object.__setattr__(second, 'proof', copy.copy(original))
                        return result
                    try:
                        with mock.patch.object(fix._tx, method,
                                               side_effect=substitute_after_currentness):
                            with self.assertRaises(TransactionError):
                                fix.apply(candidate)
                        self.assertTrue(touched)
                        self.assert_true_commit_without_receipt(f, fix)
                    finally:
                        object.__setattr__(second, 'proof', original)
                    self.assert_true_commit_without_receipt(f, fix)

    def test_late_visible_apply_state_substitution_cannot_publish_receipt_after_restoration(self):
        for alteration in ('applied-copy', 'applied-reset', 'applied-deleted',
                           '_committed', '_committed_facts', '_receipt'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with context(f) as fix:
                    candidate = fix.validate()
                    validation, actual = fix._validation, fix._tx.assert_current
                    touched = []
                    def substitute_after_currentness(*args, **kwargs):
                        result = actual(*args, **kwargs)
                        if not touched:
                            touched.append(True)
                            if alteration == 'applied-copy':
                                fix._applied_validation = copy.copy(validation)
                            elif alteration == 'applied-reset':
                                fix._applied_validation = None
                            elif alteration == 'applied-deleted':
                                del fix._applied_validation
                            else:
                                setattr(fix, alteration, object())
                        return result
                    try:
                        with mock.patch.object(fix._tx, 'assert_current',
                                               side_effect=substitute_after_currentness):
                            with self.assertRaises(TransactionError):
                                fix.apply(candidate)
                        self.assertTrue(touched)
                        self.assertNotIn(fix, bridge._COMMITTED_BINDINGS)
                    finally:
                        fix._applied_validation = validation
                        fix._committed = fix._committed_facts = fix._receipt = None
                    self.assert_true_commit_without_receipt(f, fix)

    def test_prepopulated_receipt_slots_refuse_before_source_application(self):
        f = self.fixture()
        with context(f) as fix:
            candidate = fix.validate()
            for name in ('_committed', '_committed_facts', '_receipt'):
                with self.subTest(name=name), changed(fix, name, object()):
                    self.refused(f, fix, candidate)
                    self.assertNotIn(fix, bridge._COMMITTED_BINDINGS)
                    with self.assertRaises(TransactionError):
                        _ = fix.summary
            fix.apply(candidate)
            fix.assert_committed_current(fix.committed_snapshot())

    def test_live_transaction_currentness_rechecks_original_edges_after_callback(self):
        f = self.fixture()
        with context(f) as fix:
            fix.apply(fix.validate())
            snapshot, second = fix._committed, fix._history[1]
            original, actual = second.proof, fix._tx.assert_current
            for operation in ('retrieve', 'current', 'compile-guard'):
                with self.subTest(operation=operation):
                    touched = []
                    def substitute_after_currentness(*args, **kwargs):
                        result = actual(*args, **kwargs)
                        touched.append(True)
                        object.__setattr__(second, 'proof', copy.copy(original))
                        return result
                    try:
                        with mock.patch.object(fix._tx, 'assert_current',
                                               side_effect=substitute_after_currentness):
                            error = compiler.CompileSafetyError if operation == 'compile-guard' else TransactionError
                            with self.assertRaises(error):
                                if operation == 'retrieve':
                                    fix.committed_snapshot()
                                elif operation == 'current':
                                    fix.assert_committed_current(snapshot)
                                else:
                                    compiler._guard(fix, snapshot)
                        self.assertTrue(touched)
                    finally:
                        object.__setattr__(second, 'proof', original)
                    self.assertIs(fix.committed_snapshot(), snapshot)

    def test_direct_transaction_apply_and_forged_visible_success_cannot_issue_receipt(self):
        f = self.fixture()
        with context(f) as fix:
            candidate = fix.validate()
            validation = fix._validation
            fix._tx.apply(candidate._token)
            fix._applied_validation = validation
            fix._validation_attempt_started = fix._apply_attempt_started = True
            for owner in (fix, copy.copy(fix)):
                with self.subTest(owner=type(owner)):
                    with self.assertRaises(TransactionError):
                        owner.committed_snapshot()
                    self.assertNotIn(owner, bridge._COMMITTED_BINDINGS)
            with self.assertRaises(TransactionError):
                fix.apply(candidate)
        self.assertEqual(f.source.read_bytes(), FINAL)

    def test_reset_receipt_fields_cannot_refresh_successful_original_authority(self):
        f = self.fixture()
        with context(f) as fix:
            fix.apply(fix.validate())
            original = fix._committed
            for name in ('_committed', '_committed_facts', '_receipt', '_validation_graph_facts'):
                with self.subTest(name=name), changed(fix, name, None):
                    with self.assertRaises(TransactionError):
                        fix.committed_snapshot()
                    with self.assertRaises(TransactionError):
                        fix.assert_committed_current(original)
                    with self.assertRaises(compiler.CompileSafetyError):
                        compiler._guard(fix, original)
                self.assertIs(fix.committed_snapshot(), original)

    def test_committed_seal_retains_actual_history_container_and_validation_binding(self):
        f = self.fixture()
        with context(f) as fix:
            fix.apply(fix.validate())
            snapshot, history = fix._committed, fix._history
            for alteration in ('container-copy', 'member-copy', 'reorder', 'cycle', 'growth', 'binding-copy'):
                with self.subTest(alteration=alteration):
                    members = list(history)
                    binding = bridge._VALIDATION_BINDINGS[fix]
                    if alteration == 'container-copy':
                        fix._history = list(history)
                    elif alteration == 'member-copy':
                        history[1] = copy.copy(history[1])
                    elif alteration == 'reorder':
                        history.reverse()
                    elif alteration == 'cycle':
                        history[1] = history
                    elif alteration == 'growth':
                        history.append(history[-1])
                    else:
                        bridge._VALIDATION_BINDINGS[fix] = tuple(list(binding))
                    try:
                        with self.assertRaises(TransactionError):
                            fix.committed_snapshot()
                        with self.assertRaises(compiler.CompileSafetyError):
                            compiler._guard(fix, snapshot)
                    finally:
                        fix._history = history
                        history[:] = members
                        bridge._VALIDATION_BINDINGS[fix] = binding
                    self.assertIs(fix.committed_snapshot(), snapshot)

    def test_inode_drift_before_first_receipt_retrieval_never_refreshes_authority(self):
        for role in ('source', 'config', 'catalog', 'manifest', 'package'):
            with self.subTest(role=role):
                f = self.fixture()
                with context(f) as fix:
                    fix.apply(fix.validate())
                    snapshot = fix._committed
                    same_byte_replace(getattr(f, role))
                    with mock.patch.object(bridge, 'parse',
                            side_effect=AssertionError('fresh baseline after inode drift')):
                        with self.assertRaises(TransactionError):
                            fix.committed_snapshot()
                        with self.assertRaises(compiler.CompileSafetyError):
                            compiler._guard(fix, snapshot)
                    self.assertEqual(f.source.read_bytes(), FINAL)
                    self.assertEqual(fix.summary.final_sha256, lint.digest(FINAL))

    def test_no_committed_receipt_exists_before_genuine_successful_two_pass_apply(self):
        f = self.fixture()
        with context(f) as fix:
            with self.assertRaises(TransactionError):
                fix.committed_snapshot()
            candidate = fix.validate()
            with self.assertRaises(TransactionError):
                fix.committed_snapshot()
            self.refused(f, fix, copy.copy(candidate))
            fix.apply(candidate)
            receipt = fix.committed_snapshot()
            self.assertEqual(receipt.sources[f.target], FINAL)
            self.assertEqual(receipt.source_profile, core.SOURCE_PROFILE)
            self.assertEqual(receipt.proof.grammar_version, core.GRAMMAR_VERSION)
            self.assertEqual(receipt.proof.source_sha256, lint.digest(FINAL))
            fix.assert_committed_current(receipt)


if __name__ == '__main__':
    unittest.main()
