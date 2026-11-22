"""Conditional scope-generated reference arguments use reviewed provenance."""
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


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ConditionalScopedReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='conditional-scoped-reference-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.project, self.packages = root / 'project', root / 'packages'
        self.project.mkdir()
        fixture = TOOL / 'tests/fixtures/reviewed-packages/local'
        for name, version in (('project-references', '0.1.0'), ('scoped-annotations', '0.3.0')):
            shutil.copytree(fixture / name / version, self.packages / 'local' / name / version)
        shutil.copytree(TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0',
                        self.packages / 'preview/mannot/0.4.0')

    def session(self, body, *, scope_import=b'local-tag-scope'):
        source = (b'#import "@local/project-references:0.1.0": project-ref\n'
                  b'#import "@local/scoped-annotations:0.3.0": ' + scope_import + b'\n' + body)
        (self.project / 'main.typ').write_bytes(source)
        graph = import_graph.load_import_graph(self.project, (('main.typ', source),))
        capture = namespace_packages.load_namespace_packages(str(self.packages))
        return provenance.ConditionalReferenceProvenance(graph, capture, str(self.packages))

    def analyze(self, body, *, scope_import=b'local-tag-scope'):
        return arguments.analyze(self.session(body, scope_import=scope_import), 'main.typ')

    def test_reviewed_scope_target_is_conditional_and_never_an_edit(self):
        for wrapper in (b'local-tag-scope', b'local-scope-annotations'):
            with self.subTest(wrapper=wrapper):
                result = self.analyze(b'#' + wrapper + b'(s => [#project-ref("pde", (s.tag)("energy"))])',
                                      scope_import=wrapper)
                self.assertEqual(len(result.trace.calls), 1)
                self.assertEqual(len(result.diagnostics), 1, result.unresolved)
                finding = result.diagnostics[0]
                self.assertIn('scope-generated', finding.explanation)
                self.assertEqual(finding.status, 'CONDITIONAL')
                self.assertTrue(finding.conditions and finding.package_evidence)
                self.assertTrue({'@local/scoped-annotations:0.3.0', '@preview/mannot:0.4.0'} <=
                                {item.import_path for item in finding.package_evidence})
                self.assertFalse(finding.edit_permission or result.edit_permission or result.catalog_checked)

    def test_suppression_remains_effective(self):
        result = self.analyze(b'#local-tag-scope(s => [\n'
                              b'// typst-lint: ignore-next core.project-ref-arguments -- intentional\n'
                              b'#project-ref("pde", (s.tag)("energy"))\n])')
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.suppressed), 1, result.unresolved)

    def test_dynamic_target_alias_and_wrong_field_stay_unresolved(self):
        for target in (b'(s.tag)(name)', b'(s.name)("energy")', b'(s.tag)(..items)', b'other'):
            with self.subTest(target=target):
                result = self.analyze(b'#local-tag-scope(s => [#project-ref("pde", ' + target + b')])')
                self.assertFalse(result.diagnostics)
                self.assertTrue(any('Target type' in reason for reason in result.unresolved), result.unresolved)

    def test_shadow_and_mutation_do_not_establish_scope(self):
        for body in (b'#local-tag-scope(s => [#let s = other\n#project-ref("pde", (s.tag)("energy"))])',
                     b'#local-tag-scope(s => [#{s = other}\n#project-ref("pde", (s.tag)("energy"))])',
                     b'#local-tag-scope(s => [#s.insert("tag", other)\n#project-ref("pde", (s.tag)("energy"))])',
                     b'#local-tag-scope(s => [#other(s)\n#project-ref("pde", (s.tag)("energy"))])',
                     b'#let local-tag-scope = other\n#local-tag-scope(s => [#project-ref("pde", (s.tag)("energy"))])'):
            with self.subTest(body=body):
                result = self.analyze(body)
                self.assertFalse(result.diagnostics)

    def test_uncaptured_mannot_namespace_withholds_scoped_proof(self):
        shutil.rmtree(self.packages / 'preview/mannot/0.4.0')
        result = self.analyze(b'#local-tag-scope(s => [#project-ref("pde", (s.tag)("energy"))])')
        self.assertFalse(result.diagnostics)

    def test_changed_scoped_package_withholds_scoped_proof(self):
        source = self.packages / 'local/scoped-annotations/0.3.0/lib.typ'
        source.write_bytes(source.read_bytes() + b'\n// changed\n')
        result = self.analyze(b'#local-tag-scope(s => [#project-ref("pde", (s.tag)("energy"))])')
        self.assertFalse(result.diagnostics)

    def test_session_change_during_scope_resolution_discards_all_evidence(self):
        body = (b'#local-tag-scope(s => [\n'
                b'// typst-lint: ignore-next core.project-ref-arguments -- intentional\n'
                b'#project-ref("pde", (s.tag)("energy"))\n])')
        original = arguments.scoped_provenance.resolve
        for raises in (False, True):
            with self.subTest(raises=raises):
                session = self.session(body)

                def changed(*args, **kwargs):
                    resolver = original(*args, **kwargs)
                    session._selection_reason = 'changed during analysis'
                    if raises:
                        raise RuntimeError('capture changed while resolving')
                    return resolver

                with patch.object(arguments.scoped_provenance, 'resolve', side_effect=changed):
                    result = arguments.analyze(session, 'main.typ')
                self.assertEqual(result.status, 'UNAVAILABLE')
                self.assertEqual(result.unresolved, ('Session capture changed during inspection',))
                self.assertFalse(result.diagnostics or result.suppressed or result.trace)
                self.assertFalse(result.edit_permission or result.catalog_checked)

    def test_session_change_after_trace_discards_non_scoped_findings(self):
        session = self.session(b'#project-ref("pde", "not-a-label")')
        original = arguments.parse

        def changed(source):
            result = original(source)
            session._selection_reason = 'changed after trace'
            return result

        with patch.object(arguments, 'parse', side_effect=changed):
            result = arguments.analyze(session, 'main.typ')
        self.assertEqual(result.status, 'UNAVAILABLE')
        self.assertEqual(result.unresolved, ('Session capture changed during inspection',))
        self.assertFalse(result.diagnostics or result.suppressed or result.trace)


if __name__ == '__main__':
    unittest.main()
