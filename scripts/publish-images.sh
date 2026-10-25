#!/bin/sh
# Publish the images used by the reference local runtime to a registry.
# Usage: scripts/publish-images.sh [registry-prefix]
# Default prefix matches config.yaml: ghcr.io/nowcast-in-a-box
set -eu

root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$root"
registry="${1:-ghcr.io/nowcast-in-a-box}"

docker build -f diagnostics/Dockerfile -t "$registry/nib-diagnostics:automatic-flow-basemap-v1" .
docker build -f adapters/cma_radar/Dockerfile -t "$registry/nib-ingestion:cma-demo" .
docker build --build-context "weights=${NIB_NOWCASTNET_WEIGHTS_DIR:-$root/artifacts/cma_radar/nowcastnet}" -f adapters/cma_radar/nowcastnet/Dockerfile -t "$registry/nib-nowcastnet:cma-demo-embedded" .
docker push "$registry/nib-diagnostics:automatic-flow-basemap-v1"
docker push "$registry/nib-ingestion:cma-demo"
docker push "$registry/nib-nowcastnet:cma-demo-embedded"
