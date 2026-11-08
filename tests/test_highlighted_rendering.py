#!/usr/bin/env python3
"""Check that moving a font-size scope inside highlighted preserves rendering."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#import "@local/text-utils:0.1.2": highlighted
#set page(width: 12cm, height: auto, margin: 12pt)
#set text(font: sys.inputs.at("font"), size: 12pt)
#set text(fill: if sys.inputs.at("theme") == "dark" {
  rgb("#e8edf3")
} else { black })
#let sample(body, size) = if sys.inputs.at("renderer") == "inner" {
  highlighted(text(size: size, body))
} else {
  text(size: size, highlighted(body))
}
#for size in (8pt, 10pt, 12pt, 18pt, 24pt, 0.6em, 1.6em) {
  block(sample([Agjp Render test], size))
  block(sample([
    A #strong[bold] and #emph[italic] #link("https://example.com")[linked] word.
  ], size))
  block(width: 7cm, sample([
    This highlighted sentence wraps across several lines and includes ordinary
    words and a short ending.
  ], size))
}
'''


class HighlightedRenderingTests(unittest.TestCase):
    def test_inner_and_outer_size_scopes_render_identically(self):
        # Compare actual SVG geometry and glyph placement, not macro fields:
        # highlighted[text(size: n)[...]] must match text(size: n)[highlighted[...]].
        fonts = subprocess.run(
            ["typst", "fonts"], check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        with tempfile.TemporaryDirectory(prefix="typst-highlighted-") as folder:
            root = Path(folder)
            source = root / "sizes.typ"
            source.write_text(FIXTURE)
            for font in ("Times New Roman", "New Computer Modern", "Arial"):
                for theme in ("light", "dark"):
                    with self.subTest(font=font, theme=theme):
                        if font not in fonts:
                            self.skipTest(f"{font} is not installed")
                        rendered = []
                        for renderer in ("inner", "outer"):
                            output = root / f"{renderer}.svg"
                            subprocess.run(
                                [
                                    "typst", "compile", "--package-path", str(REPO_ROOT),
                                    "--input", f"font={font}",
                                    "--input", f"theme={theme}",
                                    "--input", f"renderer={renderer}",
                                    str(source), str(output),
                                ],
                                check=True, capture_output=True, text=True,
                            )
                            svg = ElementTree.parse(output).getroot()
                            rendered.append([
                                (element.tag, element.attrib) for element in svg.iter()
                            ])
                        self.assertEqual(rendered[0], rendered[1])


if __name__ == "__main__":
    unittest.main()
