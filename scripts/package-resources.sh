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
# Visualization caches are local build data, not a runtime resource.
rm -rf resources/diagnostics/visualization/data
find resources -name '.DS_Store' -delete
find resources -type d \( -name '__pycache__' -o -name '.pytest_cache' \) -prune -exec rm -rf {} +
find resources -name '*.pyc' -delete

echo "resources/ ready at $root/resources"
