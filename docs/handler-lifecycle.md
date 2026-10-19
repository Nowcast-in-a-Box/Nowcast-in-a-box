# Local Handler lifecycle and browser connection (#26)

Users run a prebuilt Handler. Go is a contributor tool, not an installation
requirement. Release binaries and container images must actually be provided by
the maintainer; the launchers do not build a missing binary for users.

## macOS: start once, keep running

Double-click `Start NiB.command` (or `bin/macos/start.command`), or run it from
any working directory. If launchd cannot start the background job, the
launcher keeps the Handler running in that Terminal window instead of exiting.
The launcher registers `~/Applications/NiB Handler.app` for `nib://` URLs and
starts an on-demand user launchd job. Closing Terminal or the Hub page does not
stop it. It is **not** installed in `~/Library/LaunchAgents`, is not enabled at
login, and is not automatically restarted after a crash. It does not promise
to survive logout.

```sh
bin/macos/start.command start
bin/macos/start.command status
bin/macos/start.command stop
bin/macos/start.command restart
```

`start` is the default. `serve` is the explicit foreground contributor entry.
Repeat startup reuses a running compatible instance. Ordinary `stop`, `restart`
and termination signals refuse while an inference job is queued/running or a
diagnostic attempt owns the execution resource. Wait for completion first.
There is no force-cancel operation in this release. An OS force-kill cannot be
prevented; unfinished saved jobs are shown as interrupted, not resubmitted.

`-config /absolute/path/config.yaml` selects another configuration. `-host` and
`-port` override its API bind address and are also retained by the registered
macOS app. Only loopback addresses are accepted. The registered app uses the
configuration selected by the latest registration; it is not a profile chooser.
For a contributor-provided executable, set `NIB_HANDLER_BIN` before launching.

The default API and results addresses are `http://127.0.0.1:8765` and
`http://127.0.0.1:8766`. Both come from `config.yaml`; the Hub website's port is
unrelated. Startup does not select another port or kill a conflicting program.
An old Handler without the identity protocol needs an explicit upgrade/stop,
not silent adoption.

## Remove the local installation

Use the platform uninstaller when a fresh-user pass needs the Handler to be
installed from scratch:

- macOS: `Uninstall NiB.command`
- Windows: `Uninstall NiB.cmd`
- Linux: `Uninstall NiB.sh`

The default removes the macOS application and launchd job, URL-scheme
registration, `~/Library/Caches/NiB/handler`, the project `nib-settings.json`,
and the Handler log. It keeps `runs/`, `artifacts/`, and the data folder. On
Linux and Windows it removes the desktop/registry registration and the
per-user Handler cache instead of a macOS application.

Use `--dry-run` to inspect the planned paths, or `--purge` to additionally
remove generated runs, artifacts, packaged resources, logs, and downloaded
Handler binaries. `--purge` asks for a second confirmation and never deletes
the external data folder. An active queued or running job blocks uninstall;
wait for it to finish or stop it from the local CLI first.

## Configuration changes and local control

Compatibility checks include the canonical config source, API/results addresses,
run/artifact locations, the runtime resources root, Docker context and Atlas
revision. Images, data files, catalogs and mounts live in the resources
manifests, so changing one of those manifests requires a Handler restart; the
compatibility identity covers the resources root, not the manifest contents.
Data-root and trusted-origin updates do not change that identity. Credentials
are not part of the public identity response.

The Hub saves the selected folder per data source in `nib-settings.json` next to
`config.yaml`. The file is gitignored, contains `data_roots` (and accepts the old
single `data_root`), is written atomically with mode `0600`, and is read after
`config.yaml` and before the `NIB_DATA_ROOT` fallback. Deleting it returns the
Handler to the fallback.

Local control ownership follows the config file's canonical path, separately
from compatibility. After editing execution settings or a port, use `restart`
with the **same config file**. The CLI verifies the old instance against its
private session before stopping it, then starts with the new settings. A plain
`start` refuses to silently keep using old execution settings. Keep the config
file valid and at its original path until its existing service is stopped.

Startup/status print actual addresses, the config source and local service-file
location. On macOS the latter is under
`~/Library/Caches/NiB/handler/<profile-id>/`: `handler.log`, `handler.plist` and a
private `session.json` (mode `0600`). The session contains a credential; **do not
paste it into issues or logs**. Routine output never prints the token.
The older per-config `logs/handler.log` records lifecycle and job events.

## Webpage connection

1. Install/run the platform launcher once to register the application.
2. In Hub, explicitly connect this computer. The browser first checks the fixed
   local target, then can open `nib://connect?origin=<Hub origin>` if needed.
3. Confirm the browser's **Open NiB Handler** prompt and any browser local-network
   permission. There is no second application authorization dialog or token to copy.
4. Pairing and identity checks have an approximately eight-second overall budget.
   Docker, input-directory, image and weight checks happen separately afterward.
   A connected Handler can legitimately report an environment that is not ready.
5. If startup/confirmation takes longer, manually start Handler and retry. A
   timeout cannot prove whether an application is missing, a prompt was declined,
   local networking was denied, or the deployment origin is wrong.

Only the loopback address is remembered by the browser; tokens stay in memory.
Refresh and temporary interruptions restore observation over HTTP without opening
another scheme prompt or submitting jobs. Instance changes refresh the session.
Explicit Disconnect removes the saved address and cancels pending recovery.

The frontend defaults to API port 8765, never derives it from the webpage port,
and does not scan ports. A dedicated non-default-address development/build may
set `VITE_NIB_HANDLER_URL` to the matching loopback endpoint. There is no bearer
credential in a scheme URL, remote callback or browser persistent storage.

## Trust and deployment

`runtime.allowed_origins` must contain exact, approved Hub origins. The checked-in
configuration provides the documented local development origins on ports 8000
and 5173. A real remote deployment origin must be supplied by its maintainer;
there is no fabricated public deployment address.

The scheme validates the Hub origin but **never adds it to the allowlist**.
Browser confirmation is not permission for an arbitrary website to control the
machine. Pairing checks the actual HTTP Origin against both the requested origin
and the current trusted configuration. Host/loopback checks, exact-origin CORS
and authenticated execution remain enforced. Local stop control rejects browser
Origin requests. Connection and authenticated requests do not follow redirects.

The Hub server does not proxy the user's localhost, and data/weights/results do
not move to it. Browser restrictions for a publicly deployed HTTP website must
be verified on that deployment; a localhost Hub check does not establish that.

## Linux and Windows scope

The existing platform launchers and registered scheme invocations keep a
foreground Handler path when no service is running. `start`, `connect` and
`restart` can use that path; another invocation can query or stop it. They do
not claim macOS-style terminal-independent persistence. Cross-builds and command
regressions are not native platform acceptance.
