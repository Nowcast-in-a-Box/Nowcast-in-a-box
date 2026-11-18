#!/usr/bin/env bash
# Build from the repository root: bash scripts/build-handler.sh <output> [release-tag]
set -euo pipefail
version="${2:-}"
if [[ -z "$version" ]]; then
  ref="$(git symbolic-ref --quiet HEAD 2>/dev/null || true)"
  version="dev-${ref#refs/heads/}"
  [[ -n "$ref" ]] || version=dev-unknown
fi
exec go build -ldflags "-X github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler.Version=$version" -o "${1:?missing output path}" ./handler/cmd/nib-handler
