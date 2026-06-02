#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bin_dir="${PDF_VERSIONING_BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$bin_dir"
ln -sf "$repo_root/tools/pdf-versioning/pdf_version_server.py" "$bin_dir/pdf-versioning"

printf 'Installed pdf-versioning CLI to %s/pdf-versioning\n' "$bin_dir"
printf 'Make sure %s is on your PATH.\n' "$bin_dir"
