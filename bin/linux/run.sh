#!/usr/bin/env bash
# Linux entry point. Fetches or runs the handler for this machine.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
REPO="Nowcast-in-a-Box/Nowcast-in-a-box"

uname_s="$(uname -s)"
if [[ "$uname_s" != Linux ]]; then
	echo "ERROR: this entry is for Linux. macOS: bin/macos/start.command  Windows: bin/windows/run.ps1" >&2
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

name="nib-handler-linux-${goarch}"
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

if [[ "${NIB_SKIP_URI_REGISTRATION:-}" != 1 ]]; then
	desktop="${XDG_DATA_HOME:-$HOME/.local/share}/applications/nib-handler.desktop"
	mkdir -p "$(dirname "$desktop")"
	printf '[Desktop Entry]\nType=Application\nName=NiB handler\nExec="%s" connect -config "%s" %%u\nMimeType=x-scheme-handler/nib;\nNoDisplay=true\n' "$bin" "$ROOT/config.yaml" > "$desktop"
	xdg-mime default nib-handler.desktop x-scheme-handler/nib >/dev/null 2>&1 || true
fi

if [[ "${1:-}" == diagnostics ]]; then
	shift
	exec "$bin" diagnostics -config "$ROOT/config.yaml" "$@"
fi
case "${1:-}" in
	start|serve|status|stop|restart|connect)
		action="$1"; shift
		exec "$bin" "$action" -config "$ROOT/config.yaml" "$@"
		;;
esac
exec "$bin" -config "$ROOT/config.yaml" "$@"
