#!/usr/bin/env bash
# Developer foreground loop: go run rebuilds the Handler from source.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "usage: $0 [config.yaml]"
  echo "Stop any installed Handler first, then run this in the foreground."
  exit 0
fi

exec go run ./handler/cmd/nib-handler serve -config "${1:-$root/config.yaml}"
