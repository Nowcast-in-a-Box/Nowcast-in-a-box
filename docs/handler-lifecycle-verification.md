# Handler lifecycle verification (#26)

Runtime baseline: `3aa18e6`. Companion Hub work: issue #11, branch
`feat/handler-bootstrap`. This record separates executable evidence from source
review and from deployment/platform limitations.

**Status:** automated regression, macOS lifecycle acceptance and the complete
local Firefox connection walkthrough passed. This is not a claim of public
remote-HTTP deployment acceptance.

## Reproduced before implementation

Using an isolated build/configuration, not the existing shared Handler:

- A second startup failed first on the occupied results listener (8766).
- Scheme handling checked a hard-coded API port instead of the selected config.
- A supplied scheme origin could be silently enrolled into the allowlist.
- A foreground Handler exited on terminal SIGHUP.
- An API-port collision could print readiness/token output before startup failed.

Temporary reproduction and real-process harnesses were kept under the ignored
`.pi/handler-lifecycle/`; they are not application dependencies or committed
machine-specific installation instructions.

## Automated regression

- Complete `go test -race ./...` and `go vet ./...` passed.
- Six Handler cross-builds passed: Linux/macOS/Windows, amd64 and arm64.
  Linux/Windows command-regression test binaries also compiled. These are **not**
  claims of native Linux/Windows service acceptance.
- Runtime's adapter/runtime, verification and visualization Python suites were
  rerun in the existing isolated diagnostics development environment. External
  observations, prepared map caches and the standalone Python Playwright check
  remain explicitly gated/skipped; they were not fabricated or downloaded.
- Hub backend: 279 tests and Ruff passed.
- Hub frontend: complete unit, type, lint and production-build checks passed;
  the existing `LocalPreflight.tsx` Fast Refresh warning remained non-fatal.
- Hub's 12 existing Chromium E2E cases passed, including real backend-generated
  packages, immutable fingerprints, exact ZIP/checksum validation and historical
  re-download equality. Stale selectors/old explicit warning-checkbox assumptions
  were aligned with the already-existing Start UI, without changing Checkout's
  production acceptance semantics or dropping its frozen-contract assertions.

Boundary regressions cover configured ports, generic/unresponsive services,
immutable execution configuration versus stable source-file ownership, trusted
pairing/PNA preflight, no origin enrollment, browser stop denial, active inference
and diagnostics, repeated live run IDs, historical-job/admission races, symlinked
missing paths, cancelled connection generations and stale sample/folder results.
Runs polling is single-flight and bounded; readiness is invalidated on refresh.
The original direct loopback/result-path boundary tests were retained.

## Actual macOS process acceptance

Isolated ports and config files included spaces, quotes, ampersands and symlinked
parents. The existing user Handler was not stopped or reconfigured.

Passed with the actual built binary and current-user launchd:

- The startup CLI exited while its service remained accessible.
- Repeated startup reused the same instance; restart produced a different nonce.
- Both configured listeners were ready before success; no token appeared in output.
- The session file was private (`0600`) and removed after stop.
- No login LaunchAgent was installed; `KeepAlive` was false.
- The background process could find Docker (`24.0.2`).
- Changed images were rejected by plain start and applied by explicit restart.
- Changing the API port/dataset did not lose ownership of the old service.
- Creating run/artifact directories under symlink parents did not change identity.
- A deliberately slow non-execution HTTP request completed before restart:
  approximately **3.29 seconds**, then a new instance served both ports.
- With no session/API but a still-registered PID, restart waited for that PID to
  exit naturally: approximately **3.26 seconds**. Nothing was force-killed.

The native app uses AppleEvent URL delivery, not shell `$1`. Registration keeps
the selected binary/config and effective host/port overrides. Real app creation
also verified that an absent Bundle ID is added before ad-hoc signing.

## Firefox DevTools evidence and remaining limits

Real Firefox 158, a separate test profile, actual Hub production build and an
isolated local Handler were used. Page/console inspection used Firefox DevTools
RDP; trusted pointer input used Firefox's native WebDriver BiDi endpoint. The
browser application confirmation was retained. The test selected only an
isolated app bundle, leaving the user's existing application untouched.

Verified at Hub `http://127.0.0.1:63662/runs`, targeting API
`http://127.0.0.1:63659` (the fixture file instead declared IPv6/API port 63660):

- Cancelling browser confirmation preserved the page and produced actionable
  timeout/retry guidance after **8.09 seconds**.
- Accepting Firefox's site/application confirmations cold-started the actual
  Handler through AppleEvent delivery. Host/port overrides survived registration.
- Connection succeeded independently of the subsequent environment-not-ready
  result. Only the address, not a token, appeared in persistent browser storage.
- Catalog/Runs navigation and full refresh restored the same instance over HTTP,
  without another scheme prompt.
- Explicit CLI restart produced a new instance; automatic HTTP pairing recovered
  the new session and refreshed capabilities. Observed requests submitted no jobs.
- Disconnect followed by refresh sent no local Handler requests and did not
  restore the connection.
- Reconnecting to the running instance reused it without a scheme prompt. The
  original fixture config bytes were unchanged throughout.

The isolated Firefox profile retained its application-confirmation dialogs and
selected only the isolated application bundle. It did not grant an arbitrary
website access or change the user's existing app. Temporary harness timing and
RDP-event-filter failures were corrected and rerun, not counted as product passes.

Not established here:

- A clean-machine install or availability of a usable GitHub Release bundle.
- A formally deployed public HTTP Hub's origin/network-permission behavior;
  its real origin has not been provided. Local cross-origin testing cannot prove it.
- Native Linux/Windows persistence or end-to-end execution.
- A new real model inference/automatic-diagnostics workflow in this stage; that
  belongs to the following execution/results stage.

No credentials, real observations, weights, screenshots or run outputs belong in
this verification document or its commit. Local raw evidence remains ignored.
