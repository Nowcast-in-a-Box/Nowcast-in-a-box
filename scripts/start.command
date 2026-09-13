#!/bin/bash
# Double-click from Finder. Same entry as run.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/run.sh" "$@"
