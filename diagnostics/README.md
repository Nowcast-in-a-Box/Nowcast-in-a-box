# Diagnostics handoff

`visualization/` contains the delivered rendering library and offline HTML
gallery. It produces maps, GIF animations and manifests from a validated
`VisualizationField`. Its [README](visualization/README.md) and
[input/output contract](visualization/docs/visualization_bt_handoff_api.md)
describe the current APIs and dependency setup.

`verification/` contains the metrics, SQLite store, query/chart APIs and standalone
review service delivered in PR #12. Its [README](verification/README.md) describes
that module and the real field-provider contract it still needs from the platform.

The common computation entry point and real Forecast reader are not implemented
in this handoff. The Dockerfile is a scaffold: building it does not establish that
`python -m diagnostics` can run. Installing both diagnostic modules in the image,
providing real stored fields and implementing the common entry point are part of
the subsequent runtime integration.

## Existing handler CLI

The CLI prepares an inspectable directory under `paths.runs` (default `runs/`),
validates the request, mounts inputs read-only and invokes the configured image
with no network. It needs an image implementing the protocol below; the provided
scaffold does not yet provide that implementation.

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
that protocol only. The subsequent integration will make metrics/visualization
optional and validate selected real artifacts. There are no HTTP job endpoints
or result web server in this handoff.

## Verification

Handler contributors run `go test ./...` and `go vet ./...` at the repository
root. Command-runner tests cover input validation, image selection, read-only
mounts, offline execution arguments, timeout cleanup and retained failed runs.
The visualization module's README lists its independent pytest and lint checks;
real-cache tests need the documented basemap paths. Missing caches mean those
checks have not been established.

Do not commit inputs, weights, basemap caches or generated run outputs.
