"""Retain strict include evidence checks and the second node read."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import include_context as capture
from syntax_client import EXECUTABLE


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class IncludeSignatureInlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-include-inline-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        source = 'Text $x^2$ α'.encode()
        (self.root / 'main.typ').write_bytes(source)
        self.context = capture.load_include_context(self.root, (('main.typ', source),))
        self.item = self.context.source_for('main.typ')
        self.node = self.item.tree.nodes[0]

    def test_forged_node_fields_are_rejected(self):
        count = len(self.item.tree.nodes)
        class StrSubclass(str):
            pass
        cases = (
            ('kind', StrSubclass('Markup')), ('kind', 'a' * 81),
            ('kind', '가' * 27), ('kind', '\ud800'),
            ('start', True), ('start', -1), ('start', len(self.item.source) + 1),
            ('end', True), ('end', -1), ('end', len(self.item.source) + 1),
            ('errors', 0), ('display', 0),
            ('children', []), ('children', (True,)), ('children', (-1,)),
            ('children', (count + 1,)), ('children', (0,) * (count + 1)),
            ('operands', []), ('operands', ((), (), ())),
            ('operands', (('top',),)), ('operands', (('invalid', 0),)),
            ('operands', ((StrSubclass('top'), 0),)), ('operands', (('top', True),)),
            ('operands', (('bottom', -1),)), ('operands', (('top', count + 1),)),
        )
        for name, value in cases:
            original = getattr(self.node, name)
            with self.subTest(field=name, value=repr(value)):
                try:
                    object.__setattr__(self.node, name, value)
                    with self.assertRaises(capture.IncludeCaptureError):
                        capture.assert_include_context(self.context)
                finally:
                    object.__setattr__(self.node, name, original)
                capture.assert_include_context(self.context)

    def test_mutation_between_node_validation_and_tree_hash_is_rejected(self):
        original_digest = capture.digest
        original_start = self.node.start
        observed = []
        def mutate_after_node_validation(data):
            if data is self.item.source and not observed:
                observed.append(True)
                object.__setattr__(self.node, 'start', original_start + 1)
            return original_digest(data)
        try:
            with patch.object(capture, 'digest', side_effect=mutate_after_node_validation):
                with self.assertRaises(capture.IncludeCaptureError):
                    capture.assert_include_context(self.context)
            self.assertTrue(observed)
        finally:
            object.__setattr__(self.node, 'start', original_start)
        capture.assert_include_context(self.context)


if __name__ == '__main__':
    unittest.main()
