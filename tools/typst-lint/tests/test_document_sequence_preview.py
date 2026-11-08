"""Actual hash-chained three-rule document preview plans."""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_sequence_preview
import include_context
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import InputError, SafetyError, digest, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentSequencePreviewTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

    def test_opt_in_conditional_gaps_are_chained_with_all_standard_stages(self):
        source = (b'Thus $ x^i $\n\nHence.\n\n#block[\n$\nA #footnote[X]\n\nB\n$\n'
                  b'#proof[Done.]\nNext.\n]')
        tree = parse(source)
        standard = document_sequence_preview.preview(tree, 'main.typ', self.catalog)
        report = document_sequence_preview.preview(tree, 'main.typ', self.catalog, profile='conditional-gaps')
        self.assertEqual([s['rule_id'] for s in standard['steps']],
                         ['pde.standalone-display', 'core.parenthesized-scripts', 'pde.display-continuation-gap'])
        self.assertEqual([s['rule_id'] for s in report['steps']],
                         [s['rule_id'] for s in standard['steps']]
                         + ['pde.footnote-trailing-gap', 'pde.proof-trailing-gap'])
        self.assertEqual(report['profile'], 'conditional-gaps')
        for key in ('runtime_binding_proved', 'render_equivalence_proved', 'edit_permission'):
            self.assertFalse(report[key])
        current = source
        for step in report['steps']:
            self.assertEqual(step['baseline_sha256'], digest(current))
            edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode()) for e in step['edits'])
            current = plan(current, edits).candidate()
            self.assertEqual(step['candidate_sha256'], digest(current))
        self.assertEqual(document_sequence_preview.replay(tree, 'main.typ', self.catalog, report,
                                                          profile='conditional-gaps'), current)
        self.assertIsNone(document_sequence_preview.preview(parse(current), 'main.typ', self.catalog,
                                                             profile='conditional-gaps'))
        with self.assertRaises(SafetyError):
            document_sequence_preview.replay(tree, 'main.typ', self.catalog, report)
        with self.assertRaises(SafetyError):
            document_sequence_preview.replay(tree, 'main.typ', self.catalog, standard, profile='conditional-gaps')

    def test_conditional_profile_does_not_trust_forged_or_partial_records(self):
        source = b'#block[\n$\nA #footnote[X]\n\nB\n$\n#proof[Done.]\nNext.\n]'
        tree = parse(source)
        report = document_sequence_preview.preview(tree, 'main.typ', self.catalog, profile='conditional-gaps')
        for mutation in ('profile', 'offset', 'hash', 'drop', 'permission'):
            changed = deepcopy(report)
            if mutation == 'profile': changed['profile'] = 'standard'
            elif mutation == 'offset': changed['steps'][0]['edits'][0]['start'] += 1
            elif mutation == 'hash': changed['steps'][1]['baseline_sha256'] = '0' * 64
            elif mutation == 'drop': changed['steps'].pop()
            elif mutation == 'permission': changed['edit_permission'] = True
            with self.subTest(mutation=mutation), self.assertRaises(SafetyError):
                document_sequence_preview.replay(tree, 'main.typ', self.catalog, changed, profile='conditional-gaps')
        for profile in (None, True, 'conditional', 'STANDARD'):
            with self.subTest(profile=profile), self.assertRaises(InputError):
                document_sequence_preview.preview(tree, 'main.typ', self.catalog, profile=profile)

    def test_punctuation_profile_chains_all_stages_and_is_profile_bound(self):
        source = (b'Thus $ x^i $\n\nHence.\n\n#block[\n$\nA #footnote[X]\n\nB\n$\n'
                  b'#proof[Done.]\nNext.\nClaim. #footnote[Reason.]\n]')
        tree = parse(source)
        standard = document_sequence_preview.preview(tree, 'main.typ', self.catalog, profile='conditional-gaps')
        report = document_sequence_preview.preview(tree, 'main.typ', self.catalog,
                                                   profile='conditional-gaps-and-punctuation')
        self.assertEqual(report['verification'],
                         'hash-chained-three-rule-and-conditional-gap-punctuation-reanalysis', report)
        self.assertEqual([step['rule_id'] for step in report['steps']],
                         [step['rule_id'] for step in standard['steps']] + ['pde.prose-footnote-punctuation'])
        self.assertFalse(report['edit_permission'] or report['runtime_binding_proved']
                         or report['render_equivalence_proved'])
        current = document_sequence_preview.replay(tree, 'main.typ', self.catalog, report,
                                                    profile='conditional-gaps-and-punctuation')
        self.assertIn(b'Claim.#footnote[Reason.]', current)
        self.assertIsNone(document_sequence_preview.preview(parse(current), 'main.typ', self.catalog,
                                                             profile='conditional-gaps-and-punctuation'))
        with self.assertRaises(SafetyError):
            document_sequence_preview.replay(tree, 'main.typ', self.catalog, report, profile='conditional-gaps')
        with self.assertRaises(SafetyError):
            document_sequence_preview.replay(tree, 'main.typ', self.catalog, standard,
                                              profile='conditional-gaps-and-punctuation')

    def test_punctuation_profile_preserves_other_findings_and_suppression(self):
        source = ('#block[한글. #footnote[이유]\r\n\r\nNext.\r\n]\r\n'
                  '// typst-lint: ignore-next pde.prose-footnote-punctuation -- intentional\r\n'
                  '#block[Ignored. #footnote[Note.]]').encode()
        tree = parse(source)
        report = document_sequence_preview.preview(tree, 'main.typ', self.catalog,
                                                   profile='conditional-gaps-and-punctuation')
        self.assertEqual(report['verification'],
                         'hash-chained-three-rule-and-conditional-gap-punctuation-reanalysis', report)
        self.assertEqual([step['rule_id'] for step in report['steps']], ['pde.prose-footnote-punctuation'])
        result = document_sequence_preview.replay(tree, 'main.typ', self.catalog, report,
                                                   profile='conditional-gaps-and-punctuation')
        self.assertIn('한글.#footnote[이유]'.encode(), result)
        self.assertIn(b'Ignored. #footnote[Note.]', result)
        self.assertEqual(result.count(b'\r\n'), source.count(b'\r\n'))

    def test_punctuation_profile_withholds_on_unknown_or_withheld_evidence(self):
        source = b'#block[Claim. #footnote[Reason.]]'
        tree = parse(source)
        with patch.object(document_sequence_preview.footnote_rules, 'preview_conditional_prose_punctuation',
                          return_value={'verification': 'withheld', 'reason': 'fixture refusal'}):
            report = document_sequence_preview.preview(tree, 'main.typ', self.catalog,
                                                       profile='conditional-gaps-and-punctuation')
        self.assertEqual(report['verification'], 'withheld')
        self.assertIn('fixture refusal', report['reason'])
        self.assertEqual(report['steps'], [])

    def test_punctuation_profile_withholds_on_cross_rule_mutation(self):
        source = b'#block[Claim. #footnote[Reason.]\n#proof[Done.]\nNext.\n]'
        tree = parse(source)
        # A forged stage deletes the proof name while keeping its own declared
        # hashes internally consistent. The independent cross-rule pass refuses.
        start = source.index(b'proof')
        edits = [Edit(start, start + len(b'proof'), b'other')]
        changed = plan(source, edits).candidate()
        fake = dict(verification='conditional-parse-syntax-directives-and-footnote-reanalysis',
                    baseline_sha256=digest(source), candidate_sha256=digest(changed),
                    edits=[dict(start=start, end=start + len(b'proof'), replacement='other',
                                rule_id='pde.prose-footnote-punctuation')])
        # Keep the original proof unchanged until the punctuation stage.
        with patch.object(document_sequence_preview.proof_rules, 'preview_conditional',
                          return_value=document_sequence_preview.proof_rules.ProofResult()), \
             patch.object(document_sequence_preview.footnote_rules, 'preview_conditional_prose_punctuation',
                          return_value=fake):
            report = document_sequence_preview.preview(tree, 'main.typ', self.catalog,
                                                       profile='conditional-gaps-and-punctuation')
        self.assertEqual(report['verification'], 'withheld')
        self.assertEqual(report['steps'], [])
        self.assertIn('changed proof findings', report['reason'])

    def test_punctuation_only_source_is_required_in_document_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = b'#block[Claim. #footnote[Reason.]]'
            (root / 'main.typ').write_bytes(source)
            context = include_context.load_include_context(root, (('main.typ', source),))
            tree = context.source_for('main.typ').tree
            self.assertIsNone(document_sequence_preview.preview(tree, 'main.typ', self.catalog,
                                                                 profile='conditional-gaps'))
            record = document_sequence_preview.preview(tree, 'main.typ', self.catalog,
                                                       profile='conditional-gaps-and-punctuation')
            with self.assertRaises(SafetyError):
                document_sequence_preview.replay_document(context, [], self.catalog,
                                                           profile='conditional-gaps-and-punctuation')
            replayed = document_sequence_preview.replay_document(context, [record], self.catalog,
                                                                  profile='conditional-gaps-and-punctuation')
            self.assertEqual(replayed['main.typ'], b'#block[Claim.#footnote[Reason.]]')
            self.assertEqual((root / 'main.typ').read_bytes(), source)

    def test_opt_in_spacer_is_chained_profile_bound_and_idempotent(self):
        source = (b'Thus $ x^i $\n\nHence.\n\n#block[\n$\nA #footnote[X]\n\nB\n$\n'
                  b'Claim. #footnote[Reason.]\n$\nC #dots_space #footnote[More.]\n$\n]')
        profile = 'conditional-gaps-punctuation-and-spacers'
        tree = parse(source)
        without = document_sequence_preview.preview(tree, 'main.typ', self.catalog,
                                                      profile='conditional-gaps-and-punctuation')
        report = document_sequence_preview.preview(tree, 'main.typ', self.catalog, profile=profile)
        self.assertEqual(report['verification'],
                         'hash-chained-three-rule-and-conditional-gap-punctuation-spacer-reanalysis', report)
        self.assertEqual([step['rule_id'] for step in report['steps']],
                         [step['rule_id'] for step in without['steps']] + ['pde.unaligned-footnote-no-spacer'])
        self.assertFalse(report['runtime_binding_proved'] or report['render_equivalence_proved']
                         or report['edit_permission'])
        candidate = document_sequence_preview.replay(tree, 'main.typ', self.catalog, report, profile=profile)
        self.assertIn(b'C  #footnote[More.]', candidate)
        self.assertIsNone(document_sequence_preview.preview(parse(candidate), 'main.typ', self.catalog, profile=profile))
        with self.assertRaises(SafetyError):
            document_sequence_preview.replay(tree, 'main.typ', self.catalog, report,
                                              profile='conditional-gaps-and-punctuation')
        changed = deepcopy(report)
        changed['steps'][-1]['baseline_sha256'] = '0' * 64
        with self.assertRaises(SafetyError):
            document_sequence_preview.replay(tree, 'main.typ', self.catalog, changed, profile=profile)

    def test_spacer_profile_skips_aligned_comments_and_suppression(self):
        cases = (b'#block[$\n A &= B #dots_space #footnote[X]\n$]',
                 b'#block[$\n A = B #dots_space /* comment */ #footnote[X]\n$]',
                 b'// typst-lint: ignore-next pde.unaligned-footnote-no-spacer -- deliberate\n'
                 b'#block[$\n A = B #dots_space #footnote[X]\n$]')
        for source in cases:
            with self.subTest(source=source):
                self.assertIsNone(document_sequence_preview.preview(parse(source), 'main.typ', self.catalog,
                                                                    profile='conditional-gaps-and-spacers'))

        shadowed = b'#let footnote(x) = x\n#block[$\n A = B #dots_space #footnote[X]\n$]'
        report = document_sequence_preview.preview(parse(shadowed), 'main.typ', self.catalog,
                                                   profile='conditional-gaps-and-spacers')
        self.assertEqual(report['verification'], 'withheld', report)
        self.assertIn('footnote binding is not locally proven', report['reason'])
        self.assertFalse(report['steps'])

    def test_spacer_profile_withholds_forged_or_withheld_preview(self):
        source = b'#block[$\n A = B #dots_space #footnote[X]\n$]'
        tree = parse(source)
        real = document_sequence_preview.footnote_rules.preview_conditional_unaligned_spacers(
            tree, 'main.typ', document_sequence_preview.resolve(tree), catalog=self.catalog)
        for mutation in ('withheld', 'authority', 'hash', 'target'):
            fake = deepcopy(real)
            if mutation == 'withheld': fake = {'verification': 'withheld', 'reason': 'fixture refusal'}
            elif mutation == 'authority': fake['runtime_binding_proved'] = True
            elif mutation == 'hash': fake['candidate_sha256'] = '0' * 64
            else:
                fake['edits'][0]['start'] = source.index(b'A')
                fake['edits'][0]['end'] = source.index(b'A') + 1
                fake['candidate_sha256'] = digest(source.replace(b'A', b'', 1))
            with self.subTest(mutation=mutation), patch.object(document_sequence_preview.footnote_rules,
                    'preview_conditional_unaligned_spacers', return_value=fake):
                report = document_sequence_preview.preview(tree, 'main.typ', self.catalog,
                                                           profile='conditional-gaps-and-spacers')
                self.assertEqual(report['verification'], 'withheld', report)
                self.assertFalse(report['steps'])

    def test_conditional_replay_requires_every_candidate_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            main = b'#include "child.typ"\n'
            child = b'#block[\n#proof[Done.]\nNext.\n]'
            (root / 'main.typ').write_bytes(main)
            (root / 'child.typ').write_bytes(child)
            context = include_context.load_include_context(root, (('main.typ', main),))
            item = context.source_for('child.typ')
            record = document_sequence_preview.preview(item.tree, item.path, self.catalog,
                                                        profile='conditional-gaps')
            with self.assertRaises(SafetyError):
                document_sequence_preview.replay_document(context, [], self.catalog, profile='conditional-gaps')
            replayed = document_sequence_preview.replay_document(context, [record], self.catalog,
                                                                  profile='conditional-gaps')
            self.assertEqual(replayed['child.typ'], child.replace(b']\nNext.', b']\n\nNext.'))
            self.assertEqual((root / 'child.typ').read_bytes(), child)
            with self.assertRaises(SafetyError):
                document_sequence_preview.replay_document(context, [record], self.catalog)

    def test_conditional_cross_rule_reanalysis_refuses_changed_footnote(self):
        source = b'#block[\nText #footnote[X]\n\nNext.\n#proof[Done.]\nFollowing.\n]'
        tree = parse(source)
        position = source.index(b'footnote')
        fake = document_sequence_preview.proof_rules.ProofResult(
            diagnostics=(object(),), edits=(Edit(position, position + len(b'footnote'), b'otherfun'),))
        with patch.object(document_sequence_preview.proof_rules, 'preview_conditional', return_value=fake):
            report = document_sequence_preview.preview(tree, 'main.typ', self.catalog, profile='conditional-gaps')
        self.assertEqual(report['verification'], 'withheld')
        self.assertEqual(report['steps'], [])
        self.assertIn('changed footnote', report['reason'])

    def test_conditional_utf8_crlf_and_suppressed_findings_survive_mapping(self):
        source = ('#block[\r\n한글. #footnote[이유]\r\n\r\n다음.\r\n'
                  '// typst-lint: ignore-next pde.proof-trailing-gap -- intentional\r\n'
                  '#proof[첫째.]\r\n다음.\r\n\r\n#proof[둘째.]\r\n다음.\r\n]').encode()
        tree = parse(source)
        report = document_sequence_preview.preview(tree, 'main.typ', self.catalog, profile='conditional-gaps')
        self.assertEqual(report['verification'], 'hash-chained-three-rule-and-conditional-gap-reanalysis', report)
        candidate = document_sequence_preview.replay(tree, 'main.typ', self.catalog, report,
                                                     profile='conditional-gaps')
        self.assertEqual(candidate.count(b'\r\n'), source.count(b'\r\n') + 1)
        self.assertIn(b'#proof[' + '첫째.'.encode() + b']\r\n' + '다음.'.encode(), candidate)
        self.assertIn(b'#proof[' + '둘째.'.encode() + b']\r\n\r\n' + '다음.'.encode(), candidate)

    def test_conditional_recursion_error_withholds_all_stages(self):
        source = b'Thus $ x^i $\n\nHence.\n\n#block[\n#proof[Done.]\nNext.\n]'
        tree = parse(source)
        with patch.object(document_sequence_preview.proof_rules, 'preview_conditional', side_effect=RecursionError):
            report = document_sequence_preview.preview(tree, 'main.typ', self.catalog, profile='conditional-gaps')
        self.assertEqual(report['verification'], 'withheld')
        self.assertFalse(report['steps'])
        self.assertIsNone(report['candidate_sha256'])

    def test_three_passes_are_hash_chained_and_replay_to_noop(self):
        source = b'#wrap[\r\n  Thus $ x^i $ <id>\r\n\r\n  Hence.\r\n]'
        report = document_sequence_preview.preview(parse(source), 'x.typ', self.catalog)
        self.assertEqual(report['verification'], 'hash-chained-parse-and-three-rule-reanalysis')
        self.assertEqual([s['rule_id'] for s in report['steps']],
                         ['pde.standalone-display', 'core.parenthesized-scripts',
                          'pde.display-continuation-gap'])
        current = source
        for step in report['steps']:
            self.assertEqual(step['baseline_sha256'], digest(current))
            edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode()) for e in step['edits'])
            current = plan(current, edits).candidate()
            self.assertEqual(step['candidate_sha256'], digest(current))
        self.assertEqual(report['candidate_sha256'], digest(current))
        self.assertEqual(current, b'#wrap[\r\n  Thus\r\n  $ x^(i) $ <id>\r\n  Hence.\r\n]')
        self.assertEqual(document_sequence_preview.replay(parse(source), 'x.typ', self.catalog, report), current)
        self.assertIsNone(document_sequence_preview.preview(parse(current), 'x.typ', self.catalog))

    def test_replay_rejects_altered_stale_or_withheld_plans(self):
        source = b'Thus $ x^i $\n\nHence.\n'
        tree = parse(source)
        report = document_sequence_preview.preview(tree, 'x.typ', self.catalog)
        for mutate in ('offset', 'rule', 'hash', 'stale', 'boolean', 'boolean_metadata'):
            changed = deepcopy(report)
            if mutate == 'offset': changed['steps'][0]['edits'][0]['start'] += 1
            elif mutate == 'rule': changed['steps'][0]['rule_id'] = 'other'
            elif mutate == 'hash': changed['candidate_sha256'] = '0' * 64
            elif mutate == 'boolean': changed['steps'][0]['edits'][0]['start'] = True
            elif mutate == 'boolean_metadata':
                changed['unresolved_display_contexts']['pde.standalone-display'] = False
            with self.subTest(mutate=mutate), self.assertRaises(SafetyError):
                document_sequence_preview.replay(parse(source + b' ' if mutate == 'stale' else source),
                                                 'x.typ', self.catalog, changed)

    def test_partial_script_withholds_even_an_earlier_standalone_edit(self):
        source = b'Thus $ x^#(value + 1) $\n\nHence.\n'
        report = document_sequence_preview.preview(parse(source), 'x.typ', self.catalog)
        self.assertEqual(report['verification'], 'withheld')
        self.assertFalse(report['steps'])
        self.assertIsNone(report['candidate_sha256'])
        self.assertIn('Script analysis is incomplete', report['reason'])

    def test_failed_display_preview_withholds_all_stages(self):
        source = b'Thus $ x^i $\n\nHence.\n'
        with patch.object(document_sequence_preview, 'preview_standalone',
                          return_value={'verification': 'withheld', 'reason': 'changed syntax'}):
            report = document_sequence_preview.preview(parse(source), 'x.typ', self.catalog)
        self.assertEqual(report['verification'], 'withheld')
        self.assertEqual(report['steps'], [])
        self.assertIn('changed syntax', report['reason'])

    def test_suppression_remains_attached_across_other_edits(self):
        source = (b'// typst-lint: ignore-next pde.display-continuation-gap -- intentional\n'
                  b'$ x^i $\n\nHence.\n')
        report = document_sequence_preview.preview(parse(source), 'x.typ', self.catalog)
        self.assertEqual(report['verification'], 'hash-chained-parse-and-three-rule-reanalysis')
        self.assertEqual([s['rule_id'] for s in report['steps']], ['core.parenthesized-scripts'])

    def test_document_replay_requires_complete_records_and_original_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            main = b'#include "child.typ"\n'
            child = b'Thus $ x^i $\n\nHence.\n'
            (root / 'main.typ').write_bytes(main)
            (root / 'child.typ').write_bytes(child)
            context = include_context.load_include_context(root, (('main.typ', main),))
            child_source = context.source_for('child.typ')
            record = document_sequence_preview.preview(child_source.tree, 'child.typ', self.catalog)
            candidates = document_sequence_preview.replay_document(context, [record], self.catalog)
            self.assertEqual(candidates['main.typ'], main)
            self.assertEqual(candidates['child.typ'], b'Thus\n$ x^(i) $\nHence.\n')
            with self.assertRaises(TypeError):
                candidates['child.typ'] = child
            for records in ([], [record, record], [dict(record, path='foreign.typ')]):
                with self.subTest(records=records), self.assertRaises(SafetyError):
                    document_sequence_preview.replay_document(context, records, self.catalog)
            with self.assertRaises(include_context.IncludeCaptureError):
                document_sequence_preview.replay_document(replace(context), [record], self.catalog)

    def test_document_replay_refuses_a_withheld_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = b'$ x^#(value + 1) $\n'
            (root / 'main.typ').write_bytes(source)
            context = include_context.load_include_context(root, (('main.typ', source),))
            record = document_sequence_preview.preview(context.sources[0].tree, 'main.typ', self.catalog)
            self.assertEqual(record['verification'], 'withheld')
            with self.assertRaises(SafetyError):
                document_sequence_preview.replay_document(context, [record], self.catalog)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_document_replay_compiles_an_owned_include_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            main = b'#include "child.typ"\n'
            child = b'Thus $ x^i $\n\nHence.\n'
            (root / 'main.typ').write_bytes(main)
            (root / 'child.typ').write_bytes(child)
            (root / 'packages').mkdir()
            (root / 'cache').mkdir()
            context = include_context.load_include_context(root, (('main.typ', main),))
            records = [document_sequence_preview.preview(s.tree, s.path, self.catalog)
                       for s in context.sources]
            candidates = document_sequence_preview.replay_document(context, [r for r in records if r], self.catalog)
            def compile():
                pdf = root / 'output.pdf'
                subprocess.run(['typst', 'compile', '--root', str(root),
                                '--package-path', str(root / 'packages'),
                                '--package-cache-path', str(root / 'cache'),
                                '--creation-timestamp', '1700000000', str(root / 'main.typ'), str(pdf)],
                               check=True, capture_output=True, timeout=30)
                return pdf.read_bytes()
            before = compile()
            for path, data in candidates.items():
                (root / path).write_bytes(data)
            after = compile()
            self.assertEqual(before, after)
