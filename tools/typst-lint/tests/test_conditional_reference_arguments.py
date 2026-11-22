"""Conditional argument checks consume freshly traced reference calls only."""
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_reference_arguments as arguments
import conditional_reference_provenance as provenance
import import_graph
import namespace_packages
from syntax_client import EXECUTABLE
from typst_lint import InputError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ConditionalReferenceArgumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='conditional-reference-arguments-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project, self.packages = self.root / 'project', self.root / 'packages'
        self.project.mkdir()
        fixture = TOOL / 'tests/fixtures/reviewed-packages/local'
        for name, version in (('project-references', '0.1.0'), ('math-blocks', '0.2.0'),
                              ('text-utils', '0.1.2')):
            shutil.copytree(fixture / name / version, self.packages / 'local' / name / version)

    def session(self, body):
        source = b'#import "@local/project-references:0.1.0": project-ref\n' + body
        (self.project / 'main.typ').write_bytes(source)
        graph = import_graph.load_import_graph(self.project, (('main.typ', source),))
        capture = namespace_packages.load_namespace_packages(str(self.packages))
        return provenance.ConditionalReferenceProvenance(graph, capture, str(self.packages))

    def test_known_invalid_arguments_and_unresolved_values(self):
        body = (b'#project-ref("book", <good>)\n'
                b'#project-ref("book", "1.2")\n'
                b'#project-ref(3, <good>)\n'
                b'#project-ref("book", <good>, 3)\n'
                b'#project-ref("book", target: <good>)\n'
                b'#project-ref("book", [@good])\n'
                b'#project-ref("book", unknown)\n'
                b'#project-ref(..values)\n')
        result = arguments.analyze(self.session(body), 'main.typ')
        self.assertEqual(result.status, 'PARTIAL')
        self.assertEqual(len(result.diagnostics), 5, result.unresolved)
        self.assertTrue(any('Spread' in reason for reason in result.unresolved))
        self.assertTrue(any('Target type' in reason for reason in result.unresolved))
        self.assertTrue(all(row.status == 'CONDITIONAL' and row.route and row.conditions
                            and not row.edit_permission for row in result.diagnostics))
        self.assertFalse(result.edit_permission or result.catalog_checked)
        self.assertEqual(len(result.trace.calls), 8)

    def test_shadowed_or_unknown_import_is_not_diagnosed(self):
        for prefix, expected in ((b'#let project-ref = none\n', 'CONDITIONAL'),
                                 (b'#import "other.typ": *\n', 'PARTIAL')):
            with self.subTest(prefix=prefix):
                result = arguments.analyze(self.session(prefix + b'#project-ref("x", "bad")'), 'main.typ')
                self.assertFalse(result.diagnostics)
                self.assertEqual(result.status, expected)

    def test_directive_suppresses_without_granting_edits(self):
        body = (b'// typst-lint: ignore-next core.project-ref-arguments -- intentional\n'
                b'#project-ref("book", "bad")\n')
        result = arguments.analyze(self.session(body), 'main.typ')
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.suppressed), 1)
        self.assertFalse(result.edit_permission)

    def test_unselected_or_forged_session_refuses(self):
        session = self.session(b'#project-ref("book", "bad")')
        result = arguments.analyze(session, 'other.typ')
        self.assertEqual(result.status, 'UNAVAILABLE')
        self.assertFalse(result.diagnostics)
        with self.assertRaises(InputError):
            arguments.analyze(object(), 'main.typ')
        uninitialized = object.__new__(provenance.ConditionalReferenceProvenance)
        invalid = arguments.analyze(uninitialized, 'main.typ')
        self.assertEqual(invalid.status, 'UNAVAILABLE')
        self.assertIn('uninitialized', invalid.unresolved[0])

    def test_caller_cannot_substitute_serialized_call_records(self):
        session = self.session(b'#project-ref("book", "bad")')
        original = session.calls('main.typ')
        self.assertEqual(len(original.calls), 1)
        # An instance-level substitute is ignored; the session's concrete
        # provenance implementation is invoked directly.
        session.calls = lambda path: replace(original, calls=())
        result = arguments.analyze(session, 'main.typ')
        self.assertEqual(len(result.diagnostics), 1)

    def test_invalid_call_span_is_refused_as_a_whole(self):
        session = self.session(b'#project-ref("book", "bad")')
        traced = session.calls('main.typ')
        altered = replace(traced.calls[0], end=traced.calls[0].end - 1)
        with patch.object(provenance.ConditionalReferenceProvenance, 'calls',
                          return_value=replace(traced, calls=(altered,))):
            result = arguments.analyze(session, 'main.typ')
        self.assertEqual(result.status, 'UNAVAILABLE')
        self.assertFalse(result.diagnostics or result.suppressed)

    def test_unicode_positions_and_grouped_values(self):
        result = arguments.analyze(self.session('한글 #project-ref(("book"), ("bad"))'.encode()), 'main.typ')
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual((result.diagnostics[0].line, result.diagnostics[0].column), (2, 27))

    def test_nested_content_and_display_footnote_keep_conditions(self):
        source = (b'#block[See #project-ref("book", "bad")]\n'
                  b'$\n A #footnote[See #project-ref("book", [@bad])]\n$\n')
        result = arguments.analyze(self.session(source), 'main.typ')
        self.assertEqual(len(result.diagnostics), 2, result.unresolved)
        self.assertEqual(result.status, 'PARTIAL')
        self.assertTrue(any('footnote-shaped' in condition for condition in result.diagnostics[1].conditions))


if __name__ == '__main__':
    unittest.main()
