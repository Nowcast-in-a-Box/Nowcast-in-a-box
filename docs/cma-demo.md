# CMA Radar / NowcastNet reference demo

This demo uses July 2026 national CMA CREF mosaics and the existing local
SEVIR-pretrained NowcastNet ONNX weight. It exercises the complete local chain:
Hub selection and configuration → frozen package → handler → ingestion → ONNX →
saved Forecasts → comparison images, animation and optional verification.

## Data and grid

The data directory contains `202607DD/CREF/Z_RADA_..._CREF_202607DD_HHMMSS.bin`.
The reader matches the observation timestamp at the end of the filename, then
chooses the latest generation for duplicate observations. It discovers nested
files and supports cross-day windows. This demo deliberately indexes July only;
August files can remain on disk without appearing in sample listings.

The reference window begins **2026-07-01 00:00 UTC**. Six context frames end at
**00:30 UTC**. The model consumes the latest five and produces twenty forecasts
at +6, +12, …, +120 minutes. Twenty subsequent observations provide the reference.
Missing time steps are rejected, never filled with repeated frames.

The default region is centered at **118°E, 32°N**. The target is a WGS84 local
Lambert azimuthal equal-area grid with **3000 m spacing and 128×128 cells**,
covering **384×384 km** edge to edge. Native CREF is 0.01° geographic sampling;
nearest source-cell sampling maps it onto the target grid. X and Y increase in
metres, and the data orientation follows those coordinates. Evaluation uses the
same stored grid for observation and prediction.

Reference encoding was checked against the adjacent CMARadar readers: raw
`-1280` is no echo and becomes background 0; other raw values at or below `-1000`
are masked. Valid negative reflectivity is clipped to background for this demo.
A region containing masked history pixels is rejected with a request to choose
another center or time. Target masks remain available to verification.

## Weight and interpretation

Copy `artifacts/sevir/nowcastnet/NowcastNet.onnx` to
`artifacts/cma_radar/nowcastnet/NowcastNet.onnx`. Both must have SHA256:

```
773b6cbe0b1e0c8cea3b62932dc60b108b60885a54f7445a0074a48d4810b37d
```

The CMA adapter clips valid input to 0..75 dBZ, linearly maps to 0..255 and applies
the original model normalization. Output uses the inverse mapping and is clipped
to 0..75 dBZ. This numerical mapping does not convert CREF into physical VIL.
Runtime uses six-minute steps instead of the model's five-minute training steps.
The products and outputs label this **cross-domain demo; forecast skill is
unvalidated**. Scores establish that verification executes, not business skill.

## Prepare the local runtime

Use a handler built from this branch; older releases do not support these
products. End users continue to run prebuilt handlers. Build images from the
repository root when preparing the development demo:

```sh
git submodule update --init --recursive
docker build -f adapters/cma_radar/Dockerfile -t nib-ingestion:cma-demo .
docker build -f adapters/cma_radar/nowcastnet/Dockerfile -t nib-nowcastnet:cma-demo .
docker build -f diagnostics/Dockerfile -t nib-diagnostics:automatic-flow .
```

Use [the example configuration](../tests/runtime/cma-demo.config.yaml). The
runtime tree comes from `paths.resources` (`runtime.yaml` in each data/model
directory); the configured `cma_radar` manifest mounts the whole data folder.
Choose the existing `cma-radar` directory for the CMA data source in Checkout,
or set `NIB_DATA_ROOT` for a one-off run. Do not add `runtime.dataset`,
`data_file`, or image names to `config.yaml`: selecting a different data product
in the Hub switches the runtime without editing the host configuration.

Authorize the exact HTTP Hub origin in `runtime.allowed_origins`. Handler and
result listeners remain loopback. Weights and inputs mount read-only, containers
run offline, and all output stays under `runs/<unique ID>/`.

## Hub flow

1. Initialize Hub's pinned Atlas submodule and start its backend and frontend.
2. Select **Local** and connect this computer before opening the Catalog. With
   the CMA configuration active, the Handler connection does not depend on
   whether Docker or the local data directory is ready yet.
3. Choose **NowcastNet · CMA demo**. Optionally choose **CREF verification** and
   **CREF comparison and animation**. SEVIR products have their own pairings.
4. In Checkout, confirm the data directory, load/select a sample if needed, and
   confirm its date/time and region center. Thresholds use dBZ (defaults 10, 20,
   30, 40, 50). Validate, check local data and weights, then Start.
5. Start freezes/submits once and moves observation to Runs. Handler executes
   the selected diagnostics automatically after inference, even when the browser
   closes. **Open result** only reads the local Visualization / Metrics page.
   Explicit diagnostics retry uses saved Forecasts and never reruns the model.
6. Runs retains the run and diagnostic attempts. The CREF comparison shows the
   latest input, the observation at each lead and the matching prediction. Its
   20-frame GIF uses a common 0..75 dBZ color scale and projected kilometre axes.
   This comparison needs no basemap cache.

## Verification

Runtime Go: `go test ./...`. Python checks run with the existing diagnostics
modules on `PYTHONPATH`; visualization tests run from their module directory so
lockfile provenance resolves correctly. One combined invocation from
`diagnostics/visualization`, using the prepared development environment, is:

```sh
PYTHONPATH=../..:../verification:.:src ../../.pi/environments/runtime/bin/python -m pytest ../../tests -q
```

The environment path is local development support, not a Python installation
requirement for end users. Hub runs its complete backend and frontend suites,
typecheck, lint and production build. Atlas declarations are checksum-verified
through Hub's catalog loader, and real stored manifests are checked against
`contracts/runtime/run-v1.schema.json`. Browser acceptance uses Firefox DevTools
and a real Hub → handler → offline Docker run.
