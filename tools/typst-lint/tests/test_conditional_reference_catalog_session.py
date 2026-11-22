"""Independent project-key diagnosis and sealed catalog inspection."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_reference_catalog as check
import conditional_reference_provenance as provenance
import import_graph
import namespace_packages
from syntax_client import EXECUTABLE


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ConditionalProjectKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='conditional-project-key-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project, self.packages = self.root / 'project', self.root / 'packages'
        self.project.mkdir()
        fixture = TOOL / 'tests/fixtures/reviewed-packages/local/project-references/0.1.0'
        shutil.copytree(fixture, self.packages / 'local/project-references/0.1.0')
        raw = json.dumps({'schema': 1, 'projects': {'book': {'references': {
            'known': {'page': 1, 'text': 'Known'}}, 'ambiguous_labels': []}}}).encode()
        self.catalog = check.capture_catalog(raw, 'explicit-fixture.json')

    def session(self, body):
        source = b'#import "@local/project-references:0.1.0": project-ref\n' + body
        (self.project / 'main.typ').write_bytes(source)
        graph = import_graph.load_import_graph(self.project, (('main.typ', source),))
        capture = namespace_packages.load_namespace_packages(str(self.packages))
        return provenance.ConditionalReferenceProvenance(graph, capture, str(self.packages))

    def test_unknown_key_with_unresolved_target_is_reported_without_label_claim(self):
        source = (b'#project-ref(("missing"), target)\n'
                  b'#project-ref("book", target)\n'
                  b'#project-ref(project, target)\n'
                  b'#project-ref("missing", "not a label")\n'
                  b'#project-ref("missing", target: value)\n')
        result = check.analyze(self.session(source), 'main.typ', self.catalog)
        self.assertEqual(result.status, 'PARTIAL')
        self.assertEqual(result.checked_calls, 0)
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        self.assertIn("no project 'missing'", result.diagnostics[0].explanation)
        self.assertTrue(any('literal project and global label' in reason for reason in result.unresolved))
        self.assertFalse(result.edit_permission or result.freshness_proved or result.runtime_selection_proved)

    def test_suppression_applies_to_unknown_key(self):
        body = (b'// typst-lint: ignore-next core.project-ref-catalog-target -- intentional\n'
                b'#project-ref("missing", target)\n')
        result = check.analyze(self.session(body), 'main.typ', self.catalog)
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual(result.checked_calls, 0)

    def test_provenance_mutation_after_argument_inspection_discards_results(self):
        original = check.parse
        for raises in (False, True):
            with self.subTest(raises=raises):
                session = self.session(b'#project-ref("missing", target)')

                def changed(source):
                    tree = original(source)
                    session._selection_reason = 'changed during catalog check'
                    if raises:
                        raise RuntimeError('changed while parsing')
                    return tree

                with patch.object(check, 'parse', side_effect=changed):
                    result = check.analyze(session, 'main.typ', self.catalog)
                self.assertEqual(result.status, 'UNAVAILABLE')
                self.assertEqual(result.unresolved, ('Session capture changed during inspection',))
                self.assertFalse(result.diagnostics or result.suppressed or result.trace or result.argument_result)
                self.assertFalse(result.checked_calls or result.catalog_identity or result.catalog_sha256)

    def test_catalog_mutation_on_early_return_discards_results(self):
        session = self.session(b'#project-ref("missing", target)')
        original = check.parse

        def changed(source):
            object.__setattr__(self.catalog.catalog.projects[0], 'alias', 'changed')
            original(source)
            raise ValueError('synthetic parse failure')

        # Even an ordinary parse failure, normally an early unavailable return,
        # must discard evidence when the catalog snapshot changed meanwhile.
        with patch.object(check, 'parse', side_effect=changed):
            result = check.analyze(session, 'main.typ', self.catalog)
        self.assertEqual(result.status, 'UNAVAILABLE')
        self.assertEqual(result.unresolved, ('Catalog capture changed during inspection',))
        self.assertFalse(result.diagnostics or result.suppressed or result.trace or result.argument_result)


if __name__ == '__main__':
    unittest.main()
