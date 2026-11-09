#!/usr/bin/env bash
# Open the local console page in a browser. Starts the Handler in the
# background first if it is not running yet.
#
# Note: when this script starts the Handler, closing the launching terminal
# may stop it again (same as Start NiB.sh). Keep it running with Start NiB.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

console_url() {
	"$ROOT/bin/linux/run.sh" status 2>/dev/null | grep -m1 '^console ' | sed 's/^console //' || true
}

url="$(console_url || true)"
if [[ -z "$url" ]]; then
	echo "Starting the Handler in the background…" >&2
	nohup "$ROOT/bin/linux/run.sh" start >/dev/null 2>/tmp/nib-open-console.err &
	for _ in $(seq 1 30); do
		url="$(console_url || true)"
		[[ -n "$url" ]] && break
		sleep 1
	done
fi

if [[ -z "$url" ]]; then
	echo "ERROR: the Handler did not report its console address; expected http://127.0.0.1:8765/ unless config.yaml changes it." >&2
	echo "  Common causes: Docker is not running (start it, then retry), a data" >&2
	echo "  directory from config.yaml is missing, or an older Handler build is" >&2
	echo "  still running. Status output:" >&2
	"$ROOT/bin/linux/run.sh" status 2>&1 | sed 's/^/  /' >&2 || true
	[[ -s /tmp/nib-open-console.err ]] && sed 's/^/  /' /tmp/nib-open-console.err >&2 || true
	exit 1
fi
if ! command -v xdg-open >/dev/null 2>&1; then
	echo "xdg-open is not available; open this address in a browser: $url" >&2
	exit 1
fi
exec xdg-open "$url"
