#!/usr/bin/env bash
# Entry point for Linux. Run it from a file manager or a terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/bin/linux/run.sh" "$@"
