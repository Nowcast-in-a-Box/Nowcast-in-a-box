#!/usr/bin/env bash
# Build the user-facing Release zip from an explicit allowlist.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "usage: scripts/package-release.sh <version>" >&2
  exit 2
fi
case "$VERSION" in
  v*) ;;
  *) VERSION="v$VERSION" ;;
esac
if [[ ! "$VERSION" =~ ^v[A-Za-z0-9._-]+$ ]]; then
  echo "ERROR: invalid version: $VERSION" >&2
  exit 2
fi

"$ROOT/scripts/cross-build.sh"
"$ROOT/scripts/package-resources.sh"

stage="$(mktemp -d "${TMPDIR:-/tmp}/nib-release.XXXXXX")"
cleanup() { rm -rf "$stage"; }
trap cleanup EXIT

mkdir -p "$stage/bin" "$stage/artifacts" "$stage/runs"
cp "$ROOT/README.md" "$ROOT/config.yaml" "$stage/"
cp "$ROOT/Start NiB.command" "$ROOT/Start NiB.cmd" "$ROOT/Start NiB.sh" "$stage/"
cp "$ROOT/Uninstall NiB.command" "$ROOT/Uninstall NiB.cmd" "$ROOT/Uninstall NiB.sh" "$stage/"

for os in macos linux windows; do
  mkdir -p "$stage/bin/$os"
  cp "$ROOT/bin/$os/"* "$stage/bin/$os/"
done
cp -R "$ROOT/resources/." "$stage/resources/"
cp "$ROOT/artifacts/.gitkeep" "$stage/artifacts/.gitkeep"
cp "$ROOT/runs/.gitkeep" "$stage/runs/.gitkeep"

# Generated caches and editor metadata are not part of the user runtime.
find "$stage" -name '.DS_Store' -delete
find "$stage" -type d \( -name '__pycache__' -o -name '.pytest_cache' \) -prune -exec rm -rf {} +
find "$stage" -name '*.pyc' -delete

mkdir -p "$ROOT/dist"
archive="$ROOT/dist/Nowcast-in-a-Box-${VERSION}.zip"
rm -f "$archive"
(cd "$stage" && zip -qr "$archive" .)
printf 'created %s\n' "$archive"
unzip -l "$archive" | tail -n 20
