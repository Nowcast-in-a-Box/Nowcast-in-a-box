# pySTEPS LK Model Container

This directory packages the already validated CMA CREF optical-flow baseline
as one offline NiB model container.  It is deterministic: it has no trained
weights and does not turn CREF into rainfall rate.

## Runtime contract

The Handler mounts one run directory at `/run`.  The entrypoint reads
`/run/inputs/context.npz`, requires finite `CREF` values in `dBZ`, estimates
Lucas-Kanade motion from the latest three frames, and writes twenty forecasts
at 6-minute spacing to `/run/forecasts/pysteps_lk/fields.npz`.

`algorithm-manifest.json` records the pinned pySTEPS version and algorithm
parameters.  It is embedded in the image and checksummed.  The current Handler
calls this required artifact `weights`; it is an interface-compatibility marker,
not a neural-network weight file.

## Build

Start Docker Desktop first, then build from the repository root:

```powershell
docker build -f adapters/cma_radar/pysteps_lk/Dockerfile -t nib-pysteps-lk:cma-1 .
```

Do not run `docker compose` manually.  When a Handler job selects
`pysteps_lk`, the Handler reads `runtime.yaml` and creates its own one-shot,
network-disabled compose invocation.

## Local algorithm check

Use the project Python environment, not an arbitrary system Python:

```powershell
.\.venv312\Scripts\python.exe -m scripts.test_pysteps_cma `
  --data-dir "C:\dataloader\CREF20240705\05" `
  --start "2024-07-05T00:00:00Z" `
  --bbox 117 31 119 33
```

Before accepting an end-to-end build, use the same history window and compare
the standalone output with the container forecast for shape, leads, grid,
units, and numeric values.

## Prepare a local NiB run (before Handler integration)

For now, the final Handler/Data Adapter hand-off is not wired.  This command
creates the same run-directory contract locally from the CMA CREF files:

```powershell
New-Item -ItemType Directory -Force .\runs | Out-Null
.\.venv312\Scripts\python.exe -m scripts.prepare_pysteps_cma_run `
  --data-dir "C:\dataloader\CREF20240705\05" `
  --start "2024-07-05T00:00:00Z" `
  --bbox 117 31 119 33 `
  --runs .\runs `
  --id pysteps-cma-20240705-0000
```

It produces `runs\pysteps-cma-20240705-0000\manifest.json`,
`inputs\context.npz`, and `inputs\target.npz`.  The JSON is **not an
algorithm configuration file**: it is the run index.  It records what data
was prepared, timestamps, grid/units, file paths, checksums, and later the
forecast produced by the container.

Then call the already-built image directly (a stand-in for the future
Handler):

```powershell
docker run --rm --network none --read-only --tmpfs /tmp `
  -e MPLCONFIGDIR=/tmp `
  -v "${PWD}\runs\pysteps-cma-20240705-0000:/run" `
  nib-pysteps-lk:cma-1 `
  --run /run `
  --algorithm-manifest /opt/nib/pysteps/algorithm-manifest.json
```

On success the same `manifest.json` gains a `forecasts.pysteps_lk` record and
the container writes `forecasts\pysteps_lk\fields.npz`.

## View forecast versus observation

The local visualizer reads the target observations and container forecast from
the same run, then writes one PNG with +6, +30, +60 and +120 minute panels:

```powershell
.\.venv312\Scripts\python.exe -m scripts.visualize_pysteps_run `
  --run .\runs\pysteps-cma-20240705-0000
```

The result is `diagnostics\pysteps_lk_comparison.png` inside that run.
