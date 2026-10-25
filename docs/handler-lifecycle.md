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

`start` is the default. `serve` is the foreground contributor entry. One Handler
is allowed per operating-system user, including across installation directories
and configured ports. A fixed lifecycle lock serializes activation, replacement
and uninstall; a separate process-lifetime lock prevents a second live Handler.
Repeated startup of the same executable build reuses the actual running address
without replacing its configuration or URL registration.

Starting a different executable build automatically stops the old idle Handler,
retires its service and application registration, and activates this build. The
executable SHA256 identifies a build; this is replacement, not semantic-version
ordering or a promise to prevent choosing an older download. Legacy path-scoped
private sessions are discovered and authenticated during migration. Active jobs
block replacement and uninstall; neither operation force-cancels inference.
Old download folders, data, runs and artifacts are not deleted or moved.

`-config /absolute/path/config.yaml` selects settings for a new activation.
`-host` and `-port` override its loopback API address. Another config or port
does not create another instance. Use explicit `restart` to apply changes to a
running build. A missing old config after a directory rename triggers safe
replacement using the newly selected configuration. Existing saved absolute
data/output paths are not rewritten automatically.
For a contributor-provided executable, set `NIB_HANDLER_BIN` before launching.

The default API and results addresses are `http://127.0.0.1:8765` and
`http://127.0.0.1:8766`. The diagnostics result viewer is published at the
loopback address in `config.yaml` (`diagnostics.host` / `diagnostics.port`) and
proxied through the results listener. All three come from `config.yaml`; the Hub
website's port is unrelated. Startup does not select another port or kill a
conflicting program.
An old Handler without the identity protocol needs an explicit upgrade/stop,
not silent adoption.

## Remove the local installation

Use the platform uninstaller when a fresh-user pass needs the Handler to be
installed from scratch:

- macOS: `Uninstall NiB.command`
- Windows: `Uninstall NiB.cmd`
- Linux: `Uninstall NiB.sh`

The uninstaller calls the Handler `uninstall` command. Under the lifecycle lock,
it discovers the actual active instance from the fixed private control record,
authenticates its identity, requests idle shutdown, verifies listener closure and
process completion, and removes the service and application/URI registration.
The removable cache is deleted only after successful shutdown; lock files live
outside it so deleting a cache cannot create two independent lock owners.
An uninstall from a renamed or different release folder controls the same
instance. A missing binary, invalid private session or busy Handler aborts rather
than claiming success or killing an unverified PID.

The default keeps `runs/`, `artifacts/`, and user data. Use `--dry-run` to inspect
the planned operations, or `--purge` to additionally remove generated assets and
downloaded binaries inside the uninstaller's release directory. Purge requires
explicit confirmation and never deletes the external data directory or another
release's download folder. Linux/Windows registration changes are implemented
but require native platform acceptance; macOS lifecycle is exercised locally.

## Configuration changes and local control

Configuration compatibility remains useful for diagnostics, but it is not the
instance owner. The fixed control record locates the actual running address,
instance ID, executable digest and private local-stop credential. Optional empty
paths remain empty in the configuration fingerprint rather than resolving to
the invoking terminal's working directory. Data-root, trusted-origin and image
source changes remain dynamically reloadable.

The Hub saves the selected folder per data source in `nib-settings.json` next to
`config.yaml`. The file is gitignored, contains `data_roots` (and accepts the old
single `data_root`), is written atomically with mode `0600`, and is read after
`config.yaml` and before the `NIB_DATA_ROOT` fallback. Deleting it returns the
Handler to the fallback.

Local control ownership is independent of the config filename and release
directory. `status`, `stop`, `restart` and `uninstall` find the recorded live
instance even if the command selected another config or port. Idle automatic
replacement retires the old service before publishing the new registration.
Registration now runs inside activation, not before startup in shell scripts,
so a refused busy upgrade cannot redirect the user's application registration.

On macOS, the fixed cache is `~/Library/Caches/NiB/handler/`, containing
`handler.log`, `handler.plist` and private `session.json` (mode `0600`). Locks
are under the user's configuration directory in `NiB/locks/` and deliberately
survive uninstall. Legacy profile records are used only to verify and retire old
instances, not to create new profiles. The session contains a credential;
**never paste it into issues or logs**. Startup output does not print the token.
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

The CLI lifecycle regressions also run inside a Linux Go 1.22 container:
`start`, `connect`, and `restart` start real child processes that the parent
authenticates and stops. Both test child entrypoints must inherit the parent's
isolated HOME/cache; resetting it in `TestMain` makes local control lose the
child's session. This checks Linux CLI execution, not desktop URI integration.
