# Diagnostics handoff

`visualization/` contains the delivered rendering library and offline HTML
gallery. It produces maps, GIF animations and manifests from a validated
`VisualizationField`. Its [README](visualization/README.md) and
[input/output contract](visualization/docs/visualization_bt_handoff_api.md)
describe the current APIs and dependency setup.

`verification/` contains the metrics, SQLite store, query/chart APIs and standalone
review service delivered in PR #12. Its [README](verification/README.md) describes
that module and the real field-provider contract it still needs from the platform.

The integrated `python -m diagnostics --config request.json` entrypoint consumes
version 2 stored-run requests and installs both diagnostic modules in the image.
Its reader validates NPZ+manifest fields, and calls verification or visualization
only when selected. See [runtime integration](../docs/runtime-integration.md) for
resource preparation, scientific semantics and completed validation limits.

Build from the repository root with
`docker build -f diagnostics/Dockerfile -t nib-diagnostics:dev .`. A successful
build does not establish real model or basemap acceptance.

## Existing handler CLI

The legacy CLI below prepares an inspectable directory under `paths.runs`,
validates its old request, mounts inputs read-only and invokes a configured image
with no network. Its v1 protocol is not the stored-run v2 protocol used by the
new HTTP jobs. Use a matching legacy image for this CLI; the integrated image
refuses a v1 request rather than silently inventing fields.

```sh
bin/macos/start.command diagnostics --request /path/to/request.yaml
# Linux: bin/linux/run.sh diagnostics --request /path/to/request.yaml
# Windows: bin/windows/run.ps1 diagnostics --request C:\path\to\request.yaml
```

Host settings come from `config.yaml`; `-config` overrides the default.
The image comes from `diagnostics.image`, or the CLI `-image` override.
Request YAML cannot supply Docker arguments or override the image.
Every invocation gets a timestamp plus random run ID; `-id` may supply an
explicit ID, and an existing run is never overwritten. Failed runs remain.

The image is called as:

```text
python -m diagnostics --config /run/request.yaml
```

The temporary request format is illustrated by
[`diagnostics-request.example.yaml`](../contracts/examples/diagnostics-request.example.yaml):

```yaml
observation: observation/future.zarr
forecasts:
  - id: wadepre
    path: models/wadepre/forecast.zarr
metrics:
  - family: continuous
    items: [mae, rmse]
  - family: yes_or_no
    items: [csi]
    levels: [35]
  - family: spatial
    items: [fss]
    levels: [35]
    half_windows: [2, 4]
```

Paths are relative to the request file or absolute; inputs must exist.
The handler rewrites them to `/run/inputs/...` and records the source request,
rewritten request, container log and status under `runs/<run-id>/`.
The separate `evaluation.example.yaml` is an architecture example, not this CLI's
request format; grid and scientific metadata there are illustrative declarations.

This legacy CLI requires a non-null JSON `products/metrics.json` and a
`products/frames/` directory before reporting success. Runner tests establish
that legacy protocol only. New HTTP jobs make metrics/visualization optional and
validate their selected stored-run outputs. The Go handler serves registered
result webpages; the standalone verification service remains a module tool.

## Verification

Handler contributors run `go test ./...` and `go vet ./...` at the repository
root. Command-runner tests cover input validation, image selection, read-only
mounts, offline execution arguments, timeout cleanup and retained failed runs.
The visualization module's README lists its independent pytest and lint checks;
real-cache tests need the documented basemap paths. Missing caches mean those
checks have not been established.

Do not commit inputs, weights, basemap caches or generated run outputs.
