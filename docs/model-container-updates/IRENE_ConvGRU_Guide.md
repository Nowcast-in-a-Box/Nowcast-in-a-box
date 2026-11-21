# Italy DPC SRI + IRENE ConvGRU: Container Guide

## Purpose and product chain

IRENE is Adriano's public, fully packaged ConvGRU Docker image. It is not an
ONNX or Python weight file copied into the NiB repository. This update adds a
DPC Data Adapter and a Converter so the original image can participate in a
testable NiB product chain:

```text
Full DPC SRI GeoTIFFs (local directory or DPC API)
  -> dpc_sri_geotiff Data Adapter
  -> /run/inputs/dpc/              # six full GeoTIFFs read by IRENE
  -> original IRENE image
  -> /run/outputs/irene/           # 48 GeoTIFFs + original IRENE manifest
  -> irene_converter
  -> /run/forecasts/irene/fields.npz  # [member, time, y, x]
  -> NiB visualization / verification
```

Key facts:

- Data product: Italian DPC SRI, variable `RR`, unit `mm/h`, 1 km grid,
  five-minute cadence.
- Six input scans produce 12 leads × 4 members = 48 GeoTIFF forecasts.
- `context.npz` is a NiB-standard companion copy. The original full GeoTIFFs
  remain in `inputs/dpc` and are what IRENE actually reads.
- The independent converter creates the standard NiB ensemble ForecastField;
  it never overwrites the original TIFF output.
- `irene.pt` is not committed to Git. It should be provisioned and
  SHA-256-verified by a Handler artifact mechanism. Local tests use Docker
  volume `irene-nib-test-artifacts`.

## Files in this update

```text
adapters/dpc_sri_geotiff/
  ingest.py / online_ingest.py          # local and online DPC Data Adapter
  fetch_truth.py / attach_target.py     # delayed truth retrieval/attachment
  Dockerfile / runtime.yaml
  irene/runtime.yaml                    # pinned image, weight checksum, mounts
  irene_converter/                      # GeoTIFF ensemble -> fields.npz
  COMPATIBILITY.md                      # compatibility limits of existing data

scripts/visualize_irene_run.py          # inputs, members, mean and spread plots
scripts/verify_irene_run.py              # truth-based metrics and figures
scripts/compare_irene_windows.py         # common-area comparison between windows
utils/stored_fields.py                   # four-dimensional ensemble support
diagnostics/fields.py                    # ensemble member selection support
```

## 1. One-time local preparation

Run from the NiB repository root:

```powershell
docker build -f adapters/dpc_sri_geotiff/Dockerfile `
  -t nib-ingestion:dpc-sri-geotiff-local .

docker build -f adapters/dpc_sri_geotiff/irene_converter/Dockerfile `
  -t nib-irene-converter:local .
```

On first use, initialize the verified IRENE artifacts while online:

```powershell
docker run --rm `
  -v irene-nib-test-artifacts:/app/artifacts/v1 `
  --entrypoint /opt/venv/bin/irene-download-artifacts `
  ghcr.io/sinapse-inpe/irene-nowcast:latest `
  --bundle /app/artifacts/v1
```

## 2. Create a run from local full DPC GeoTIFF data

The directory must contain six consecutive complete DPC files named
`dpc_sri.YYYYMMDDHHMM.tif`. The previously validated directory is:

```text
C:\dataloader\irene-container-test\inputs
```

```powershell
$runId = "irene-dpc-local-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$revision = "0000000000000000000000000000000000000000"

docker run --rm `
  --entrypoint /opt/venv/bin/python `
  -v "C:\dataloader\irene-container-test\inputs:/data:ro" `
  -v "${PWD}\runs:/runs" `
  nib-ingestion:dpc-sri-geotiff-local `
  -m adapters.dpc_sri_geotiff.ingest `
  --data /data `
  --start 2026-11-19T03:25:00Z `
  --runs /runs `
  --id $runId `
  --atlas-revision $revision
```

## 3. Configurable domain: position, size and geographic metadata

The original DPC image is `1200×1400`, while each NiB run may select one
in-bounds computation window. Its width and height must be multiples of 32,
which is the ConvGRU computational constraint.

For a centred 320×320 experiment, append this to the previous command:

```powershell
--domain-size 320
```

For an explicitly positioned window, supply all four values together:

