# Local new-user acceptance runbook

This is the manual pass that follows the automated local acceptance suite.
It walks a first-time user from the Hub through a local CMA inference and the
Docker-produced diagnostics page. Automated coverage lives in the Hub
repository (`npm run test:e2e:local`); this runbook is for the real-machine
check with Docker.

## 1. Prerequisites

- Docker is running. With `runtime.image_registry` set, the Handler pulls the
  diagnostics image at startup and the data/model images during Validate.
  Local tags `nib-ingestion:cma-demo`, `nib-nowcastnet:cma-demo-embedded` and
  `nib-diagnostics:automatic-flow-basemap-v1` are also accepted when prebuilt.
- A CMA Radar data folder, for example `/Users/you/data/cma-radar`.
- The embedded NowcastNet model image includes the pinned ONNX weight; users
  need no host weight file for this model.
- The current diagnostics image includes the offline global terrain/WMO
  display basemap; users need no `artifacts/basemap` directory.
- A built Handler binary and the Hub checkout (or a published Hub) on this
  machine. No GitHub Release is required for this runbook.

The first start pulls missed image layers from `runtime.image_registry`; a
missing image is reported with the exact image name if the pull fails.

## 2. Start the Handler

Use the normal launcher, as a first-time user would. No terminal command,
flag, or environment variable is required:

- macOS: double-click `Start NiB.command`.
- Windows: double-click `Start NiB.cmd`.
- Linux: run `Start NiB.sh`.

The launcher starts the packaged binary with the repository `config.yaml`
(8765/8766). That file contains host settings only: ports, paths, trusted Hub
origins and the Atlas revision. It has no dataset or data-file setting, so the
same launcher works for CMA, SEVIR, or a later data source. Choose the data
folder in Checkout; it is saved in `nib-settings.json` next to `config.yaml`.

The launcher reuses a compatible running instance. To stop it later:

```sh
bin/macos/start.command stop                 # macOS
bin/linux/run.sh stop                        # Linux
powershell -File bin\windows\run.ps1 stop    # Windows
```

Do not stop a Handler you did not start, especially one on ports 8765/8766.

Contributor test harness only: the Hub browser E2E uses
`.pi/automatic-flow/config.yaml` on 50154/50155 and `NIB_CONFIG` to keep test
ports isolated. That file and `NIB_CONFIG` are never part of the user flow.

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

Open `http://127.0.0.1:5173`. The repository `config.yaml` already lists this
origin in `runtime.allowed_origins`. For a built static Hub, serve `frontend/dist`
at port 8000 instead; the dist defaults to the Handler at 8765, and the
repository config already allows `http://127.0.0.1:8000`. A published Hub uses
its own HTTPS origin, which the distributor must add to `runtime.allowed_origins`
before distribution.

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
9. **Open the result.** When the run finishes, press `Open result`. Handler
   starts one diagnostics result viewer and proxies it on the existing result
   listener; the page contains `Visualization` and `Metrics` tabs in one
   document. Verify the real-forecast map, world/data navigation, frame playback,
   layers and PNG export. Switch to Metrics: the first selected available method
   must render a line chart without pressing Run query. Only the methods selected
   in Checkout should be offered.
   Also open a saved image-only or metrics-only result, then explicitly retry
   with visualization. Reopen the result and verify terrain is available and
   the first selected metric reflects the new diagnostic report.

The result viewer uses `diagnostics.host` and `diagnostics.port` in
`config.yaml`. Its container is stopped when Handler stops.

## 5. Evidence to capture

- Screenshot of the timeout/start instruction for the current OS.
- Screenshot of the saved data path and the resources panel after Validate.
- The run id and stage from Local runs.
- `logs/handler.log` from the Handler folder.
- `GET /v1/resources` output if a preparation step fails (use the Hub network
  panel or the Handler log; the token is never printed).
- A screenshot of the result page with both tabs.

## 6. Cleanup and fresh-user reset

For an ordinary cleanup:

- Stop the Hub dev server and backend.
- Stop the Handler with the launcher `stop` command.
- Leave `runs/` in place for inspection; remove it manually only when the
  evidence is no longer needed.
- Do not delete the user's artifacts or data folder.

For a fresh-user pass, use the platform uninstaller:

- macOS: `Uninstall NiB.command --dry-run`, then `Uninstall NiB.command`
- Windows: `Uninstall NiB.cmd --dry-run`, then `Uninstall NiB.cmd`
- Linux: `Uninstall NiB.sh --dry-run`, then `Uninstall NiB.sh`

The default removes the local Handler installation and settings but keeps
`runs/` and `artifacts/`. Add `--purge` only in a throwaway checkout or after
backing up those folders; it also removes generated runs, artifacts, resources,
logs, and locally downloaded binaries. Never add the external data folder to a
purge. Start again with `Start NiB.<os>` and confirm a fresh app/launchd job and
`/v1/identity` response.

## 7. Known limits

- No public container registry or GitHub Release is part of this pass. Images
  are expected to exist locally; the Handler only checks and reports them.
- The weight "download" is a local folder copy, not a network download.
- macOS folder picking, launchd registration, and the browser `nib://`
  confirmation remain manual behaviors.
- The real inference depends on the CMA data folder, image tags, and weight
  matching the pinned SHA-256 in this repository.
