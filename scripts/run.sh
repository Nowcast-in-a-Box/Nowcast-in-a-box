#!/usr/bin/env bash
# Run the prebuilt nib-handler on Linux or macOS. No local Go install.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="Nowcast-in-a-Box/Nowcast-in-a-box"

uname_s="$(uname -s)"
uname_m="$(uname -m)"
case "$uname_s" in
	Linux) goos=linux ;;
	Darwin) goos=darwin ;;
	*)
		echo "ERROR: unsupported OS $uname_s. Windows: scripts/run.ps1" >&2
		exit 1
		;;
esac
case "$uname_m" in
	x86_64|amd64) goarch=amd64 ;;
	arm64|aarch64) goarch=arm64 ;;
	*)
		echo "ERROR: unsupported architecture $uname_m" >&2
		exit 1
		;;
esac

name="nib-handler-${goos}-${goarch}"
dist="$ROOT/handler/dist"
bin="$dist/$name"
mkdir -p "$dist"

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
		echo "Download it from a GitHub Release, or from the CI artifact named nib-handler, and put it there." >&2
		exit 1
	fi
	chmod +x "$bin"
fi

exec "$bin" -config "$ROOT/config.yaml" "$@"
