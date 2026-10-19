#!/usr/bin/env bash
# User entry point for Linux. Run from a file manager or a terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/bin/linux/uninstall.sh" "$@"
