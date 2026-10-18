# Local new-user acceptance runbook

This is the manual pass that follows the automated local acceptance suite.
It walks a first-time user from the Hub through a local CMA inference and the
Docker-produced diagnostics page. Automated coverage lives in the Hub
repository (`npm run test:e2e:local`); this runbook is for the real-machine
check with Docker.

## 1. Prerequisites

- Docker is running and the three CMA images are present locally:
  `nib-ingestion:cma-demo`, `nib-nowcastnet:cma-demo`,
  `nib-diagnostics:automatic-flow`.
- A CMA Radar data folder, for example `/Users/you/data/cma-radar`.
- `artifacts/cma_radar/nowcastnet/NowcastNet.onnx` exists (135 MB, pinned
  SHA-256), or `paths.weight_source` points at an artifacts-like folder
  that contains it.
- A built Handler binary and the Hub checkout (or a published Hub) on this
  machine. No GitHub Release is required for this runbook.

The first start does not pull image layers: Docker's local layer cache is the
expected source. Missing images are reported with the exact image name so they
can be built or pulled before the run.

## 2. Start the Handler

The binary already discovers the active `config.yaml` from the working or
program directory, or from `NIB_CONFIG`; `-config` is only needed for a file
with another name. The repository `config.yaml` is the SEVIR default (8765/8766).
For the CMA demo, start with the example without a flag:

```sh
env NIB_CONFIG=tests/runtime/cma-demo.config.yaml .pi/local-e2e/nib-handler
```

The example uses 8765/8766 and allows the Hub dev server on 5173. Its
`paths.resources: ../..` points at the repository tree that carries the data and
model `runtime.yaml` manifests; `nib-diagnostics:automatic-flow` is the manifest
image. It has no dataset or data-file setting: choose the CMA folder in Checkout,
or set `NIB_DATA_ROOT` for a one-off run.

Use the platform launcher instead when starting the installed bundle:

- macOS: double-click `bin/macos/start.command`, or run it from Terminal.
- Windows: in PowerShell, run `bin\windows\run.ps1`.
- Linux: run `./bin/linux/run.sh`.

The launcher reuses a compatible running instance. To stop it later:

```sh
bin/macos/start.command stop          # macOS
bin/linux/run.sh stop                 # Linux
powershell -File bin\windows\run.ps1 stop   # Windows
```

Do not stop a Handler you did not start, especially one on ports 8765/8766.

## 3. Start the Hub

From the Hub repository:

```sh
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

In another terminal:

```sh
cd backend
.venv/bin/python -m uvicorn nib_hub.app:create_app --factory \
  --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:5173`. The Handler configuration must list this exact
origin in `runtime.allowed_origins`.

## 4. Walkthrough and expected outcomes

1. **Choose Local.** The page offers Local and Cloud. Cloud says execution is
   unavailable. Choose Local.
2. **Connect before the Handler is running.** Stop the Handler first, choose
   Local, and press `Connect this computer`. After about 8 seconds the page
   shows `Start NiB Handler on <your OS>` with the fixed launcher path for
   that system. Start the Handler, then press `Retry connection`.
3. **Connect successfully.** The top bar shows `Local Handler connected`, and
   the compact resources line shows the diagnostics image as ready (or the
   exact missing-image message).
4. **Select products.** In the Catalog choose the CMA Radar data source, the
   NowcastNet CMA model, a CREF verification product, and a visualization
   product. The model card only enables Add after the data product is in the
   draft.
5. **Open Checkout and enter the data folder.** Type or paste the CMA data
   folder (the same path used above). Press `Save folder`. The Handler writes
   `nib-settings.json` next to its `config.yaml`, outside git. Restarting the
   Handler must keep the saved path.
6. **Review defaults.** CREF verification shows the default thresholds
   `[10, 20, 30, 40, 50]`, the default metric set, and the default spatial
   half-windows. Edit a value to confirm it is a real input, then restore it.
   The CMA data product exposes the forecast start time; enter an invalid time
   without `Z` first. `Validate Selection` reports the schema error and Start
   stays disabled.
7. **Validate.** Enter a valid window (for example `20260701` and
   `2026-07-01T00:00:00Z`) and press `Validate Selection`. The local panel
   checks the window, reports each image as ready, and prepares a missing
   weight by copying it from `paths.weight_source` with byte progress and a
   SHA-256 check. Start becomes enabled only after those checks pass.
8. **Run.** Press `Start`. The browser submits the frozen package once and
   navigates to Local runs. Closing the page does not stop inference or the
   automatic diagnostics.
9. **Open the result.** When the run finishes, press `Open result`. The page is
   served from the Handler's registered result files. Switch between
   `Visualization` and `Metrics` with the keyboard; the selected panel changes
   without JavaScript.

## 5. Evidence to capture

- Screenshot of the timeout/start instruction for the current OS.
- Screenshot of the saved data path and the resources panel after Validate.
- The run id and stage from Local runs.
- `logs/handler.log` from the Handler folder.
- `GET /v1/resources` output if a preparation step fails (use the Hub network
  panel or the Handler log; the token is never printed).
- A screenshot of the result page with both tabs.

## 6. Cleanup

- Stop the Hub dev server and backend.
- Stop the Handler with the launcher `stop` command.
- Leave `runs/` in place for inspection; remove it manually only when the
  evidence is no longer needed.
- Do not delete the user's artifacts or data folder.

## 7. Known limits

- No public container registry or GitHub Release is part of this pass. Images
  are expected to exist locally; the Handler only checks and reports them.
- The weight "download" is a local folder copy, not a network download.
- macOS folder picking, launchd registration, and the browser `nib://`
  confirmation remain manual behaviors.
- The real inference depends on the CMA data folder, image tags, and weight
  matching the pinned SHA-256 in this repository.
