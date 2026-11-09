"""Document inspection exposes detached annotations only as conditional hints."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from syntax_client import EXECUTABLE


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DetachedAnnotationCliTests(unittest.TestCase):
    def test_opt_in_reports_diagnostic_without_writing(self):
        source = (b'#local-scope-annotations(s => [\n'
                  b'$ x #(s.tag)("x") $\n\n#(s.annot)(("x",), cetz, { draw })\n])')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'main.typ'
            target.write_bytes(source)
            base = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                    '--root', str(root), '--entrypoint', 'main.typ', '--rule-set', 'pde', '--format', 'json']
            ordinary = subprocess.run(base, capture_output=True, text=True, timeout=15)
            self.assertEqual(ordinary.returncode, 2, ordinary.stderr)
            self.assertNotIn('conditional_detached_annotations', json.loads(ordinary.stdout))
            result = subprocess.run(base + ['--preview-conditional-scoped-calls'],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(result.stdout)
            section = report['conditional_detached_annotations']
            row, = section['by_file']
            finding, = row['diagnostics']
            self.assertEqual(finding['rule_id'], 'core.annotation-in-display')
            self.assertFalse(section['edit_permission'] or section['compiler_permission']
                             or section['coverage_complete'] or row['execution_proved'])
            self.assertFalse(report['applied_fixes'] or report['compiler_invoked'])
            self.assertIn('core.annotation-in-display', report['coverage']['uninspected'])
            self.assertEqual(target.read_bytes(), source)
            text_cmd = base[:-2] + ['--format', 'text', '--preview-conditional-scoped-calls']
            text_result = subprocess.run(text_cmd, capture_output=True, text=True, timeout=15)
            self.assertEqual(text_result.returncode, 2, text_result.stderr)
            self.assertIn('main.typ: PARTIAL; 1 hints', text_result.stdout)
            self.assertIn('condition: The wrapper resolves', text_result.stdout)
            self.assertEqual(target.read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
