"""Opt-in real reference proposal -> isolated fixture render/query evidence.

The test copies the exact reviewed package definition into a temporary package
path and supplies fabricated matching catalogs. Only the test applies its CLI
proposal to its own fixture. No package installation, real catalog, consumer
document or URL fetch is involved; production writes/compilation stay disabled.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from reference_rules import ARGUMENTS
from static_resolver import PROJECT_REFERENCES, PROJECT_REFERENCES_SOURCE_SHA256
from syntax_client import EXECUTABLE
from typst_lint import digest


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst and the syntax adapter')
class ReferenceRenderTests(unittest.TestCase):
    def test_actual_cli_candidate_renders_exact_unique_catalog_link(self):
        source = (b'#import "@local/project-references:0.1.0": project-ref\n'
                  b'#project-ref("pde", [@energy])\n')
        expected = source.replace(b'[@energy]', b'<energy>')
        data = {'schema': 1, 'projects': {
            'other': {'title': 'Other Project', 'pdf_url': 'https://example.invalid/other.pdf',
                      'references': {'energy': {'page': 99, 'text': 'Other Theorem'}},
                      'ambiguous_labels': []},
            'pde': {'title': 'Fixture PDE', 'pdf_url': 'https://example.invalid/pde.pdf',
                    'references': {'energy': {'page': 7, 'text': 'Theorem 2.4'},
                                   'other': {'page': 33, 'text': 'Other Target'}},
                    'ambiguous_labels': []}}}
        catalog_raw = json.dumps(data).encode('utf-8')
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory).resolve()
            consumer, packages, cache = (temporary / name for name in ('consumer', 'packages', 'cache'))
            consumer.mkdir()
            cache.mkdir()
            package = packages / 'local/project-references/0.1.0'
            package.mkdir(parents=True)
            originals = {}
            for filename in ('lib.typ', 'typst.toml'):
                raw = (REPO / 'local/project-references/0.1.0' / filename).read_bytes()
                (package / filename).write_bytes(raw)
                originals[filename] = raw
            self.assertEqual(digest(originals['lib.typ']), PROJECT_REFERENCES_SOURCE_SHA256)
            # This runtime catalog is fabricated and exists only in the test's
            # copied package. It is not an input that the linter follows.
            (package / 'projects.json').write_bytes(catalog_raw)
            originals['projects.json'] = catalog_raw
            configured_catalog = consumer / 'catalog.json'
            configured_catalog.write_bytes(catalog_raw)
            config = consumer / '.typst-lint.toml'
            config_raw = b'rule-set="core"\ncatalog="catalog.json"\n'
            config.write_bytes(config_raw)
            input_file, pdf = consumer / 'fixture.typ', consumer / 'fixture.pdf'
            input_file.write_bytes(source)
            pdf.write_bytes(b'old fixture output')

            command = [sys.executable, str(TOOL / 'typst-lint'), 'fix', '--dry-run',
                       '--root', str(consumer), '--package-path', str(packages), '--format', 'json']
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 2)
            self.assertFalse(report['coverage']['complete'])
            self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
            self.assertEqual(report['proposal_status'], 'partial-not-applicable-transaction')
            self.assertEqual(input_file.read_bytes(), source)
            self.assertEqual(pdf.read_bytes(), b'old fixture output')
            self.assertEqual(report['hashes']['reference_catalog'], digest(catalog_raw))
            self.assertEqual(len(report['package_evidence']), 1)
            evidence = report['package_evidence'][0]
            self.assertEqual(evidence['import_path'], PROJECT_REFERENCES)
            self.assertEqual(evidence['source_identity'], str(package / 'lib.typ'))
            self.assertEqual(evidence['source_sha256'], PROJECT_REFERENCES_SOURCE_SHA256)
            self.assertEqual({d['rule_id'] for d in report['diagnostics']}, {ARGUMENTS})
            self.assertEqual(len(report['proposed_edits']), 1)
            proposal = report['proposed_edits'][0]
            self.assertEqual(proposal['rule_id'], ARGUMENTS)
            self.assertEqual(proposal['path'], 'fixture.typ')
            self.assertEqual(proposal['baseline_sha256'], digest(source))
            self.assertEqual(source[proposal['start']:proposal['end']], b'[@energy]')
            edit = Edit(proposal['start'], proposal['end'], proposal['replacement'].encode('utf-8'))
            candidate = plan(source, (edit,)).candidate()
            self.assertEqual(candidate, expected)
            input_file.write_bytes(candidate)  # Apply to this test's isolated fixture only.

            second = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(second.returncode, 2, second.stderr)
            repeated = json.loads(second.stdout)
            self.assertFalse(repeated['proposed_edits'] or repeated['diagnostics'])
            self.assertFalse(repeated['compiler_invoked'] or repeated['applied_fixes'])
            self.assertEqual(input_file.read_bytes(), candidate)
            self.assertEqual(pdf.read_bytes(), b'old fixture output')

            options = ['--root', str(consumer), '--package-path', str(packages),
                       '--package-cache-path', str(cache)]
            subprocess.run([shutil.which('typst'), 'compile', *options, str(input_file), str(pdf)],
                           check=True, capture_output=True, timeout=60)
            self.assertTrue(pdf.read_bytes().startswith(b'%PDF-'))
            query = subprocess.run([shutil.which('typst'), 'query', *options, str(input_file), 'link'],
                                   check=True, capture_output=True, timeout=60)
            links = json.loads(query.stdout)
            self.assertEqual(links, [{'func': 'link', 'dest': 'https://example.invalid/pde.pdf#page=7',
                                     'body': {'func': 'text', 'text': 'Theorem 2.4 of Fixture PDE'}}])
            self.assertEqual(input_file.read_bytes(), candidate)
            self.assertEqual(config.read_bytes(), config_raw)
            self.assertEqual(configured_catalog.read_bytes(), catalog_raw)
            for filename, original in originals.items():
                self.assertEqual((package / filename).read_bytes(), original)
            self.assertEqual(list(cache.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
