#!/usr/bin/env bash
# Dispatches to the OS entry under bin/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "$(uname -s)" in
	Linux) exec "$ROOT/bin/linux/run.sh" "$@" ;;
	Darwin) exec "$ROOT/bin/macos/start.command" "$@" ;;
	*)
		echo "ERROR: unsupported OS $(uname -s). Windows: bin/windows/run.ps1" >&2
		exit 1
		;;
esac
