"""Cargo's legitimate hardlinked executable remains usable on Linux."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from syntax_client import EXECUTABLE, parse


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class AdapterArtifactTests(unittest.TestCase):
    def test_hardlinked_known_build_artifact_is_accepted(self):
        # Keep the temporary link on the same filesystem as the build artifact.
        with tempfile.TemporaryDirectory(dir=EXECUTABLE.parent) as directory:
            os.link(EXECUTABLE, Path(directory) / 'linked-artifact')
            self.assertGreaterEqual(EXECUTABLE.stat().st_nlink, 2)
            tree = parse(b'$x^i$')
            self.assertFalse(tree.nodes[0].errors)
            self.assertTrue(any(node.kind == 'MathAttach' for node in tree.nodes))


if __name__ == '__main__':
    unittest.main()
