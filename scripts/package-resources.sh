#!/bin/sh
# Build a self-contained release resources/ tree. Dockerfiles use paths
# relative to this root, so the source they COPY must ship beside the
# runtime.yaml manifests. Run from the repository root.
set -eu

root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$root"
rm -rf resources
mkdir -p resources
cp -R adapters resources/adapters
cp -R diagnostics resources/diagnostics
cp -R containers resources/containers
cp -R ingestion resources/ingestion
cp -R interfaces resources/interfaces
cp -R utils resources/utils
find resources -type d -name __pycache__ -prune -exec rm -rf {} +

echo "resources/ ready at $root/resources"
