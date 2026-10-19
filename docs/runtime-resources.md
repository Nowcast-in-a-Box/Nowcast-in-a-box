# Runtime resources

The Handler supports every data source and model whose runtime manifest is
packaged under `paths.resources`. `config.yaml` no longer selects a dataset,
data file, catalog file, or container image. The Hub's Catalog selection decides
which packaged data product to execute, and each data/model directory carries
the container definition that runs it.

## Layout

In a development checkout the repository root is the resources root
(`paths.resources: .`). A release bundle can put the same tree under
`resources/`; `scripts/package-resources.sh` copies the source directories and
manifests needed to build the containers from that root.

```text
<resources-root>/
  adapters/<data-id>/
    runtime.yaml                 data reader definition
    Dockerfile                   reader image
    <model-id>/
      runtime.yaml               model definition
      Dockerfile                 model image
  diagnostics/
    runtime.yaml                 diagnostics image and mounts
    Dockerfile
  containers/ ingestion/ interfaces/ utils/   source used by Dockerfiles
```

A data directory without `runtime.yaml` is ignored. The Handler never parses
Dockerfile instructions; it reads the image name and optional build hint from
`runtime.yaml`. If a resources root has no `adapters/` but contains
`resources/adapters/` (a bundle with `config.yaml` at its top level), the
Handler uses the nested `resources/` tree automatically.

## `runtime.yaml` schema

Every file uses `schema: nib.resource.v1`. Unknown keys are rejected.

### Data

```yaml
schema: nib.resource.v1
kind: data
id: cma_radar                 # must match the directory name
product_id: cma-radar         # product id in the Hub package
name: CMA Radar
data:
  path: "."                   # relative to the selected data folder
  kind: directory             # file or directory
  # catalog: source/CATALOG.csv   # file data only
verification:
  level_min: 0
  level_max: 75
  unit: dBZ
visualization_basemap: false
container:
  image: nib-ingestion:cma-demo
  build:
    context: "."
    dockerfile: adapters/cma_radar/Dockerfile
  entrypoint: ["--dataset", "cma_radar", "--data", "/inputs/data"]
  mounts:
    - name: data
      target: /inputs/data
```

`data.path` and `data.catalog` must stay inside the selected data folder.
`file` data requires a regular file; `directory` data requires a directory.
Verification thresholds are validated against `level_min..level_max`.

### Model

```yaml
schema: nib.resource.v1
kind: model
id: nowcastnet                # must match the directory name
product_ids: [nowcastnet-cma] # one or more Hub product ids
name: NowcastNet · CMA demo
weights:
  file: NowcastNet.onnx       # one file name, under artifacts/<data>/<model>/
  sha256: "…"
container:
  image: nib-nowcastnet:cma-demo
  build:
    context: "."
    dockerfile: adapters/cma_radar/nowcastnet/Dockerfile
  command: ["--run", "/run", "--weights", "/weights/model.onnx"]
  mounts:
    - name: run
      target: /run
      write: true
    - name: inputs
      target: /run/inputs
    - name: weights
      target: /weights/model.onnx
```

A model must mount `run`, `inputs`, and `weights`. Product ids must be unique
within their data source.

### Diagnostics

```yaml
schema: nib.resource.v1
kind: diagnostics
container:
  image: nib-diagnostics:automatic-flow
  build:
    context: "."
    dockerfile: diagnostics/Dockerfile
  command: ["--config", "{{request}}"]
  mounts:
    - name: run
      target: /run
      write: true
    - name: inputs
      target: /run/inputs
    - name: forecasts
      target: /run/forecasts
    - name: basemap
      target: /basemap
```

`{{request}}` is replaced with the absolute path inside the mounted run
directory. The `basemap` mount is omitted for data sources with
`visualization_basemap: false`.

## Allowed mounts

| Name | Host source |
| --- | --- |
| `data` | selected data file/directory |
| `catalog` | selected catalog file |
| `run` | `runs/<run-id>` |
| `inputs` | `runs/<run-id>/inputs` |
| `forecasts` | `runs/<run-id>/forecasts` |
| `weights` | `artifacts/<data-id>/<model-id>/<file>` |
| `basemap` | `artifacts/basemap/wmo` |

Mount targets must be absolute container paths. Writes are read-only unless
`write: true`. The Handler appends no implicit mounts except the job workspace
used by ingestion (`-v runs/<id>:/workspace`).

## Data folders and settings

The Hub chooses a folder per data source. The Handler stores it in
`nib-settings.json` next to `config.yaml`:

```json
{"data_roots": {"sevir": "/data/SEVIR", "cma_radar": "/data/cma-radar"}}
```

`PATCH /v1/settings` takes `{"data_id": "...", "data_root": "..."}`.
`GET /v1/settings` returns the effective map. A legacy `data_root` value and
`NIB_DATA_ROOT` remain fallbacks; an explicit per-data entry wins.

## HTTP surface

- `GET /v1/capabilities` returns `data_sources[]` (id, product id, name, saved
  root, readiness), models keyed by `data_id`, and diagnostics readiness.
- `GET /v1/resources?data_id=<id>` reports the diagnostics image and, when a
  data id is known, the selected data/model images and weights.
- `POST /v1/resources/prepare` takes
  `{"data_id": "<id>", "models": ["<runtime-model-id>"]}`, copies missing
  weights from `paths.weight_source`, verifies SHA-256, and re-checks images.
- `POST /v1/preflight` takes `data_id` or `data_product_id` plus the sample
  window and model runtime ids.
- `GET /v1/datasets/<data-id>/samples` uses the data manifest's reader
  container.

Image layers are considered cached. The Handler reports missing images and
never pulls or builds automatically.

## Adding a data source or model

1. Add `adapters/<data-id>/runtime.yaml` and its Dockerfile.
2. Add each model's `runtime.yaml` and Dockerfile under that data directory,
   with unique Hub `product_ids`.
3. Build the image tags named in the manifests from the resources root.
4. Restart the Handler. The new data source appears in `capabilities.data_sources`
   and can be selected from the Hub; no `config.yaml` edit is required.
