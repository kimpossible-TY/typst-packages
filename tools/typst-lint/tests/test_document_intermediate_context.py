"""In-memory sequence contexts remain issued, bounded, and non-authoritative."""
from dataclasses import replace
import gc
import json
from pathlib import Path
import sys
import tracemalloc
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_footnote_binding as binding
import conditional_modern_highlight as modern
import document_footnote_evidence as footnote
import document_intermediate_context as intermediate
import document_resources
import document_sequence_preview as sequence
import include_context
import import_graph
from document_gap_preview import _MappedEdits
from edit_plan import Edit, plan
from syntax_client import parse
import test_document_modern_highlight_evidence as fixtures
from typst_lint import InputError, SafetyError, load_rules


@unittest.skipUnless(fixtures.fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class IntermediateContextTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CapturedModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project

    def setup_capture(self):
        source = b'#include "child.typ"\n'
        child = fixtures.fixtures.IMPORT + b'#highlighted[Text $x_1$ end.]'
        (self.project / 'child.typ').write_bytes(child)
        _, analyzer, snapshot, _ = self.fixture.issue(source)
        context, session = analyzer._session._context, analyzer._session
        proof = footnote.PreviewFootnoteEvidence(session, snapshot)
        catalog = load_rules()[0]['rules']
        records = [item for source in context.sources
                   if (item := sequence.preview(source.tree, source.path, catalog)) is not None]
        self.assertTrue(records)
        self.assertTrue(all(row['verification'] != 'withheld' for row in records))
        return proof, records, source, child

    def test_replay_and_new_binding_work_without_live_graph_reads(self):
        proof, records, main, child = self.setup_capture()
        with patch.object(include_context, 'load_include_context', side_effect=AssertionError('live include')), \
             patch.object(import_graph, 'load_import_graph', side_effect=AssertionError('live import')):
            value = intermediate.derive(proof, records)
            session = intermediate.issue_binding(value)
            report = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True).analyze_all()
        self.assertTrue(binding._valid(session))
        self.assertNotEqual(value.context.source_for('child.typ').source, child)
        self.assertEqual(value.context.source_for('main.typ').source, main)
        self.assertEqual(len(value.context.edges), 1)
        self.assertEqual(value.context.edges[0].target.path, 'child.typ')
        self.assertTrue(any(row['diagnostics'] for row in report['by_file']))
        self.assertFalse(value.runtime_binding_proved or value.edit_permission or value.compiler_permission)
        self.assertEqual((self.project / 'main.typ').read_bytes(), main)
        self.assertEqual((self.project / 'child.typ').read_bytes(), child)
        with self.assertRaises(SafetyError):
            footnote.PreviewFootnoteEvidence(session, proof._snapshot)
        with self.assertRaises(SafetyError):
            document_resources.stage_original(value.context, proof._snapshot)

    def test_altered_plans_unissued_views_and_mutated_snapshot_refuse(self):
        proof, records, _, _ = self.setup_capture()
        with self.assertRaises(SafetyError):
            intermediate.derive(proof, [])
        bad = [dict(item) for item in records]
        bad[0]['candidate_sha256'] = '0' * 64
        with self.assertRaises(SafetyError):
            intermediate.derive(proof, bad)
        value = intermediate.derive(proof, records)
        with self.assertRaises(SafetyError):
            intermediate.assert_intermediate(replace(value))
        session = intermediate.issue_binding(value)
        object.__setattr__(proof._snapshot.files[0], 'data', b'changed')
        self.assertFalse(binding._valid(session))
        with self.assertRaises(SafetyError):
            intermediate.assert_intermediate(value)

    def test_include_or_import_site_changes_are_refused(self):
        proof, records, _, _ = self.setup_capture()
        value = intermediate.derive(proof, records)
        original = proof._session._context
        mapping = {source.path: source.source for source in value.context.sources}
        mapping['main.typ'] += b'#include "unknown.typ"\n'
        with self.assertRaisesRegex(SafetyError, 'no replayed sequence'):
            intermediate._derived_include(original, mapping)
        for statement, kind in ((b'#include "child.typ"', 'ModuleInclude'),
                                (b'#import "child.typ": *', 'ModuleImport')):
            before = b'#let first = [' + statement + b']\n#let second = []'
            start = before.index(statement)
            insertion = before.index(b'[]') + 1
            edits = (Edit(start, start + len(statement), b''), Edit(insertion, insertion, statement))
            after = plan(before, edits).candidate()
            with self.subTest(kind=kind), self.assertRaises(SafetyError):
                intermediate._check_sites(parse(before), parse(after), kind, (_MappedEdits(edits),))

    def test_context_lifetime_is_tied_to_original_proof(self):
        proof, records, _, _ = self.setup_capture()
        value = intermediate.derive(proof, records)
        context = value.context
        object.__setattr__(proof._snapshot.files[0], 'data', b'changed')
        with self.assertRaises(SafetyError):
            include_context.assert_include_context(context)
        proof, records, _, _ = self.setup_capture()
        value = intermediate.derive(proof, records)
        context = value.context
        del value
        gc.collect()
        with self.assertRaises(InputError):
            include_context.assert_include_context(context)

    def test_nested_derivation_is_refused(self):
        source = fixtures.fixtures.IMPORT + b'#highlighted[Text $x$ end.]'
        _, analyzer, snapshot, _ = self.fixture.issue(source)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        value = intermediate.derive(proof, [])
        session = intermediate.issue_binding(value)
        another = footnote.PreviewFootnoteEvidence(session, snapshot)
        with self.assertRaisesRegex(SafetyError, 'non-derived'):
            intermediate.derive(another, [])

    def test_changed_issued_context_invalidates_derived_session(self):
        proof, records, _, _ = self.setup_capture()
        value = intermediate.derive(proof, records)
        session = intermediate.issue_binding(value)
        object.__setattr__(value.context.sources[0], 'source', b'forged')
        self.assertFalse(binding._valid(session))
        with self.assertRaises((InputError, SafetyError)):
            modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)

    def test_unissued_or_wrong_proof_refuses(self):
        with self.assertRaises(InputError):
            intermediate.derive(object(), [])
        with self.assertRaises(SafetyError):
            intermediate.issue_binding(object())

    def test_record_size_and_item_budgets_refuse_before_replay(self):
        proof, records, _, _ = self.setup_capture()
        with patch.object(intermediate, 'MAX_SEQUENCE_BYTES', 5):
            with self.assertRaisesRegex(InputError, 'budget exceeded'):
                intermediate.derive(proof, records)
        with patch.object(intermediate, 'MAX_SEQUENCE_CHUNKS', 2):
            with self.assertRaisesRegex(InputError, 'budget exceeded'):
                intermediate.derive(proof, records)

    def test_private_reconstruction_is_not_an_issued_context(self):
        proof, _, _, _ = self.setup_capture()
        original = proof._session._context
        rebuilt = intermediate._derived_include(original, {x.path: x.source for x in original.sources})
        with self.assertRaises(InputError):
            include_context.assert_include_context(rebuilt)

    def test_large_single_string_rejected_before_encoder_allocation(self):
        records = [{'replacement': 'x' * (8 * 1024 * 1024)}]
        tracemalloc.start()
        try:
            with patch.object(intermediate, 'MAX_SEQUENCE_BYTES', 1024):
                with self.assertRaisesRegex(InputError, 'budget exceeded'):
                    intermediate._copy_records(records)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 512 * 1024)

    def test_preflight_counts_utf8_escapes_and_rejects_cycles_and_depth(self):
        records = [{'text': '한글\n"\\\x01', 'n': -12, 'value': None, 'ok': True}]
        size = len(json.dumps(records, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        with patch.object(intermediate, 'MAX_SEQUENCE_BYTES', size):
            self.assertEqual(intermediate._copy_records(records), records)
        with patch.object(intermediate, 'MAX_SEQUENCE_BYTES', size - 1):
            with self.assertRaisesRegex(InputError, 'budget exceeded'):
                intermediate._copy_records(records)
        cyclic = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(InputError, 'cycle'):
            intermediate._copy_records(cyclic)
        nested = []
        for _ in range(intermediate.MAX_SEQUENCE_DEPTH + 2):
            nested = [nested]
        with self.assertRaisesRegex(InputError, 'budget exceeded'):
            intermediate._copy_records(nested)
        with self.assertRaises(InputError):
            intermediate._copy_records([{1: 'not a JSON object key'}])
