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
from typst_lint import SafetyError, digest, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentSequencePreviewTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

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
