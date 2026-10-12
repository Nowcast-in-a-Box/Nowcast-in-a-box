#!/bin/bash
# Dispatches to the macOS entry under bin/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/bin/macos/start.command" "$@"
