# Real-time ArcoDataHub VMI + pySTEPS Optical-Flow Test

This test replays a locally downloaded real-time ArcoDataHub VMI snapshot through the local data adapter and the pySTEPS Lucas–Kanade optical-flow container.

The full workflow is:

```text
ArcoDataHub latest VMI frames -> local Zarr snapshot -> Data Adapter -> pySTEPS container -> forecast and visualizations
```

## Run with latest ArcoDataHub data

The workflow can retrieve the latest three five-minute VMI frames automatically. API credentials are never stored in the YAML configuration or passed to the container.

On the ArcoDataHub **My datasets** page, use **Copy** for `opera-plus-it-vmi`. Then run the following command and paste the complete copied Access URL when prompted. The pasted URL is hidden in the terminal and only its credentials are saved in the private user-profile file `%USERPROFILE%\.nib\arcodatahub.json`.

```powershell
cd <repository-root>

.\.venv312\Scripts\python.exe -m scripts.run_arco_pysteps --setup-access-url
```

Check credentials and dataset permission before downloading:

```powershell
.\.venv312\Scripts\python.exe -m scripts.run_arco_pysteps --check-access
```

If the access check passes, run the complete latest-data workflow:

```powershell
.\.venv312\Scripts\python.exe -m scripts.run_arco_pysteps
```

The live retrieval was previously verified with a three-frame snapshot. At the time of writing, live access is blocked by ArcoDataHub HTTP 401 for the configured credentials; this is an account/key or subscription issue, not a container or optical-flow failure. Use the local replay below while the current key is being resolved.

## Run the local replay

Run in PowerShell:

```powershell
cd <repository-root>

.\.venv312\Scripts\python.exe -m scripts.run_arco_pysteps `
  --snapshot "<path-to-local-arco-vmi-snapshot>"
```

This uses the existing local Zarr snapshot only. It does not contact ArcoDataHub or use API credentials. The command runs the local data adapter and the already-built pySTEPS container.

## Generate visualizations

After the replay completes, run:

```powershell
$run = Get-ChildItem .\runs -Directory -Filter "arco-vmi-*" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

.\.venv312\Scripts\python.exe -m scripts.visualize_realtime_pysteps_run `
  --run $run.FullName
```

Outputs:

```text
$run\diagnostics\pysteps_lk_realtime_overview.png
$run\diagnostics\pysteps_lk_realtime_nowcast.gif
```

## Current status

Local execution completed successfully. However, the current optical-flow forecast quality is limited and requires further improvement.
