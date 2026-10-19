#!/bin/bash
# User entry point for macOS. Double-click from Finder or run from a terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/bin/macos/uninstall.command" "$@"
