#!/bin/bash
# Double-click entry point for macOS. No terminal flags or environment variables.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export NIB_OPEN_CONSOLE=1 # start.command opens the console page once the Handler is up
exec "$ROOT/bin/macos/start.command" "$@"
