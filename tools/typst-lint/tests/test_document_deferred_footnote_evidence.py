"""Conditional deferred-call proof respects actual lexical scopes and capture."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_deferred_footnote_evidence as deferred
import document_sequence_preview as sequence
import test_document_footnote_evidence as fixtures
from typst_lint import InputError, SafetyError, load_rules


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class DeferredFootnoteEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PreviewFootnoteEvidenceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def inspect(self, source):
        self.fixture.source = source
        (self.fixture.project / 'main.typ').write_bytes(source)
        proof, _, _ = self.fixture.capture()
        offset = source.rindex(b'#footnote[Note]')
        return deferred.analyze(proof, 'main.typ', (offset,))

    def test_unrelated_nested_closure_and_sibling_shadow_do_not_hide_capture(self):
        prefix = b'#import "styles.typ": *\n'
        for inner in (b'#let helper(z) = [#let footnote = none]\n',
                      b'#block[#let footnote = none]\n',
                      b'#let helper(z) = [#context [Other]]\n',
                      b'#block[#import "unknown.typ": *]\n',
                      b'#for footnote in (none,) [Other]\n'):
            source = prefix + b'#use(x => [' + inner + b'#block[$ x #footnote[Note] $]])'
            with self.subTest(inner=inner):
                result = self.inspect(source)
                self.assertEqual(result.status, 'CONDITIONAL', result)
                self.assertEqual(len(result.calls), 1)
                self.assertFalse(result.runtime_binding_proved or result.execution_proved
                                 or result.edit_permission or result.compiler_permission)

    def test_shadow_mutation_unknown_wildcard_and_parameter_are_unresolved(self):
        prefix = b'#import "styles.typ": *\n'
        cases = (b'#use(x => [#let footnote = none\n#footnote[Note]])',
                 b'#use(footnote => [#footnote[Note]])',
                 b'#use(x => [#{footnote = none}\n#footnote[Note]])',
                 b'#use(x => [#for z in (1,) { footnote = none }\n#footnote[Note]])',
                 b'#use(x => [#import "unknown.typ": *\n#footnote[Note]])',
                 b'#use(x => [#import x.footnote\n#footnote[Note]])',
                 b'#use(x => [#import "unknown.typ": item as footnote\n#footnote[Note]])',
                 b'#use(x => [#import "styles.typ" as footnote: paragraph_tab\n#footnote[Note]])',
                 b'#use(x => [#import "styles.typ" as other: paragraph_tab as footnote\n#footnote[Note]])')
        for body in cases:
            with self.subTest(body=body):
                result = self.inspect(prefix + body)
                self.assertEqual(result.status, 'INCOMPLETE', result)
                self.assertFalse(result.calls)

    def test_dynamic_path_and_nested_closure_remain_unresolved(self):
        prefix = b'#import "styles.typ": *\n'
        cases = (b'#use(x => [#for y in (1,) [#footnote[Note]]])',
                 b'#use(x => [#context [#footnote[Note]]])',
                 b'#use(x => [#use(y => [#footnote[Note]])])')
        for body in cases:
            with self.subTest(body=body):
                result = self.inspect(prefix + body)
                self.assertEqual(result.status, 'INCOMPLETE', result)
                self.assertFalse(result.calls)

    def test_exact_parenthesized_content_callback_is_supported(self):
        source = (b'#import "styles.typ": *\n'
                  b'#use(x => ([#let helper(z) = [Other]\n#footnote[Note]]))')
        result = self.inspect(source)
        self.assertEqual(result.status, 'CONDITIONAL', result)
        self.assertEqual(len(result.calls), 1)

    def test_callback_declared_in_repeated_or_conditional_execution_is_unresolved(self):
        prefix = b'#import "styles.typ": *\n#let footnote = footnote\n'
        cases = (
            b'#for n in (0, 1) { let f = x => [#footnote[Note]]; f(0); footnote = none }',
            b'#while false { let f = x => [#footnote[Note]]; f(0) }',
            b'#if true { let f = x => [#footnote[Note]]; f(0) }',
            b'#for n in (0, 1) { for k in (0, 1) { let f = x => [#footnote[Note]]; f(0) } }',
        )
        for body in cases:
            with self.subTest(body=body):
                result = self.inspect(prefix + body)
                self.assertEqual(result.status, 'INCOMPLETE', result)
                self.assertFalse(result.calls)

    def test_source_selection_and_snapshot_mutation_are_rejected(self):
        source = b'#import "styles.typ": *\n#use(x => [#let helper(z) = [Other]\n#footnote[Note]])'
        self.fixture.source = source
        (self.fixture.project / 'main.typ').write_bytes(source)
        proof, _, snapshot = self.fixture.capture()
        offset = source.index(b'#footnote[Note]')
        for path, offsets in (('missing.typ', (offset,)), ('main.typ', (True,)),
                              ('main.typ', (offset, offset)), ('main.typ', (offset + 1,))):
            with self.subTest(path=path, offsets=offsets), self.assertRaises((InputError, SafetyError)):
                deferred.analyze(proof, path, offsets)
        object.__setattr__(snapshot.files[0], 'data', b'changed')
        with self.assertRaises(SafetyError):
            deferred.analyze(proof, 'main.typ', (offset,))

    @unittest.skipUnless(shutil.which('typst'), 'Requires Typst')
    def test_typst_callback_captures_builtin_across_sibling_scope(self):
        with tempfile.TemporaryDirectory(prefix='typst-deferred-binding-') as directory:
            root = Path(directory).resolve()
            source = ('#let original = footnote\n#let scope(f) = f(0)\n'
                      '#scope(x => [#let helper(z) = [#let footnote = none]\n'
                      '#metadata(type(footnote) == type(original)) <probe>])')
            (root / 'main.typ').write_text(source)
            result = subprocess.run([shutil.which('typst'), 'query', '--root', str(root),
                '--field', 'value', '--format', 'json', str(root / 'main.typ'), '<probe>'],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [True])

    @unittest.skipUnless(shutil.which('typst'), 'Requires Typst')
    def test_typst_callback_capture_can_change_between_loop_iterations(self):
        with tempfile.TemporaryDirectory(prefix='typst-loop-capture-') as directory:
            root = Path(directory).resolve()
            source = ('#let original = footnote\n#let footnote = footnote\n'
                      '#for n in (0, 1) { let f = x => [#metadata(type(footnote) == type(original)) <probe>]; '
                      'f(0); footnote = none }')
            (root / 'main.typ').write_text(source)
            result = subprocess.run([shutil.which('typst'), 'query', '--root', str(root),
                '--field', 'value', '--format', 'json', str(root / 'main.typ'), '<probe>'],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [True, False])

    def test_opt_in_sequence_reproves_deferred_call_in_final_candidate(self):
        source = (b'#import "styles.typ": *\n#use(x => [#let helper(z) = [Other]\n'
                  b'#block[$ x #dots_space #footnote[Note] $]])')
        self.fixture.source = source
        (self.fixture.project / 'main.typ').write_bytes(source)
        proof, session, _ = self.fixture.capture()
        tree = session._context.source_for('main.typ').tree
        catalog = load_rules()[0]['rules']
        options = dict(profile='conditional-gaps-and-spacers', footnote_evidence=proof)
        strict = sequence.preview(tree, 'main.typ', catalog, **options)
        self.assertEqual(strict['verification'], 'withheld')
        record = sequence.preview(tree, 'main.typ', catalog, conditional_deferred_footnotes=True, **options)
        self.assertNotEqual(record['verification'], 'withheld', record)
        self.assertTrue(record['steps'][-1]['conditional_deferred_footnote_calls'])
        result = sequence.replay_document(session._context, [record], catalog,
            conditional_deferred_footnotes=True, **options)
        self.assertNotIn(b'#dots_space', result['main.typ'])
        with self.assertRaises(SafetyError):
            sequence.replay_document(session._context, [record], catalog, **options)
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_final_reanalysis_rejects_changed_scope_and_facade(self):
        source = b'#import "styles.typ": *\n#use(x => [#let helper(z) = [Other]\n#footnote[Note]])'
        self.fixture.source = source
        (self.fixture.project / 'main.typ').write_bytes(source)
        proof, _, _ = self.fixture.capture()
        offsets = {'main.typ': (source.index(b'#footnote[Note]'),)}
        with self.assertRaises(SafetyError):
            proof.validate_staged_facades({'main.typ': source}, offsets)
        proof.validate_staged_facades({'main.typ': source}, offsets, allow_deferred=True)
        for insertion in (b'#let footnote = none\n', b'#import "styles.typ" as footnote: paragraph_tab\n',
                          b'#import "unknown.typ": *\n'):
            candidate = source.replace(b'#footnote[Note]', insertion + b'#footnote[Note]')
            with self.subTest(insertion=insertion), self.assertRaises(SafetyError):
                proof.validate_staged_facades({'main.typ': candidate},
                    {'main.typ': (candidate.index(b'#footnote[Note]'),)}, allow_deferred=True)
        with self.assertRaises(SafetyError):
            proof.validate_staged_facades({'main.typ': source, 'styles.typ': b'#let other = 1'},
                                          offsets, allow_deferred=True)
        with self.assertRaises(InputError):
            proof.validate_staged_facades({'main.typ': source}, offsets, allow_deferred=1)

    def test_opt_in_rejects_wrong_profile_and_forged_record(self):
        import copy
        source = (b'#import "styles.typ": *\n#use(x => [#let helper(z) = [Other]\n'
                  b'#block[$ x #dots_space #footnote[Note] $]])')
        self.fixture.source = source
        (self.fixture.project / 'main.typ').write_bytes(source)
        proof, session, _ = self.fixture.capture()
        tree = session._context.source_for('main.typ').tree
        catalog = load_rules()[0]['rules']
        with self.assertRaises((InputError, SafetyError)):
            sequence.preview(tree, 'main.typ', catalog, footnote_evidence=proof,
                             conditional_deferred_footnotes=True)
        options = dict(profile='conditional-gaps-and-spacers', footnote_evidence=proof,
                       conditional_deferred_footnotes=True)
        record = sequence.preview(tree, 'main.typ', catalog, **options)
        forged = copy.deepcopy(record)
        forged['steps'][-1]['conditional_deferred_footnote_calls'].clear()
        with self.assertRaises(SafetyError):
            sequence.replay_document(session._context, [forged], catalog, **options)

    def test_composed_modern_remaps_and_rechecks_deferred_call(self):
        import document_composed_modern_highlight as composed
        import document_footnote_evidence as footnote
        import test_document_intermediate_context as modern_fixtures
        case = modern_fixtures.IntermediateContextTests('runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        fixture = case.fixture
        source = (modern_fixtures.fixtures.fixtures.IMPORT + b'#highlighted[Text $x$ end.]\n'
                  b'#use(x => [#let helper(z) = [Other]\n'
                  b'#block[$ x #dots_space #footnote[Note] $]])')
        _, analyzer, snapshot, _ = fixture.issue(source)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        item = analyzer._session._context.source_for('main.typ')
        profile = 'conditional-gaps-and-spacers'
        record = sequence.preview(item.tree, item.path, load_rules()[0]['rules'], profile=profile,
                                  footnote_evidence=proof, conditional_deferred_footnotes=True)
        self.assertTrue(record['steps'][-1]['conditional_deferred_footnote_calls'])
        final, report, _ = composed.stage(proof, [record], profile=profile, sequence_facade_evidence=True,
                                          conditional_deferred_footnotes=True)
        self.assertEqual(report['candidate_count'], 1)
        self.assertNotIn(b'#dots_space', final['project', 'main.typ'])
        offset = final['project', 'main.typ'].index(b'#footnote[Note]')
        self.assertEqual(len(deferred.analyze(proof, 'main.typ', (offset,),
                            candidate=final['project', 'main.typ']).calls), 1)
        with self.assertRaises(SafetyError):
            composed.stage(proof, [record], profile=profile, sequence_facade_evidence=True)

    def test_cli_selects_evidence_and_reports_conditional_scope(self):
        import contextlib
        import io
        from types import SimpleNamespace
        from unittest.mock import patch
        import document_preview_cli as cli
        import document_resources
        source = (b'#import "styles.typ": *\n#use(x => [#let helper(z) = [Other]\n'
                  b'#block[$ x #dots_space #footnote[Note] $]])')
        (self.fixture.project / 'main.typ').write_bytes(source)
        manifest = self.fixture.project.parent / 'manifest.json'
        manifest.write_text(json.dumps({'roots': {'project': str(self.fixture.project),
            'packages': str(self.fixture.packages), 'cache': str(self.fixture.cache)},
            'files': [{'root': 'project', 'path': 'main.typ', 'role': 'source'}]}))
        base = ['--root', str(self.fixture.project), '--entrypoint', 'main.typ', '--manifest', str(manifest)]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(base + ['--conditional-deferred-footnotes'])
        def fake_compile(context, records, catalog, snapshot, **options):
            self.assertIs(options['conditional_deferred_footnotes'], True)
            staged = document_resources.stage_document(context, records, catalog, snapshot,
                profile=options['profile'], footnote_evidence=options['footnote_evidence'],
                conditional_deferred_footnotes=True)
            self.assertNotIn(b'#dots_space', staged['project', 'main.typ'])
            return SimpleNamespace(baseline_sha256='a'*64, candidate_sha256='b'*64,
                pdfs_identical=False, dependency_count=1, unobserved_capture_count=0,
                system_font_files=0, compiler_sha256='c'*64)
        output = io.StringIO()
        with patch.object(cli.document_preview_compile, 'compile_preview', side_effect=fake_compile), \
                contextlib.redirect_stdout(output):
            status = cli.main(base + ['--format', 'json', '--conditional-gaps', '--conditional-spacers',
                                    '--conditional-facade-footnotes', '--conditional-deferred-footnotes'])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(report['conditional_deferred_footnote_preview']['proved_calls'], 1)
        self.assertFalse(report['conditional_deferred_footnote_preview']['runtime_binding_proved'])
        self.assertFalse(report['source_apply_gate']['eligible'] or report['source_files_modified'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_real_compiler_uses_only_staged_deferred_candidate(self):
        import document_preview_compile as compiler
        source = (b'#import "styles.typ": *\n#let dots_space = h(1em)\n#let use(f) = f(0)\n'
                  b'#use(x => [#let helper(z) = [Other]\n#block[$ x #dots_space #footnote[Note] $]])')
        self.fixture.source = source
        (self.fixture.project / 'main.typ').write_bytes(source)
        proof, session, snapshot = self.fixture.capture()
        catalog = load_rules()[0]['rules']
        profile = 'conditional-gaps-and-spacers'
        item = session._context.source_for('main.typ')
        record = sequence.preview(item.tree, item.path, catalog, profile=profile, footnote_evidence=proof,
                                  conditional_deferred_footnotes=True)
        self.assertTrue(record['steps'][-1]['conditional_deferred_footnote_calls'])
        result = compiler.compile_preview(session._context, [record], catalog, snapshot, entrypoint='main.typ',
            profile=profile, footnote_evidence=proof, conditional_deferred_footnotes=True)
        self.assertTrue(result.baseline_sha256 and result.candidate_sha256)
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
