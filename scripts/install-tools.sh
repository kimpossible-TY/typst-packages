#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bin_dir="${PDF_VERSIONING_BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$bin_dir"
ln -sf "$repo_root/tools/pdf-versioning/pdf_version_server.py" "$bin_dir/pdf-versioning"
ln -sf "$repo_root/tools/project-references/export_references.py" "$bin_dir/project-references"

printf 'Installed pdf-versioning CLI to %s/pdf-versioning\n' "$bin_dir"
printf 'Installed project-references CLI to %s/project-references\n' "$bin_dir"
printf 'Make sure %s is on your PATH.\n' "$bin_dir"
