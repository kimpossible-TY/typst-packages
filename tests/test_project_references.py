"""Compile independent source/consumer documents and verify catalog refreshes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "export_references", ROOT / "tools/project-references/export_references.py",
)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)
PACKAGE = ROOT / "local/project-references/0.1.0"


class ProjectReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="project-references-test-")
        self.root = Path(self.temporary.name)
        shutil.copy(PACKAGE / "lib.typ", self.root / "lib.typ")

    def tearDown(self):
        self.temporary.cleanup()

    def compile(self, source):
        return subprocess.run(
            ["typst", "compile", str(source), str(self.root / "consumer.pdf")],
            capture_output=True, text=True,
        )

    def fixture_catalog(self):
        return {"schema": 1, "projects": {"fixture": {
            "title": "Reference Source",
            "pdf_url": "https://example.com/reference-source/main.pdf",
            "ambiguous_labels": ["duplicate"],
            "references": {
                "energy": {"text": "eq.1.2.3", "page": 2},
                "theorem": {"text": "Theorem S.1.1", "page": 1},
                "literal": {"text": '#panic("not code")', "page": 1},
            },
        }}}

    def write_catalog(self, data):
        (self.root / "projects.json").write_text(json.dumps(data), encoding="utf-8")

    def test_consumer_text_and_destinations(self):
        self.write_catalog(self.fixture_catalog())
        source = self.root / "consumer.typ"
        shutil.copy(ROOT / "tests/project-references-consumer.typ", source)
        result = self.compile(source)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_references_fail_clearly(self):
        self.write_catalog(self.fixture_catalog())
        for call, expected in (
            ('project-ref(1, <energy>)', "project must be a string"),
            ('project-ref("fixture", "energy")', "target must be a global label"),
            ('project-ref("missing", <energy>)', "unknown project"),
            ('project-ref("fixture", <missing>)', "unknown global label"),
            ('project-ref("fixture", <duplicate>)', "ambiguous global label"),
            ('project-ref("fixture", <local-scope-1-energy>)', "local-scope labels"),
            ('project-ref("fixture", <mannot-scope-1-energy>)', "local-scope labels"),
            ('project-ref("fixture", <_mannot-marker>)', "local-scope labels"),
        ):
            with self.subTest(call=call):
                source = self.root / "invalid.typ"
                source.write_text('#import "lib.typ": project-ref\n#' + call, encoding="utf-8")
                result = self.compile(source)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_source_rules_local_exclusion_and_refresh(self):
        source = self.root / "main.typ"
        shutil.copy(ROOT / "tests/project-references-source.typ", source)
        references, ambiguous = exporter.collect_references(source, [])
        self.assertEqual(ambiguous, [])
        self.assertEqual(references["energy"]["text"].replace("\u00a0", " "), "eq. (1.1.1)")
        self.assertEqual(references["prelim-equation"]["text"].replace("\u00a0", " "), "eq. (P.1.1)")
        self.assertEqual(references["supplement-equation"]["text"].replace("\u00a0", " "), "eq. (S.1.1)")
        self.assertEqual(references["supplement-theorem"]["text"], "Theorem S.1.1")
        self.assertEqual(references["custom-text"]["text"], "plain reference 1")
        self.assertEqual(references["custom-linked"]["text"], "custom eq. 3")
        self.assertIn("global-figure", references)
        self.assertNotIn("unnumbered", references)
        self.assertNotIn("explicit-energy", references)
        self.assertFalse(any(name.startswith(("local-scope-", "custom-local-")) for name in references))
        self.assertGreaterEqual(references["energy"]["page"], 2)

        catalog = self.root / "projects.json"
        exporter.update_catalog(catalog, "fixture", {
            "title": "Reference Source",
            "pdf_url": "https://example.com/reference-source/main.pdf",
            "references": references,
        })
        consumer = self.root / "real-consumer.typ"
        consumer.write_text(
            '#import "lib.typ": project-ref\n'
            '#let result = project-ref("fixture", <energy>)\n'
            '#assert.eq(result.body, text(' + json.dumps(references["energy"]["text"] + " of Reference Source", ensure_ascii=False) + '))\n'
            '#assert.eq(result.dest, "https://example.com/reference-source/main.pdf#page=' + str(references["energy"]["page"]) + '")\n'
            '#result\n', encoding="utf-8",
        )
        result = self.compile(consumer)
        self.assertEqual(result.returncode, 0, result.stderr)

        source.write_text(source.read_text(encoding="utf-8").replace(
            "$ E = m c^2 $ <energy>", "$ k = 0 $\n  $ E = m c^2 $ <energy>",
        ), encoding="utf-8")
        updated, ambiguous = exporter.collect_references(source, [])
        self.assertEqual(ambiguous, [])
        self.assertEqual(updated["energy"]["text"].replace("\u00a0", " "), "eq. (1.1.2)")
        exporter.update_catalog(catalog, "fixture", {
            "title": "Reference Source",
            "pdf_url": "https://example.com/reference-source/main.pdf",
            "references": updated,
        })
        self.assertEqual(json.loads(catalog.read_text())["projects"]["fixture"]["references"]["energy"], updated["energy"])
        self.assertFalse(list(self.root.glob(".project-references-*.typ")))

    def test_duplicate_labels_are_excluded(self):
        source = self.root / "duplicates.typ"
        source.write_text(
            '#set math.equation(numbering: "(1)")\n'
            '$ x = 1 $ <unique>\n'
            '$ y = 2 $ <duplicate>\n'
            '$ z = 3 $ <duplicate>\n', encoding="utf-8",
        )
        references, ambiguous = exporter.collect_references(source, [])
        self.assertEqual(set(references), {"unique"})
        self.assertEqual(ambiguous, ["duplicate"])

        source.write_text(
            '#set math.equation(numbering: "(1)")\n'
            '$ x = 1 $ <unique>\n'
            '$ y = 2 $ <duplicate>\n'
            '#text("Also labelled") <duplicate>\n', encoding="utf-8",
        )
        references, ambiguous = exporter.collect_references(source, [])
        self.assertEqual(set(references), {"unique"})
        self.assertEqual(ambiguous, ["duplicate"])

    def test_catalog_updates_preserve_other_projects(self):
        catalog = self.root / "projects.json"
        exporter.update_catalog(catalog, "first", {"title": "First"})
        exporter.update_catalog(catalog, "second", {"title": "Second"})
        data = json.loads(catalog.read_text())
        self.assertEqual(set(data["projects"]), {"first", "second"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
