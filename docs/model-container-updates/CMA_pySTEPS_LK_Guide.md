# CMA Radar + pySTEPS LK: Container Guide

## Purpose and contract

`adapters/cma_radar/pysteps_lk/` packages the validated CMA CREF optical-flow
baseline as an offline NiB model container:

```text
CMA CREF history (dBZ)
  -> Data Adapter / run/inputs/context.npz
  -> pySTEPS Lucas-Kanade + semi-Lagrangian extrapolation
  -> run/forecasts/pysteps_lk/fields.npz
```

- Input: six CMA CREF frames at six-minute cadence. The algorithm estimates
  motion from the latest three frames.
- Output: 20 deterministic forecasts from +6 to +120 minutes, with shape
  `[time, y, x]`.
- Units: CREF remains `dBZ`; it is not converted to rainfall rate.
- The algorithm has no learned weights. `algorithm-manifest.json` pins the
  pySTEPS version and parameters and acts as the current Handler compatibility
  artifact.
- The container is one-shot and offline: it does not download data or poll.

## Files in this update

```text
adapters/cma_radar/pysteps_lk/
  Dockerfile                 # Python and pySTEPS runtime
  requirements.txt
  model_run.py               # standard entrypoint from adapters/model_runner.py
  model.py                   # LK optical flow and extrapolation
  adapter.py                 # run/NPZ hand-off
  runtime.yaml               # Handler resource declaration
  algorithm-manifest.json    # pinned algorithm/version metadata

scripts/prepare_pysteps_cma_run.py  # temporary local Data Adapter/Handler stand-in
scripts/test_pysteps_cma.py          # standalone algorithm check
scripts/visualize_pysteps_run.py     # forecast-versus-observation figure
```

## 1. Build the image

Start Docker Desktop, then run this from the NiB repository root:

```powershell
docker build -f adapters/cma_radar/pysteps_lk/Dockerfile `
  -t nib-pysteps-lk:cma-1 .
```

## 2. Optional standalone algorithm check

Replace `--data-dir` with the actual CMA CREF directory when necessary:

```powershell
.\.venv312\Scripts\python.exe -m scripts.test_pysteps_cma `
  --data-dir "C:\dataloader\CREF20240705\05" `
  --start "2024-07-05T00:00:00Z" `
  --bbox 117 31 119 33
```

## 3. Create a local NiB run

Until the final Handler/Data Adapter orchestration is wired, this command
This command creates the same run structure locally for standalone checks:

```powershell
New-Item -ItemType Directory -Force .\runs | Out-Null

.\.venv312\Scripts\python.exe -m scripts.prepare_pysteps_cma_run `
  --data-dir "C:\dataloader\CREF20240705\05" `
  --start "2024-07-05T00:00:00Z" `
  --bbox 117 31 119 33 `
  --runs .\runs `
  --id pysteps-cma-20240705-0000
```

This creates:

```text
runs/pysteps-cma-20240705-0000/
  manifest.json             # run index: data, times, grid and products
  inputs/context.npz        # six historical CREF frames
  inputs/target.npz         # 20 observed future frames for evaluation
```

## 4. Run the model container

```powershell
docker run --rm --network none --read-only --tmpfs /tmp `
  -e MPLCONFIGDIR=/tmp `
  -v "${PWD}\runs\pysteps-cma-20240705-0000:/run" `
  nib-pysteps-lk:cma-1 `
  --run /run `
  --weights /opt/nib/pysteps/algorithm-manifest.json
```

The container writes:

```text
runs/pysteps-cma-20240705-0000/forecasts/pysteps_lk/fields.npz
```

## 5. Visualize forecast and observations

```powershell
.\.venv312\Scripts\python.exe -m scripts.visualize_pysteps_run `
  --run .\runs\pysteps-cma-20240705-0000

explorer .\runs\pysteps-cma-20240705-0000\diagnostics
```

The output `pysteps_lk_comparison.png` contains +6, +30, +60 and +120 minute
forecast and observation panels.

## Handler integration status

The model is registered in Atlas as `pysteps-lk-cma` v2 and
`atlas/runtime/products-v2.json` maps that product to `cma_radar/pysteps_lk`.
The image carries `atlas/contracts/models/cma_radar/pysteps_lk.json` and uses
the standard model entrypoint, so Handler can select the product and launch the
image declared in `runtime.yaml`. The local preparation script remains a
reproducible offline check.
