# Adapters

Packaging-time source for one data reader and the models paired with it.
The host handler does not import this tree. A model image installs that
reader's `requirements.txt` plus the paired model's `requirements.txt`.

```text
adapters/catalog.json            web index
adapters/<data-id>/source.py     one data source
adapters/<data-id>/<model-id>/   one model. Its program is adapter.py.
artifacts/                       downloaded weight files, not committed
```

Each data directory also carries the runtime definition the handler reads:
`runtime.yaml` (container image, mounts, product id) and a `Dockerfile` for that
reader. Each model directory has the same pair. See
[runtime resources](../docs/reference/runtime-resources.md); `config.yaml` does not name a
dataset or image. Build images from the repository root, for example:

```sh
docker build -f adapters/sevir/Dockerfile -t nib-ingestion:dev .
docker build -f adapters/sevir/nowcastnet/Dockerfile -t nib-nowcastnet:dev .
```

Contracts live outside this tree: `contracts/data/<id>.json` and
`contracts/models/<data-id>/<model-id>.json`. The Python contracts are
`interfaces/`. Path resolution and the Hugging Face weight fetch are
`utils/`.

## Boundary

The reader finds and decodes local files. It does not download. Satellite
readers record `region.bbox` and do not crop. The CMA radar reader can
crop. Model adapters consume physical `Observations`, validate them, prepare
backend-private inputs, and return physical `Forecast` fields. Backend choice
stays inside Docker. See the [model contract](../docs/reference/models.md).

SEVIR VIL reads `sevir_vil.h5` from `data_dir`, restores raw VIL
`[0, 255]`. Runtime selects the exact recent history declared by the selected
model before validation. Adapters must not silently trim, pad, resize or
interpolate incompatible input.

Weight pins are Hugging Face resolve URLs. `utils.weights.ensure_weights`
checks the file under `artifacts/` first. A matching sha256 skips the
download. That directory is the right place: it is already the handler's
download cache, the binaries are gitignored, and a later run can fetch
into the same path if the file is missing.

Install one source at a time:

```sh
pip install -r adapters/gk2a/requirements.txt
pip install -r adapters/sevir/requirements.txt
pip install -r adapters/sevir/nowcastnet/requirements.txt
```

Run a request. The YAML lives with the tests, not in this tree:

```sh
python -m adapters --config tests/adapters/requests/cma_radar/example_success.yaml --data-root "$NIB_DATA_ROOT"
```

`data_dir` is relative to the data root unless it is already absolute on
this operating system. A Windows drive path is rejected on macOS and
Linux. The handler never invents a data root.

## Tests

Request YAML is under `tests/adapters/requests/`. Observation files and weight
files stay outside git. `tests/adapters/data/` holds the reader checks,
`tests/adapters/model/` the forecast checks.

Routine CI uses small synthetic function tests without real model inference.
Real inference belongs to a named release candidate and platform, with missing
required inputs reported as FAIL and execution offline. There are no accuracy
gates. The commands below are optional local reader/sample checks, not evidence
of release acceptance.

Fetch the available demo samples for those local checks, then run the suite:

```sh
scripts/fetch-demo-data.py
python -m pytest tests/adapters
```

The samples repository is private: the script reads `NIB_DEMO_DATA_TOKEN`, or the
`gh` CLI token when that is unset. That fills `testdata/demo-data/` (gitignored)
and the adapter tests pick it up through `NIB_DATA_ROOT`.
Tests that need files or ONNX Runtime skip when those are absent, and a source
without a sample skips by name.

The downloadable set contains one SEVIR sample. CMA radar samples must stay
local and must not be uploaded to GitHub, including private repositories and
Actions caches. Keep them under a local data root or the gitignored
`testdata/demo-data/cma_radar/`. Refreshing downloads preserves files outside
the manifest; CI caches only `testdata/demo-data/SEVIR/`.

## Model contributions and registration

See [the contribution guide](../docs/guides/model-contribution.md) for required
files, lifecycle and tests. Model declarations use `nib.model.v1`; their query
methods describe physical input/output fields, capabilities and weights.
Atlas registration is manual. A declaration or local catalog update does not
automatically export or publish a Hub product.

## Typed request and persistent output

Adapter YAML is parsed into the shared immutable `DataRequest` dataclass in
`interfaces/data.py` before a source adapter runs. This gives every adapter the
same typed request fields while keeping the interface layer standard-library
only.

The normal in-memory `DataBundle` is always returned. Writing Zarr is optional.
The current prototype supports CMA radar plus FY-4B, GK2A, Himawari-9, and MTG.

Run without Zarr when only the in-memory result is needed:

```sh
python -m adapters \
  --config tests/adapters/requests/cma_radar/example_success.yaml \
  --data-root "$NIB_DATA_ROOT"
```

Write a local Zarr dataset when a persistent handoff is needed:

```sh
python -m adapters \
  --config tests/adapters/requests/cma_radar/example_success.yaml \
  --data-root "$NIB_DATA_ROOT" \
  --output-zarr runs/output.zarr
```

Use `--overwrite-zarr` to replace an existing dataset. The same CLI option is
used for the supported satellite sources.

Alternatively, the optional output can be declared in YAML:

```yaml
output:
  zarr:
    path: runs/output.zarr
    overwrite: true
```

The command-line `--output-zarr` value takes precedence over the YAML output
path.

### CMA radar Zarr prototype

Downstream code can open the radar output directly:

```python
import xarray as xr

ds = xr.open_zarr("runs/cma_radar.zarr")
```

The CMA radar prototype stores the reliable metadata available from the current
CREF reader, including `reflectivity(time, latitude, longitude)`, observation
and generation times, latitude/longitude, bounding boxes, resolution, source
metadata, consolidated Zarr v2 metadata, and ZSTD compression.

The layout is informed by the MLCast radar precipitation specification:
https://mlcast-community.github.io/mlcast-dataset-validator/specs/source_data/radar_precipitation/

It is intentionally **not** declared fully MLCast-compliant. Projected `x/y`, a
verified CRS/grid mapping, GeoZarr metadata, and an official missing-value
interpretation should be added only when their source definitions are verified.

### Satellite Zarr prototype

FY-4B AGRI, Himawari-9 AHI, MTG FCI, and GK2A AMI can now use the same optional
`--output-zarr` handoff.

The satellite writer preserves the current loader output rather than imposing a
new common grid:

- one data variable per requested channel;
- a shared `time` dimension;
- channel-specific spatial dimensions so channels with different native
  resolutions can coexist in one Zarr dataset;
- native `x/y` coordinates and projection metadata when the loader provides
  them (currently FY-4B, Himawari-9, and MTG);
- native pixel dimensions without invented georeferencing when the current
  loader does not provide spatial coordinates (currently GK2A);
- source/channel metadata, requested region, adapter version, and source-file
  names;
- Zarr v2 consolidated metadata, ZSTD compression, and bounded spatial chunks.

Example downstream access:

```python
import xarray as xr

ds = xr.open_zarr("runs/fy4b.zarr")
print(ds.data_vars)
channel = ds["C13"]
```

Different satellite channels are deliberately not regridded or resampled by
the Zarr writer. For example, a channel may be stored as
`C13(time, C13_y, C13_x)` while another channel uses its own native dimensions.
This keeps persistence separate from model-specific preprocessing.

The design also follows the ongoing MLCast discussion for a geostationary
satellite dataset specification:
https://github.com/mlcast-community/mlcast-dataset-validator/issues/40

This first NiB satellite layout is a prototype for the data already produced by
the current loaders; it is not claimed to be a finished MLCast satellite
specification.
