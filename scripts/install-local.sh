#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
package_root="${TYPST_PACKAGE_PATH:-$HOME/Library/Application Support/typst/packages}"
target="$package_root/local"

mkdir -p "$target"

for package_dir in "$repo_root"/local/*; do
  [ -d "$package_dir" ] || continue
  package_name=$(basename "$package_dir")
  rm -rf "$target/$package_name"
  cp -R "$package_dir" "$target/"
done

printf 'Installed local Typst packages to %s\n' "$target"
