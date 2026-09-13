#!/usr/bin/env bash
# Cross-compile nib-handler for Linux, macOS, and Windows (amd64 + arm64).
# Used by CI. Needs Go 1.22+ on the builder, not on the user's machine.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/handler"
if ! command -v go >/dev/null 2>&1; then
	echo "ERROR: Go 1.22+ is required on the CI builder." >&2
	exit 1
fi
mkdir -p dist
targets='linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64 windows/arm64'
for spec in $targets; do
	goos="${spec%/*}"
	goarch="${spec#*/}"
	ext=""
	if [ "$goos" = windows ]; then
		ext=".exe"
	fi
	out="dist/nib-handler-${goos}-${goarch}${ext}"
	echo "building $out"
	CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" go build -o "$out" .
done
ls -l dist
