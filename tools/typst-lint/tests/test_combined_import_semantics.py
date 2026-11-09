"""Owned Typst fixtures establish combined import binding order."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('typst'), 'Requires Typst')
class CombinedImportSemanticsTests(unittest.TestCase):
    def test_module_alias_is_bound_before_named_and_wildcard_items(self):
        with tempfile.TemporaryDirectory(prefix='typst-combined-import-') as directory:
            root = Path(directory).resolve()
            (root / 'module.typ').write_text('#let item = 17\n#let footnote = 23\n')
            cases = (
                ('#let old = footnote\n#import "module.typ" as footnote: item\n'
                 '#metadata((same: type(footnote) == type(old), item: item)) <probe>',
                 {'same': False, 'item': 17}),
                ('#import "module.typ" as footnote: item as footnote\n'
                 '#metadata(footnote) <probe>', 17),
                ('#import "module.typ" as footnote: *\n#metadata(footnote) <probe>', 23),
                ('#import "module.typ" as other: item\n'
                 '#metadata((other.item, item)) <probe>', [17, 17]),
            )
            for source, expected in cases:
                with self.subTest(source=source):
                    (root / 'main.typ').write_text(source)
                    run = subprocess.run([shutil.which('typst'), 'query', '--root', str(root),
                        '--field', 'value', '--format', 'json', str(root / 'main.typ'), '<probe>'],
                        capture_output=True, text=True, timeout=15)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertEqual(json.loads(run.stdout), [expected])


if __name__ == '__main__':
    unittest.main()
