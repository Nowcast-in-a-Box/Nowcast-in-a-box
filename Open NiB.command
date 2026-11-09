#!/bin/bash
# Double-click entry point for macOS: ensure the Handler is running, then open
# the local console page in the default browser. No terminal flags needed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG=/tmp/nib-open-console.log

# The console address only appears once the Handler is ready.
console_url() {
	"$ROOT/bin/macos/start.command" status 2>/dev/null | grep -m1 '^console ' | sed 's/^console //' || true
}

url="$(console_url || true)"
if [[ -z "$url" ]]; then
	echo "Starting the NiB Handler…"
	# Detached on purpose: when launchd is unavailable, the launcher keeps the
	# Handler in the foreground, which must not block this window.
	nohup "$ROOT/bin/macos/start.command" start >"$LOG" 2>&1 &
	echo "Waiting for the console…"
	for _ in $(seq 1 40); do
		url="$(console_url || true)"
		[[ -n "$url" ]] && break
		sleep 1
	done
fi

if [[ -z "$url" ]]; then
	echo "ERROR: the Handler did not come up, so there is no console page to open." >&2
	echo "  Common causes: Docker Desktop (or OrbStack) is not running, a data" >&2
	echo "  directory from config.yaml is missing, or another Handler build owns" >&2
	echo "  this port. Starting a run needs Docker; opening this page does not." >&2
	echo "  Handler output:" >&2
	sed 's/^/  /' "$LOG" >&2 || true
	echo "  Status:" >&2
	"$ROOT/bin/macos/start.command" status 2>&1 | sed 's/^/  /' >&2 || true
	exit 1
fi
echo "Opening $url"
exec open "$url"
