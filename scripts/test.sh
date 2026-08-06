#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

"$repo_root/scripts/install-local.sh"
typst compile "$repo_root/tests/package-smoke.typ" /tmp/custom-typst-package-smoke.pdf
typst compile "$repo_root/tests/math-block-numbering.typ" /tmp/custom-typst-package-math-block-numbering.pdf

tmp_dir=/tmp/custom-typst-package-math-book
rm -rf "$tmp_dir"
typst init @local/math-book:0.1.0 "$tmp_dir"
typst compile "$tmp_dir/main.typ" /tmp/custom-typst-package-math-book.pdf

printf 'Smoke tests passed.\n'
