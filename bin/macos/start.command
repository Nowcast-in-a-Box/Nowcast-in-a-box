#!/bin/bash
# macOS entry point. Double-click from Finder, or run from a terminal.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
REPO="Nowcast-in-a-Box/Nowcast-in-a-box"

uname_s="$(uname -s)"
if [[ "$uname_s" != Darwin ]]; then
	echo "ERROR: this entry is for macOS. Linux: bin/linux/run.sh  Windows: bin/windows/run.ps1" >&2
	exit 1
fi
uname_m="$(uname -m)"
case "$uname_m" in
	x86_64|amd64) goarch=amd64 ;;
	arm64|aarch64) goarch=arm64 ;;
	*)
		echo "ERROR: unsupported architecture $uname_m" >&2
		exit 1
		;;
esac

name="nib-handler-darwin-${goarch}"
bin="$HERE/$name"
if [[ ! -x "$bin" ]]; then
	url="https://github.com/${REPO}/releases/latest/download/${name}"
	echo "fetching $url" >&2
	if ! command -v curl >/dev/null 2>&1; then
		echo "ERROR: curl is required to download the handler binary." >&2
		exit 1
	fi
	if ! curl -fL --retry 3 -o "$bin" "$url"; then
		rm -f "$bin"
		echo "ERROR: no handler binary at $bin" >&2
		echo "Download it from a GitHub Release, or build with scripts/cross-build.sh, and put it there." >&2
		exit 1
	fi
	chmod +x "$bin"
fi

exec "$bin" -config "$ROOT/config.yaml" "$@"