```powershell
--window-col 368 --window-row 362 --window-width 320 --window-height 320
```

The Data Adapter retains the full TIFFs, creates matching `context.npz` and
`config/irene.json`, and records the pixel window, projected coordinates and
WGS84 `bbox_wgs84` in `manifest.json`. Visualization subtitles display the
longitude/latitude bounding box automatically.

This does **not** make arbitrary radar products scientifically compatible with
IRENE. The current decoder and trained distribution are DPC SRI. CMA should
continue to use pySTEPS; another source requires a dedicated input adapter and
validation.

## 4. Create one run from the DPC API

This obtains the newest available complete six-scan DPC window, writes a run,
then exits; it is not a permanent polling service.

```powershell
$runId = "irene-dpc-online-" + (Get-Date -Format "yyyyMMdd-HHmmss")

docker run --rm `
  --entrypoint /opt/venv/bin/python `
  -v "C:\dataloader\irene-online-cache:/cache" `
  -v "${PWD}\runs:/runs" `
  nib-ingestion:dpc-sri-geotiff-local `
  -m adapters.dpc_sri_geotiff.online_ingest `
  --cache-dir /cache `
  --runs /runs `
  --id $runId `
  --atlas-revision 0000000000000000000000000000000000
```

The newest DPC scan normally lags wall-clock time by around 7–10 minutes. In
the final deployment, a Handler or scheduler should invoke this adapter every
five minutes; the model container itself should not download data.

## 5. Run IRENE, convert results and visualize

The following works for a run from either Section 2 or 4:

```powershell
docker run --rm `
  -v irene-nib-test-artifacts:/app/artifacts/v1 `
  -v "${PWD}\runs\${runId}:/run" `
  ghcr.io/sinapse-inpe/irene-nowcast:latest `
  /opt/venv/bin/irene-nowcast `
  --config /run/config/irene.json `
  --source local `
  --input-dir /run/inputs/dpc `
  --output-dir /run/outputs/irene `
  --steps 12 `
  --device cpu `
  --threads 2

docker run --rm `
  -v "${PWD}\runs\${runId}:/run" `
  nib-irene-converter:local `
  --run /run `
  --input-dir /run/outputs/irene

.\.venv312\Scripts\python.exe -m scripts.visualize_irene_run `
  --run ".\runs\$runId"

explorer ".\runs\$runId\diagnostics\irene_ensemble"
```

Outputs:

```text
outputs/irene/.../                    # original 48 GeoTIFFs and manifest
forecasts/irene/fields.npz            # [4, 12, height, width]
diagnostics/irene_ensemble/
  input_and_ensemble_overview.png
  ensemble_mean_and_spread.png
```

## 6. Attach future truth and verify

At the instant of a real-time forecast, its future observations do not yet
exist. Around one hour later, download the twelve scans beginning at +5 min
and attach them to the same run:

```powershell
docker run --rm `
  --entrypoint /opt/venv/bin/python `
  -v "C:\dataloader\irene-online-cache:/cache" `
  nib-ingestion:dpc-sri-geotiff-local `
  -m adapters.dpc_sri_geotiff.fetch_truth `
  --cache-dir /cache `
  --start 2026-11-19T03:55:00Z `
  --steps 12

docker run --rm `
  --entrypoint /opt/venv/bin/python `
  -v "${PWD}\runs\${runId}:/run" `
  -v "C:\dataloader\irene-online-cache:/truth:ro" `
  nib-ingestion:dpc-sri-geotiff-local `
  -m adapters.dpc_sri_geotiff.attach_target `
  --run /run `
  --data /truth `
  --steps 12

.\.venv312\Scripts\python.exe -m scripts.verify_irene_run `
  --run ".\runs\$runId"
```

Verification figures are written to `diagnostics/irene_verification/`.

## Handler integration status

The three pieces are independently runnable: DPC Data Adapter, IRENE model and
Converter. Full automatic orchestration still needs Handler support for an
explicit Converter resource and ordered `IRENE -> converter -> diagnostics`
stages. The converter contract is already explicit:

```text
Input:  irene-geotiff-ensemble.v1
Output: NiB ForecastField NPZ, [member, time, y, x]
```

Only the DPC Data Adapter needs network access. The model, converter and
diagnostics should run with no network.
