#!/usr/bin/env python3
"""Export a source document's rendered global references into the shared catalog."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = REPO_ROOT / "local/project-references/0.1.0/projects.json"
PROBE = Path(__file__).with_name("probe.typ")

# Evaluate target positions in the original document as well as in the probe.
# The export must never silently use positions from an altered layout.
TARGETS = r'''{
  let targets = query(selector(heading).or(figure).or(math.equation).or(footnote))
    .filter(it => it.has("label") and it.numbering != none)
    .map(it => {
      let pos = it.location().position()
      (label: str(it.label), matches: query(it.label).len(),
        page: pos.page, x: pos.x / 1pt, y: pos.y / 1pt)
    })
  let local-prefixes = query(metadata).map(it => it.value)
    .filter(it => type(it) == dictionary
      and it.at("kind", default: "") == "project-reference-local-scope")
    .map(it => it.prefix)
  (targets: targets, local_prefixes: local-prefixes)
}'''
CAPTURES = r'''query(metadata)
  .map(it => it.value)
  .filter(it => type(it) == dictionary and
    it.at("kind", default: "") in
    ("project-reference-target", "project-reference-text"))'''


def run_typst(arguments: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["typst", *arguments], cwd=cwd, capture_output=True, text=True,
    )
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        raise RuntimeError(f"Typst failed with exit code {result.returncode}")
    if "did not converge" in result.stderr or "did not stabilize" in result.stderr:
        raise RuntimeError("Typst layout did not converge; the catalog was not updated")
    return result.stdout


def collect_references(source: Path, options: list[str]) -> tuple[dict[str, dict[str, object]], list[str]]:
    original_source = source.read_text(encoding="utf-8")
    original = json.loads(run_typst(
        ["eval", "--in", str(source), *options, TARGETS], cwd=source.parent,
    ))
    local_prefixes = original["local_prefixes"] + [
        "local-scope-", "local-scope-annotations-", "mannot-scope-", "_mannot-",
    ]
    original_targets = [target for target in original["targets"]
                        if not target["label"].startswith(tuple(local_prefixes))]
    ambiguous = sorted({target["label"] for target in original_targets
                        if target["matches"] != 1})
    baseline = {}
    for target in original_targets:
        name = target["label"]
        if name not in ambiguous:
            baseline[name] = {key: target[key] for key in ("label", "page", "x", "y")}

    # The probe must not query all metadata/footnotes while generating more of
    # those elements. Inject a fixed target list from the original compilation.
    target_json = json.dumps(list(baseline), ensure_ascii=False)
    probe = PROBE.read_text(encoding="utf-8").replace(
        "__PROJECT_REFERENCE_TARGETS__",
        "json(bytes(" + json.dumps(target_json, ensure_ascii=False) + "))",
    )

    # A sibling copy preserves relative imports and puts probes inside the
    # source's own top-level show/set rules. The original source is untouched.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=source.parent,
        prefix=".project-references-", suffix=".typ", delete=False,
    ) as handle:
        probe_source = Path(handle.name)
        handle.write(original_source)
        handle.write("\n\n")
        handle.write(probe)
    try:
        captures = json.loads(run_typst(
            ["eval", "--in", str(probe_source), *options, CAPTURES], cwd=source.parent,
        ))
    finally:
        probe_source.unlink(missing_ok=True)

    if source.read_text(encoding="utf-8") != original_source:
        raise ValueError("The source changed during export; run the export again")

    references: dict[str, dict[str, object]] = {}
    chunks: dict[str, list[str]] = {}
    for capture in captures:
        name = capture["label"]
        if capture["kind"] == "project-reference-text":
            chunks.setdefault(name, []).append(capture["text"])
        else:
            position = {key: capture[key] for key in ("label", "page", "x", "y")}
            if position != baseline.get(name):
                raise ValueError(f"Export probe changed the layout at <{name}>")
            if name in references:
                raise ValueError(f"Duplicate exported reference <{name}>")
            references[name] = {key: capture[key] for key in ("page", "x", "y")}

    if set(references) != set(baseline):
        raise ValueError("The export probe did not resolve every global target")

    for name, entry in references.items():
        text = "".join(chunks.get(name, [])).strip()
        if not text:
            raise ValueError(f"Reference <{name}> has no exportable text")
        entry["text"] = text
    if not references:
        raise ValueError(f"No numbered global references found in {source}")
    return dict(sorted(references.items())), ambiguous


def update_catalog(catalog: Path, project: str, entry: dict[str, object]) -> None:
    data = json.loads(catalog.read_text(encoding="utf-8")) if catalog.exists() else {
        "schema": 1, "projects": {},
    }
    if data.get("schema") != 1 or not isinstance(data.get("projects"), dict):
        raise ValueError(f"Unsupported project reference catalog: {catalog}")
    data["projects"][project] = entry
    catalog.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=catalog.parent, delete=False,
    ) as handle:
        temporary_catalog = Path(handle.name)
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        os.replace(temporary_catalog, catalog)
    finally:
        temporary_catalog.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Source project's entrypoint, e.g. main.typ")
    parser.add_argument("--project", required=True, help="Short project key used by project-ref")
    parser.add_argument("--title", required=True, help="Project name appended after 'of'")
    parser.add_argument("--pdf-url", required=True, help="Published source PDF URL")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--font-path", action="append", default=[])
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--package-path", type=Path)
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9_-]*", args.project):
        parser.error("--project must be a lowercase key such as pde or smooth-manifolds")
    if not args.title.strip():
        parser.error("--title cannot be empty")
    url = urlsplit(args.pdf_url)
    if url.scheme not in ("http", "https") or not url.netloc or url.fragment:
        parser.error("--pdf-url must be an HTTP(S) URL without a fragment")
    try:
        source = args.source.resolve(strict=True)
    except OSError as error:
        parser.error(str(error))
    options: list[str] = []
    for font_path in args.font_path:
        options.extend(("--font-path", str(Path(font_path).resolve())))
    for build_input in args.input:
        options.extend(("--input", build_input))
    for option, value in (("--root", args.root), ("--package-path", args.package_path)):
        if value is not None:
            options.extend((option, str(value.resolve())))

    try:
        references, ambiguous = collect_references(source, options)
        for name in ambiguous:
            print(f"project-references: excluding ambiguous label <{name}> (multiple targets)", file=sys.stderr)
        update_catalog(args.catalog.resolve(), args.project, {
            "title": args.title,
            "pdf_url": args.pdf_url,
            "references": references,
            "ambiguous_labels": ambiguous,
        })
    except (OSError, RuntimeError, ValueError) as error:
        print(f"project-references: {error}", file=sys.stderr)
        return 1
    print(f"Exported {len(references)} global references for {args.project} to {args.catalog.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
