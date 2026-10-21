#!/usr/bin/env bash
# Build one user-facing Release zip per requested OS from an explicit allowlist.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "usage: scripts/package-release.sh <version> [macos|linux|windows ...]" >&2
  exit 2
fi
shift
case "$VERSION" in
  v*) ;;
  *) VERSION="v$VERSION" ;;
esac
if [[ ! "$VERSION" =~ ^v[A-Za-z0-9._-]+$ ]]; then
  echo "ERROR: invalid version: $VERSION" >&2
  exit 2
fi

oses=("$@")
if (( ${#oses[@]} == 0 )); then
  oses=(macos linux windows)
fi
for os in "${oses[@]}"; do
  case "$os" in
    macos|linux|windows) ;;
    *) echo "ERROR: unsupported OS: $os" >&2; exit 2 ;;
  esac
done

"$ROOT/scripts/cross-build.sh"
"$ROOT/scripts/package-resources.sh"

mkdir -p "$ROOT/dist"
stage=""
cleanup() { [[ -n "$stage" ]] && rm -rf "$stage"; }
trap cleanup EXIT

for os in "${oses[@]}"; do
  stage="$(mktemp -d "${TMPDIR:-/tmp}/nib-release-${os}.XXXXXX")"
  mkdir -p "$stage/bin/$os" "$stage/artifacts" "$stage/runs"
  cp "$ROOT/README.md" "$ROOT/config.yaml" "$stage/"
  case "$os" in
    macos)
      cp "$ROOT/Start NiB.command" "$ROOT/Uninstall NiB.command" "$stage/"
      ;;
    linux)
      cp "$ROOT/Start NiB.sh" "$ROOT/Uninstall NiB.sh" "$stage/"
      ;;
    windows)
      cp "$ROOT/Start NiB.cmd" "$ROOT/Uninstall NiB.cmd" "$stage/"
      ;;
  esac
  cp -R "$ROOT/bin/$os/." "$stage/bin/$os/"
  cp -R "$ROOT/resources/." "$stage/resources/"
  cp "$ROOT/artifacts/.gitkeep" "$stage/artifacts/.gitkeep"
  cp "$ROOT/runs/.gitkeep" "$stage/runs/.gitkeep"

  find "$stage" -name '.DS_Store' -delete
  find "$stage" -type d \( -name '__pycache__' -o -name '.pytest_cache' \) -prune -exec rm -rf {} +
  find "$stage" -name '*.pyc' -delete

  archive="$ROOT/dist/Nowcast-in-a-Box-${VERSION}-${os}.zip"
  rm -f "$archive"
  (cd "$stage" && zip -qr "$archive" .)
  printf 'created %s\n' "$archive"
  rm -rf "$stage"
  stage=""
done
