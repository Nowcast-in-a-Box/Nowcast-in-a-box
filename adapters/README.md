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
