#!/usr/bin/env python3
"""Verify literal project-ref calls against a pinned catalog and published PDFs."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from urllib.request import urlopen


CALL = re.compile(r'project-ref\(\s*"([^"\n]+)"\s*,\s*<([^>\n]+)>\s*\)')
LOCAL_PREFIXES = ("local-scope-", "local-scope-annotations-", "mannot-scope-", "_mannot-")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compact(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"-\s*\n\s*", "", text).replace("\u00ad", ""))


def required_references(root: Path) -> list[tuple[str, str]]:
    required = set()
    for source in root.rglob("*.typ"):
        relative = source.relative_to(root)
        if any(part.startswith(".") or part in ("typst-packages", "node_modules") for part in relative.parts):
            continue
        required.update(CALL.findall(source.read_text(encoding="utf-8")))
    if not required:
        raise ValueError("No literal project-ref calls found")
    return sorted(required)


def check_catalog(data: dict, required: list[tuple[str, str]]) -> None:
    if data.get("schema") != 1 or not isinstance(data.get("projects"), dict):
        raise ValueError("Invalid project catalog")
    for alias, label in required:
        project = data["projects"].get(alias)
        if project is None:
            raise ValueError(f"Unknown project: {alias}")
        if label.startswith(LOCAL_PREFIXES):
            raise ValueError(f"Local-scope reference: {alias}/<{label}>")
        if label in project.get("ambiguous_labels", []):
            raise ValueError(f"Ambiguous reference: {alias}/<{label}>")
        if label not in project.get("references", {}):
            raise ValueError(f"Missing reference: {alias}/<{label}>")
        published = project.get("published_pdf", {})
        if not re.fullmatch(r"[0-9a-f]{64}", published.get("sha256", "")):
            raise ValueError(f"Missing published PDF hash: {alias}")


def published_pdf(project: dict, alias: str, directory: Path) -> bytes:
    published = project["published_pdf"]
    cached = directory / f"{alias}.pdf"
    if cached.exists():
        content = cached.read_bytes()
    else:
        url = published["download_url"]
        if not url.startswith("https://"):
            raise ValueError(f"Published PDF must use HTTPS: {alias}")
        with urlopen(url, timeout=30) as response:
            content = response.read(64 * 1024 * 1024 + 1)
        if len(content) > 64 * 1024 * 1024 or not content.startswith(b"%PDF-"):
            raise ValueError(f"Invalid published PDF response: {alias}")
    if digest(content) != published["sha256"]:
        raise ValueError(f"Published PDF hash mismatch: {alias}")
    directory.mkdir(parents=True, exist_ok=True)
    if not cached.exists():
        cached.write_bytes(content)
    return content


def check_published(project: dict, alias: str, content: bytes, labels: list[str]) -> None:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages = len(reader.pages)
    if pages != project["published_pdf"]["pages"]:
        raise ValueError(f"Published PDF page count mismatch: {alias}")
    for label, entry in project["references"].items():
        if type(entry.get("page")) is not int or not 1 <= entry["page"] <= pages:
            raise ValueError(f"Page outside published PDF: {alias}/<{label}>")
        if not isinstance(entry.get("text"), str) or not entry["text"].strip():
            raise ValueError(f"Empty reference text: {alias}/<{label}>")
        if label.startswith(LOCAL_PREFIXES) or label in project.get("ambiguous_labels", []):
            raise ValueError(f"Non-public target in catalog: {alias}/<{label}>")
    for label in labels:
        entry = project["references"][label]
        page_text = compact(reader.pages[entry["page"] - 1].extract_text())
        expected = compact(entry["text"])
        equation = re.search(r"\([0-9]+(?:\.[0-9]+)+\)$", expected)
        # Equation supplements and heading supplements are not printed beside
        # their source targets. Verify the displayed target number instead.
        if equation:
            expected = equation.group()
        elif re.fullmatch(r"(?:Section|절)[0-9]+(?:\.[0-9]+)*", expected):
            expected = re.search(r"[0-9].*", expected).group() + ":"
        if expected not in page_text:
            raise ValueError(f"Reference text absent from published page: {alias}/<{label}>")
    print(f"Published {alias}: sha256={digest(content)} pages={pages} references={len(project['references'])}")


def check_consumer(data: dict, required: list[tuple[str, str]], pdf: Path) -> None:
    from pypdf import PdfReader

    reader = PdfReader(pdf)
    text = compact("\n".join(page.extract_text() for page in reader.pages))
    destinations = set()
    for page in reader.pages:
        for annotation in page.get("/Annots", []):
            action = annotation.get_object().get("/A", {})
            if action.get("/S") == "/URI":
                destinations.add(str(action.get("/URI")))
    for alias, label in required:
        project = data["projects"][alias]
        entry = project["references"][label]
        expected = entry["text"] + " of " + project["title"]
        if compact(expected) not in text:
            raise ValueError(f"Consumer reference text missing: {alias}/<{label}>")
        url = project["pdf_url"] + "#page=" + str(entry["page"])
        if url not in destinations:
            raise ValueError(f"Consumer PDF link missing: {alias}/<{label}>")
    print(f"Consumer PDF: {len(required)} reference strings and link destinations verified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--published-dir", required=True, type=Path)
    parser.add_argument("--consumer-pdf", type=Path)
    args = parser.parse_args()
    try:
        raw = args.catalog.read_bytes()
        if digest(raw) != args.catalog_sha256:
            raise ValueError("Installed catalog hash mismatch")
        data = json.loads(raw)
        required = required_references(args.source_root)
        check_catalog(data, required)
        for alias in sorted({alias for alias, _ in required}):
            project = data["projects"][alias]
            content = published_pdf(project, alias, args.published_dir)
            check_published(project, alias, content, [label for key, label in required if key == alias])
        print(f"Catalog sha256={digest(raw)}; {len(required)} required global labels verified")
        if args.consumer_pdf:
            check_consumer(data, required, args.consumer_pdf)
    except (OSError, ValueError, KeyError) as error:
        print(f"project-references: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
