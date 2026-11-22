"""Only captured, source-matched footnotes can enrich punctuation hints."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_footnote_binding as binding
import conditional_footnote_punctuation as punctuation
import include_context
import namespace_multiroot
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalFootnotePunctuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='conditional-footnote-punctuation-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.project, self.packages, self.cache = (root / name for name in ('project', 'packages', 'cache'))
        for directory in (self.project, self.packages, self.cache):
            directory.mkdir()
        fixture = TOOL / 'tests/fixtures/reviewed-packages/local/text-utils/0.1.2'
        shutil.copytree(fixture, self.packages / 'local/text-utils/0.1.2')

    def session(self, body, facade=b'#import "@local/text-utils:0.1.2": *\n'):
        (self.project / 'styles.typ').write_bytes(facade)
        (self.project / 'main.typ').write_bytes(body)
        context = include_context.load_include_context(self.project, (('main.typ', body),))
        capture = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        return binding.ConditionalFootnoteBinding(context, capture)

    def test_proven_call_preserves_source_and_conditional_evidence(self):
        body = b'#import "styles.typ": *\n#block[Sentence. #footnote[Note.]]\n'
        result = punctuation.analyze(self.session(body), 'main.typ')
        self.assertEqual(result.status, 'PARTIAL')
        self.assertEqual(result.source_sha256, digest(body))
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        finding = result.diagnostics[0]
        self.assertEqual(body[finding.call.start:finding.call.end], b'#footnote[Note.]')
        self.assertEqual(finding.call.source_sha256, digest(body))
        self.assertEqual([source.path for source in finding.call.facade_sources], ['styles.typ'])
        self.assertTrue(finding.call.package_evidence and finding.conditions)
        self.assertFalse(finding.runtime_binding_proved or finding.edit_permission or finding.compiler_permission)
        self.assertFalse(result.coverage_complete or result.edit_permission or result.compiler_permission)
        self.assertEqual((self.project / 'main.typ').read_bytes(), body)

    def test_suppression_and_shadowed_or_unavailable_facade(self):
        suppressed = (b'#import "styles.typ": *\n'
                      b'// typst-lint: ignore-next pde.prose-footnote-punctuation -- fixture\n'
                      b'#block[Sentence. #footnote[Note.]]\n')
        result = punctuation.analyze(self.session(suppressed), 'main.typ')
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.suppressed), 1)
        use = b'#import "styles.typ": *\n#block[Sentence. #footnote[Note.]]\n'
        for facade in (b'#let footnote = none\n', b'#import "missing.typ": *\n'):
            with self.subTest(facade=facade):
                result = punctuation.analyze(self.session(use, facade), 'main.typ')
                self.assertFalse(result.diagnostics or result.suppressed)
                self.assertTrue(result.unresolved)

    def test_unhandled_footnote_shape_does_not_hide_other_proven_findings(self):
        source = (b'#import "styles.typ": *\n'
                  b'#block[Sentence. #footnote[Note.] Another. #footnote([Other.])]\n')
        session = self.session(source)
        self.assertEqual(len(session.analyze('main.typ').calls), 2)
        result = punctuation.analyze(session, 'main.typ')
        self.assertEqual(result.status, 'PARTIAL')
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        call = result.diagnostics[0].call
        self.assertEqual(source[call.start:call.end], b'#footnote[Note.]')
        self.assertTrue(any('unsupported argument form' in reason for reason in result.unresolved))

    def test_stale_unissued_unselected_and_instance_substitution_are_refused(self):
        body = b'#import "styles.typ": *\n#block[Sentence. #footnote[Note.]]\n'
        session = self.session(body)
        session.analyze = lambda path: binding.BindingResult('CONDITIONAL', calls=('forged',))
        self.assertEqual(len(punctuation.analyze(session, 'main.typ').diagnostics), 1)
        self.assertEqual(punctuation.analyze(session, 'other.typ').status, 'UNAVAILABLE')
        self.assertEqual(punctuation.analyze(object.__new__(binding.ConditionalFootnoteBinding), 'main.typ').status,
                         'UNAVAILABLE')
        object.__setattr__(session._graph.sources[0], 'source', b'changed')
        result = punctuation.analyze(session, 'main.typ')
        self.assertEqual(result.status, 'UNAVAILABLE')
        self.assertFalse(result.diagnostics or result.suppressed or result.source_sha256)
        with self.assertRaises(InputError):
            punctuation.analyze(object(), 'main.typ')

    def test_capture_changed_during_inspection_never_returns_findings(self):
        body = b'#import "styles.typ": *\n#block[Sentence. #footnote[Note.]]\n'
        session = self.session(body)
        original = punctuation.resolve
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            object.__setattr__(session._graph.sources[0], 'source', b'changed')
            return result
        with patch.object(punctuation, 'resolve', side_effect=changed):
            result = punctuation.analyze(session, 'main.typ')
        self.assertEqual(result.status, 'UNAVAILABLE')
        self.assertFalse(result.diagnostics or result.suppressed or result.source_sha256)

    def test_batch_matches_individual_and_checks_the_seal_only_twice(self):
        body = b'#import "styles.typ": *\n#block[Sentence. #footnote[Note.]]\n'
        session = self.session(body)
        expected = tuple((source.path, punctuation.analyze(session, source.path))
                         for source in session._context.sources)
        with patch.object(binding, '_valid', wraps=binding._valid) as valid:
            batch = punctuation.analyze_all(session, max_items=10000)
        self.assertEqual(batch.results, expected)
        self.assertEqual(valid.call_count, 2)
        self.assertFalse(batch.coverage_complete or batch.edit_permission or batch.compiler_permission)
        with self.assertRaises(InputError):
            punctuation.analyze_all(session, max_items=0)

    def test_batch_discards_prior_results_if_capture_changes(self):
        body = b'#import "styles.typ": *\n#block[Sentence. #footnote[Note.]]\n'
        for fail in (False, True):
            with self.subTest(fail=fail):
                session = self.session(body)
                original = punctuation._analyze
                def changed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    object.__setattr__(session._graph.sources[0], 'source', b'changed')
                    if fail:
                        raise RuntimeError('changed during batch')
                    return result
                with patch.object(punctuation, '_analyze', side_effect=changed):
                    result = punctuation.analyze_all(session, max_items=10000)
                self.assertEqual(result.status, 'UNAVAILABLE')
                self.assertFalse(result.results)


if __name__ == '__main__':
    unittest.main()
