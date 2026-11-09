"""Nested prose footnote gap previews stay independent and read-only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from syntax_client import EXECUTABLE
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ProseFootnoteGapCliTests(unittest.TestCase):
    def test_opt_in_preview_is_separate_and_does_not_write(self):
        source = b'#block[Text.#footnote[Reason.]\n\n#paragraph_tab\nNext.]'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'main.typ'
            target.write_bytes(source)
            base = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                    '--root', str(root), '--entrypoint', 'main.typ', '--rule-set', 'pde', '--format', 'json']
            ordinary = subprocess.run(base, capture_output=True, text=True, timeout=15)
            self.assertEqual(ordinary.returncode, 2, ordinary.stderr)
            self.assertNotIn('conditional_prose_footnote_gap_previews', json.loads(ordinary.stdout))
            result = subprocess.run(base + ['--preview-conditional-gaps'], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(result.stdout)
            row, = report['conditional_prose_footnote_gap_previews']
            edit, = row['edits']
            self.assertEqual(row['baseline_sha256'], digest(source))
            self.assertEqual(edit['replacement'], '#parbreak()\n')
            self.assertEqual(edit['rule_id'], 'pde.footnote-trailing-gap')
            self.assertFalse(row['runtime_binding_proved'] or row['render_equivalence_proved']
                             or row['edit_permission'] or row['compiler_permission'])
            self.assertFalse(report['applied_fixes'] or report['compiler_invoked'])
            self.assertEqual(target.read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
