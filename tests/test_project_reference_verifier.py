"""Reject catalog/publication drift and verify actual consumer PDF links."""

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_references", ROOT / "tools/project-references/verify_references.py",
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class PublishedReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        source = cls.root / "source.typ"
        source.write_text(
            '#set math.equation(numbering: n => [(1.2.3)])\n$ x = 1 $\n',
            encoding="utf-8",
        )
        subprocess.run(
            ["typst", "compile", str(source), str(cls.root / "source.pdf")],
            capture_output=True, text=True, check=True,
        )
        cls.pdf = (cls.root / "source.pdf").read_bytes()
        cls.project = {
            "title": "Source",
            "pdf_url": "https://example.com/source.pdf",
            "published_pdf": {
                "sha256": verifier.digest(cls.pdf), "pages": 1,
                "download_url": "https://example.com/source.pdf",
            },
            "ambiguous_labels": [],
            "references": {"target": {"text": "Equation (1.2.3)", "page": 1}},
        }
        cls.data = {"schema": 1, "projects": {"source": cls.project}}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_missing_ambiguous_and_local_targets(self):
        for required, message in (
            ([("unknown", "target")], "Unknown project"),
            ([("source", "unknown")], "Missing reference"),
            ([("source", "local-scope-1-target")], "Local-scope"),
        ):
            with self.subTest(required=required), self.assertRaisesRegex(ValueError, message):
                verifier.check_catalog(self.data, required)
        data = copy.deepcopy(self.data)
        data["projects"]["source"]["ambiguous_labels"] = ["target"]
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            verifier.check_catalog(data, [("source", "target")])

    def test_rejects_unpublished_catalog_and_out_of_range_pages(self):
        project = copy.deepcopy(self.project)
        project["references"]["target"]["page"] = 2
        with self.assertRaisesRegex(ValueError, "Page outside"):
            verifier.check_published(project, "source", self.pdf, ["target"])
        project = copy.deepcopy(self.project)
        project["references"]["target"]["text"] = "Equation (9.9.9)"
        with self.assertRaisesRegex(ValueError, "text absent"):
            verifier.check_published(project, "source", self.pdf, ["target"])
        verifier.check_published(self.project, "source", self.pdf, ["target"])

    def test_published_hash_mismatch(self):
        cached = self.root / "cache"
        cached.mkdir(exist_ok=True)
        (cached / "source.pdf").write_bytes(self.pdf + b"changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verifier.published_pdf(self.project, "source", cached)

    def test_installed_catalog_hash_mismatch(self):
        catalog = self.root / "catalog.json"
        catalog.write_text(json.dumps(self.data))
        result = subprocess.run(
            [sys.executable, verifier.__file__, "--catalog", str(catalog),
             "--catalog-sha256", "0" * 64, "--source-root", str(self.root),
             "--published-dir", str(self.root / "cache")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Installed catalog hash mismatch", result.stderr)

    def test_consumer_text_and_link_are_both_required(self):
        shutil.copy(ROOT / "local/project-references/0.1.0/lib.typ", self.root / "lib.typ")
        (self.root / "projects.json").write_text(json.dumps(self.data))
        consumer = self.root / "consumer.typ"
        consumer.write_text('#import "lib.typ": project-ref\n#project-ref("source", <target>)')
        pdf = self.root / "consumer.pdf"
        subprocess.run(
            ["typst", "compile", str(consumer), str(pdf)], capture_output=True, check=True,
        )
        verifier.check_consumer(self.data, [("source", "target")], pdf)
        data = copy.deepcopy(self.data)
        data["projects"]["source"]["pdf_url"] = "https://example.com/wrong.pdf"
        with self.assertRaisesRegex(ValueError, "link missing"):
            verifier.check_consumer(data, [("source", "target")], pdf)
        with self.assertRaisesRegex(ValueError, "text missing"):
            verifier.check_consumer(self.data, [("source", "target")], self.root / "source.pdf")


if __name__ == "__main__":
    unittest.main(verbosity=2)
