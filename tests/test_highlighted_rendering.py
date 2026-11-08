#!/usr/bin/env python3
"""Check highlight geometry, mode composition, and inherited math styles."""

from pathlib import Path
import re
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

CONTENT_FIXTURE = r'''
#import "@local/text-utils:0.1.2": highlighted
#let dark = sys.inputs.at("theme", default: "light") == "dark"
#let fill = if dark { rgb("#665c1f") } else { rgb("#FFFE80") }
#set page(width: eval(sys.inputs.at("width", default: "900pt")), height: auto, margin: 12pt)
#set text(font: "New Computer Modern", size: 12pt,
  fill: if dark { rgb("#e8edf3") } else { black })
#show math.equation: set text(font: sys.inputs.at("math-font", default: "New Computer Modern Math"))
#let bodies = (
  string: "A plain string.",
  equation: [$x>0$],
  prose: [A *bold* and _italic_ #link("https://example.com")[linked] word;
    #text(size: 0.6em)[small] and #text(size: 1.6em)[Agjp gyqp].],
  mixed: [*Bold $x^2$* and _italic $1/(1+x^2)$_,
    #link("https://example.com")[$sqrt(1+x^2)$],
    $(a/(b+c))/(1+d^2)$, $integral_0^1 f(x) dif x$;
    #text(size: 0.6em)[small $x^2$] and #text(size: 1.6em)[Agjp gyqp]],
)
#let body = bodies.at(sys.inputs.at("case", default: "mixed"))
#let renderer = sys.inputs.at("renderer", default: "default")
#let render() = {
  if renderer == "plain" { body }
  else if renderer == "native" {
    highlight(fill: fill, top-edge: "ascender", bottom-edge: "descender", body)
  }
  else if renderer == "default" { highlighted(body) }
  else if renderer == "parentheses" { [#highlighted()[#body]] }
  else if renderer == "adjacent" {
    highlighted[Before ] + highlighted(body, mode: "phrase") + highlighted[ after.]
  }
  else { highlighted(body, mode: renderer) }
}
#render()
'''


def render_svg(root, fixture, **inputs):
    source, output = root / "case.typ", root / "case.svg"
    source.write_text(fixture)
    command = ["typst", "compile", "--package-path", str(REPO_ROOT)]
    for key, value in inputs.items():
        command += ["--input", f"{key}={value}"]
    subprocess.run(command + [str(source), str(output)],
                   check=True, capture_output=True, text=True)
    return ElementTree.parse(output).getroot()


def svg_elements(svg):
    return [(element.tag, element.attrib) for element in svg.iter()]


def glyph_shapes(svg):
    """Compare actual glyph outlines and scale, allowing layout translations."""
    definitions = {el.get("id"): el for el in svg.iter() if el.get("id")}
    result = []

    def multiply(a, b):
        return (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
                a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3])

    def visit(el, linear=(1, 0, 0, 1)):
        if el.tag.endswith("}defs"):
            return
        for kind, args in re.findall(r"(\w+)\(([^)]*)\)", el.get("transform", "")):
            values = [float(x) for x in re.split(r"[ ,]+", args.strip())]
            if kind == "matrix":
                linear = multiply(linear, tuple(values[:4]))
            elif kind == "scale":
                linear = multiply(linear, (values[0], 0, 0, values[-1]))
            elif kind != "translate":
                raise AssertionError(f"Unexpected SVG transform: {kind}")
        if el.tag.endswith("}use"):
            ref = el.attrib["{http://www.w3.org/1999/xlink}href"][1:]
            result.append((ElementTree.tostring(definitions[ref]), linear, el.get("fill")))
        for child in el:
            visit(child, linear)

    visit(svg)
    return result


class HighlightedRenderingTests(unittest.TestCase):
    def test_native_prose_and_compatible_call_forms(self):
        with tempfile.TemporaryDirectory(prefix="typst-highlighted-api-") as folder:
            root = Path(folder)
            for case in ("string", "equation", "prose"):
                expected = render_svg(root, CONTENT_FIXTURE, case=case, renderer="default")
                for renderer in ("flow", "parentheses"):
                    with self.subTest(case=case, renderer=renderer):
                        actual = render_svg(root, CONTENT_FIXTURE, case=case, renderer=renderer)
                        self.assertEqual(svg_elements(actual), svg_elements(expected))
                if case != "equation":
                    native = render_svg(root, CONTENT_FIXTURE, case=case, renderer="native")
                    self.assertEqual(svg_elements(expected), svg_elements(native))

    def test_inline_equations_preserve_glyphs_and_inherited_math_fonts(self):
        fonts = subprocess.run(
            ["typst", "fonts"], check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        references = []
        with tempfile.TemporaryDirectory(prefix="typst-highlighted-math-") as folder:
            root = Path(folder)
            for font in ("New Computer Modern Math", "STIX Two Math"):
                if font not in fonts:
                    continue
                reference = glyph_shapes(render_svg(
                    root, CONTENT_FIXTURE, renderer="plain", **{"math-font": font}))
                references.append(reference)
                for renderer in ("flow", "phrase"):
                    with self.subTest(font=font, renderer=renderer):
                        actual = render_svg(root, CONTENT_FIXTURE, renderer=renderer,
                                            **{"math-font": font})
                        self.assertEqual(glyph_shapes(actual), reference)
            if len(references) == 2:
                self.assertNotEqual(references[0], references[1])
            self.assertTrue(references, "No math font was available for the glyph check")

    def test_phrase_keeps_one_background_and_its_contour_in_adjacent_flow(self):
        with tempfile.TemporaryDirectory(prefix="typst-highlighted-phrase-") as folder:
            root = Path(folder)
            for theme, fill in (("light", "#fffe80"), ("dark", "#665c1f")):
                for width in ("900pt", "220pt"):
                    with self.subTest(theme=theme, width=width):
                        phrase = render_svg(root, CONTENT_FIXTURE, renderer="phrase",
                                            theme=theme, width=width)
                        adjacent = render_svg(root, CONTENT_FIXTURE, renderer="adjacent",
                                              theme=theme, width=width)
                        backgrounds = lambda svg: [
                            el for el in svg.iter() if el.get("fill") == fill
                        ]
                        boxes = backgrounds(phrase)
                        combined = backgrounds(adjacent)
                        self.assertEqual(len(boxes), 1)
                        if width == "900pt":
                            self.assertEqual(len(combined), 3)
                        # Wrapped flow can paint more than one rectangle per
                        # segment. The phrase must still contain only one fill.
                        rounded = [el for el in combined if "c " in el.get("d", "")]
                        self.assertEqual(len(rounded), 1)
                        self.assertEqual(rounded[0].get("d"), boxes[0].get("d"))
                        parents = {child: parent for parent in adjacent.iter() for child in parent}
                        self.assertEqual(len(backgrounds(parents[rounded[0]])), 1)

    def test_unknown_modes_fail_clearly(self):
        with tempfile.TemporaryDirectory(prefix="typst-highlighted-mode-") as folder:
            with self.assertRaises(subprocess.CalledProcessError) as error:
                render_svg(Path(folder), CONTENT_FIXTURE, renderer="unsupported")
            self.assertIn('mode must be "flow" or "phrase"', error.exception.stderr)

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
