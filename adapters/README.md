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

Contracts live outside this tree: `contracts/data/<id>.json` and
`contracts/models/<data-id>/<model-id>.json`. The Python contracts are
`interfaces/`. Path resolution and the Hugging Face weight fetch are
`utils/`.

## Boundary

The reader finds and decodes local files. It does not download. Satellite
readers record `region.bbox` and do not crop. The CMA radar reader can
crop. Each model adapter turns a bundle into its own tensor and runs
inference. There is no shared ONNX base class.

SEVIR VIL reads `sevir_vil.h5` from `data_dir`, restores raw VIL
`[0, 255]`, and returns six context frames. NowcastNet, exPreCast, and
WADEPre each keep the latest five frames in their own `adapter.py`.

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

Request YAML is under `tests/adapters/requests/`. Observation files and
weight files stay outside git. Set `NIB_DATA_ROOT` when a test should
read files. Tests that need files or ONNX Runtime skip when those are
absent.

## External scan

TODO(ci): not implemented here. A later job outside this repo should
scan `contracts/data/*.json` and `contracts/models/<data-id>/*.json`
and compare them with `adapters/catalog.json`. It must not import
adapter Python and must not install satpy.

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
