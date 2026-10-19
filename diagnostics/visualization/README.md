# NiB Visualization MVP

A local Python library for plotting a single gridded forecast sequence over a
basemap. It produces PNG maps, a GIF animation and a JSON output record.

The [input/output contract](docs/visualization_bt_handoff_api.md) describes the
Forecast information needed for integration. A real NiB Forecast reader is still
to be connected. The commands below use synthetic data to exercise the existing
rendering library.

## Install

Run from this module directory with Python 3.12 or newer:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock.txt
.venv/bin/python -m pip install --no-deps .
```

`requirements-dev.lock.txt` records the tested dependency versions, including
test tools. Platform-specific dependency installation must be checked on the
receiving machine. The module's `pyproject.toml` stays inside this directory when
integrated into a larger repository.

## Prepare the basemap once

Data files are not included in this package. WMO source metadata and acquisition
records are in `basemap_sources/`. On a machine with network access and `curl` installed, acquire the
cache with:

```bash
.venv/bin/python scripts/acquire_wmo_basemap.py --output data/cache/basemap/wmo
```

Use the data under the source's stated terms. This package distributes source
records and retrieval code, not the third-party basemap files. Retrieval depends
on the source service being available. Once the cache is prepared, rendering is
offline; it does not download tiles on demand.

An already prepared cache can be supplied through `--basemap` in the next command.
The WMO cache inspected during preparation occupied about 31 MiB. Terrain data are
optional and are not needed for this example.

For terrain and the CMA radar mock, follow the [data preparation guide](docs/data_setup.md).
It gives the official terrain download, checksum check and crop command, then
uses the team's existing mock samples and the bundled REF colour table.

## Run the example

```bash
.venv/bin/python scripts/visualization_handoff_example.py \
    --basemap data/cache/basemap/wmo \
    --output output/example
```

This calls the existing `VisualizationField → prepare_scene → render_frame /
write_animation → write_manifest` APIs. The script is the minimal callable
example; its three leads are demonstration data, not a required frame count.
It produces `map.png`, `animation.gif`, `manifest.json`, and `frames/*.png`.
Reusing the output directory replaces that example's files.

To view the PNG and GIF together in a local HTML page:

```bash
.venv/bin/python -m nib_visualization.cli gallery \
    --manifest output/example/manifest.json
```

Open `output/example/index.html` in a browser. The library uses a headless plotting
backend. Matplotlib/Fontconfig cache directories must be writable by the run user.

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src scripts tests
```

Tests that require real basemap caches skip when their cache environment variables
are unset. Set `NIB_WMO_BASEMAP_PATH` for the WMO cache and `NIB_BASEMAP_PATH` for an
OCHA GeoPackage to include those checks. OCHA integrity checks also require the
matching raw exports at the paths recorded in `basemap_sources/acquisition.json`;
`scripts/acquire_basemap.py` creates them. Skipped checks are not full acceptance.
The small `scripts/gif_palette.py` helper is included because its regression tests
import it.

## Integration boundary

Diagnostics can install this module and call its Python APIs. This package does
not implement a diagnostics entry point, Forecast file reader, metric computation
or SQLite registration. There is no need for a renderer per model.

The included CMA observation adapter and terrain support are separate from the
synthetic example. Running `cma-mock` uses the team's existing radar NPZs and separately prepared
terrain. The REF colour table is included at `resources/cma_ref_colormap.json`;
the data preparation guide passes it through `--color-file`.
Basemap acquisition records describe their original acquisition snapshot.

`SHA256SUMS.txt` lists the files in this source delivery for integrity checking.
