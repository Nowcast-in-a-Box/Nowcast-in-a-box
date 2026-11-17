# Automatic diagnostics and local results verification (#28)

Baseline: Runtime lifecycle commit `ee8043a` (PR #27). Companion Hub issue #13
continues the connection branch (PR #12, itself based on pending #10). This is
not a claim that those earlier PRs are merged or deployed.

## Automated regression

- Complete `go test -race ./...` and `go vet ./...` passed in the final Runtime
  state.
- Six OS/architecture Handler cross-builds passed; Linux/Windows are compile
  evidence, not native execution.
- Complete existing Runtime Python suites passed after the changes:
  adapter/runtime **86 passed, 2 skipped**; verification **659 passed, 1 skipped**;
  visualization **170 passed, 25 skipped**. Skips are the established external
  observation/catalog, prepared basemap cache and standalone Playwright gates.
- Hub backend **284 passed** and Ruff passed. Frontend **136 passed**, typecheck,
  lint (one existing non-blocking Fast Refresh warning) and production build
  passed. All **12 Chromium E2E** cases passed, including real package ZIP bytes,
  frozen fingerprints and byte-identical historical re-download.
- Added regressions cover automatic-vs-non-selected diagnostics, operation/report
  identity, partial/failure retention, extra model registration across reruns,
  interrupted operations, same-second latest selection, missing/unregistered/
  invalid-schema reports, prior Docker project rejection, cleanup command failure
  across same-process/restart, historical polling, and orphaned inference.
- Hub regressions cover first Local gate/Cloud honesty, failure that preserves the
  visible draft while blocking execution, same frozen package after ambiguous
  submission and intervening revalidation, cancellation for session/Cloud,
  draft/environment changes during package read/download, connection-bound
  preflight, focused-run failure consistency, and recovered polling errors.

## Actual local CMA + NowcastNet execution

A dedicated configuration, API/results listeners, run/artifact roots and Hub
state were used. The existing shared Handler and demo run were not stopped,
reused or modified. Real Firefox 158 used Firefox DevTools RDP for page/console/
network observation and its native WebDriver BiDi endpoint only for trusted clicks
and keyboard input.

1. Started at a clean Hub with no selected mode. Cloud explicitly said execution
   was unavailable; no local request was sent. Local showed connection before any
   Catalog product; after the explicit connection, the real Catalog appeared.
2. Selected CMA Radar, its paired NowcastNet, CREF verification and CREF
   visualization. Real July defaults, validation and the local data/weight
   preflight passed, then Start froze/submitted once and navigated to Runs.
3. Firefox was moved to a blank page while Handler continued. The saved job
   reached `succeeded / finished`; its own diagnostic operation and report existed
   before the browser returned. This is evidence that execution was not browser-
   driven or dependent on a polling page.
4. Runs opened that operation's report. Firefox DevTools established the title,
   both native radio labels, 12 loaded result images, Visualization initially
   visible and Metrics hidden, no `<script>`/iframe, and keyboard switching focus
   to Metrics. Viewing did not alter job/manifest/forecast bytes.
5. An explicit **Generate diagnostics from saved Forecasts** created a new
   operation/report in the same run. Forecast file digests and model Compose files
   remained unchanged; no model/ingestion Compose was rewritten. Runs displayed
   the new operation's report rather than the first success.
6. A copied real-run container invocation deliberately used one unsupported metric
   and a requested visualization. Diagnostics exited 1 with verification failure,
   yet visualization artifacts and a `partial` report were written. Inputs and
   Forecasts retained their digests. Firefox opened the report, loaded the
   successful visualization, switched with the keyboard to Metrics and saw the
   escaped failure message. This used read-only input/forecast mounts.
7. A harmless isolated container carrying an old diagnostic Compose project label
   was left active. Real continuous inference/diagnostics admission returned
   HTTP 409 naming the prior project; Handler did not kill it. After the test
   container exited naturally, normal stop succeeded.

These are local offline-container checks. They do not prove public Hub deployment,
clean-machine installation, cloud execution, native non-macOS lifecycle, or model
skill. The deliberate failure was an input to the real diagnostics container, not
a change to production metric logic. Its copied run, images, logs and harnesses
remain ignored and are not commit candidates.

## Remaining boundaries

- A Hub connection timeout cannot distinguish a missing app from a denied
  browser/site permission; the UI gives checks rather than inventing a cause.
- A formally deployed public HTTP Hub origin/network behavior is not supplied.
- Native Linux/Windows background service behavior remains unverified.
- The report shell presents existing artifacts and future metrics web UI is still
  owned separately. No cloud execution or full force-cancel is included.
- Concurrent external mutation of a run outside Handler is outside the execution
  lock; existing filesystem/API path protections still apply when serving.

## Demo readiness and global forecast map (#46)

The demo-readiness implementation was checked locally with Chromium, real CMA
data, pinned NowcastNet weights, Docker, and an isolated installed macOS Handler.
The shared user Handler and Hub backend were not stopped. The root macOS launcher
was exercised with `serve` and an isolated configuration; URI registration and
native Windows/Linux startup were not revalidated. Six cross-builds succeeded.

- `go test ./...` passed. The canonical Python suites completed with **956 passed,
  9 skipped**; all skips were optional OCHA cache integrations. Real WMO checks
  ran against a checksum-verified local cache.
- Hub frontend **186 tests** and production build passed. The fast local E2E
  completed **2 passed, 1 real-only skip**; the real Docker E2E completed
  **3 passed**. The map viewer completed **64 tests**, **4 Sites tests**,
  typecheck, standalone and embedded builds. Its compatible patched lockfile
  reported zero npm audit vulnerabilities.
- Actual saved-address recovery produced three bounded eight-second attempt
  groups. Network request counts stayed unchanged over a further 30 seconds.
  Each attempt includes internal probes; three attempts does not mean three
  HTTP requests. Orders list/detail did not query data without a session;
  disconnect removed 13 displayed rows immediately. Main retained its vertical
  position. Layout was checked at 1440×900, 390×844 and a 276px Checkout sidebar.
- A real Hub inference run `20261025T031624Z-32a09c677e0056e2` finished successfully
  with only `mae` in the frozen selection, Handler groups, verification config,
  and result selector. Its first Metrics chart decoded automatically as a line.
- A separate copied three-sample real run exported 60 COGs. Every display cell,
  mask, first pixel centre, bound, CRS and valid time matched its display field;
  the three scientific forecast hashes stayed unchanged. The shared terrain
  was 7200×3600 with cache mask version 4 and 3431 WMO tiles. NOAA source bytes
  matched the recorded size and SHA-256 before preparation.
- The actual result surface exercised world/data navigation, zoom, layer toggles,
  opacity, first/middle/last frame selection, 2× playback, pause, sample switching,
  map unmount on Metrics and actual PNG download. The PNG included terrain, WMO,
  model/time, legend and forecast-skill notice. No external map/CDN requests were
  observed. Viewport emulation is not physical touch-device evidence.
- COG requests through the Handler proxy returned 206 with correct Content-Range
  and 1024 bytes; out-of-range requests returned 416. A legacy image result was
  opened before a real COG retry, then reopened: its viewer refreshed the shared
  basemap mount and verification root, served terrain ranges, and offered only
  the newly selected `mae`. Result HTML used `Cache-Control: no-store`.

Ignored evidence and exported PNGs are under `.pi/demo-readiness/`; E2E keeps its
printed temporary workspace. Inputs, global terrain, weights, images and runs
remain outside version control. These checks do not establish forecast skill,
public deployment, or clean-machine installation on other operating systems.
