#!/bin/bash
# Double-click entry point for macOS. No terminal flags or environment variables.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/bin/macos/start.command" "$@"
