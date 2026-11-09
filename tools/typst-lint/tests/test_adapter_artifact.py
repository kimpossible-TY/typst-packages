"""Cargo's legitimate hardlinked executable remains usable on Linux."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import syntax_client

EXECUTABLE = syntax_client.EXECUTABLE
TOOL = Path(__file__).resolve().parents[1]


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class AdapterArtifactTests(unittest.TestCase):
    def test_hardlinked_known_build_artifact_is_accepted(self):
        # Linking the real adapter changes its ctime and poisons this process's
        # security pin. Exercise identical bytes and a real hardlink entirely
        # in a private tool tree, then let its parser process exit before cleanup.
        parent_identity = syntax_client._pin_adapter()
        with tempfile.TemporaryDirectory(prefix='typst-hardlinked-adapter-') as directory:
            root = Path(directory).resolve()
            for name in ('syntax_client.py', 'typst_lint.py'):
                shutil.copy2(TOOL / name, root / name)
            adapter = root / 'syntax-adapter' / 'target' / EXECUTABLE.parent.name / EXECUTABLE.name
            adapter.parent.mkdir(parents=True)
            shutil.copy2(EXECUTABLE, adapter)
            link = root / 'linked-artifact'
            os.link(adapter, link)
            self.assertGreaterEqual(adapter.stat().st_nlink, 2)
            self.assertTrue(os.path.samefile(adapter, link))
            script = ('import pathlib,sys; sys.path.insert(0,str(pathlib.Path.cwd())); '
                      'from syntax_client import EXECUTABLE,parse; '
                      'assert EXECUTABLE.stat().st_nlink >= 2; '
                      'tree=parse(b"$x^i$"); '
                      'assert not tree.nodes[0].errors; '
                      'assert any(node.kind == "MathAttach" for node in tree.nodes)')
            result = subprocess.run([sys.executable, '-I', '-B', '-c', script], cwd=root,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(syntax_client._pin_adapter(), parent_identity)


if __name__ == '__main__':
    unittest.main()
