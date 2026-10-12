# Handler layout verification (#6)

This change separates the process entry at `handler/cmd/nib-handler` from the
host daemon package, moves the Go module to the repository root, and places
prebuilt executables and OS launchers under `bin/`. Existing `scripts/` launchers
forward to the matching OS entry. Release asset names stay unchanged; CI and
release workflows remain parked.

The existing flags, configuration discovery, environment overrides, loopback
restrictions, `/health`, and `/v1/status` fields are preserved. Status additionally
reports the ordered preflight checks through the same Docker check result. Tests
live under `tests/handler/`, with injected preflight checks to avoid depending on
an installed/running Docker daemon.

The requested issue originally described `internal/app`, `internal/config` and
`internal/docker`; the implementation preserves the prepared, smaller
`handler` package and a thin `cmd` entry. Separate internal packages are not
needed by the current two HTTP endpoints. Diagnostics code is staged separately
under #10.

## Evidence

- Main baseline before migration: `cd handler && go test ./... && go vet ./...` passed.
- Migrated package: `go test ./...`, `go vet ./...` and `go test -race ./...` passed on macOS/arm64 with Go 1.27.2. The final launcher regression tests also passed under Go 1.22.12.
- `scripts/cross-build.sh` built Linux, macOS and Windows for both amd64 and arm64. Binary asset names stayed unchanged, and generated files were ignored by Git.
- All five Unix launch/build scripts passed `bash -n`; the four Unix launcher regression scenarios passed with paths containing spaces, unchanged arguments and exit code 23 propagated from the child.
- The compiled macOS/arm64 binary started in an isolated directory and passed real `/health` and `/v1/status` requests, port override and relative artifacts-path checks. A non-loopback Host returned 403; a non-loopback bind failed with exit 2. Docker was unavailable and was reported as a failed preflight check without breaking the status response.
- Workflow inspection confirmed every CI/release job retained `if: false`.

- Direct code review checked configuration/flag parity against main, HTTP response fields, loopback guards, CLI exit handling, platform paths and launcher/build references; no blocking migration defect remained. The separate background review could not run because its child process lacked provider credentials, so it supplied no independent review evidence.

- `GOTOOLCHAIN=go1.22.12 go test ./...` and `GOTOOLCHAIN=go1.22.12 go vet ./...` passed, including the four launcher regression scenarios.

## Scope limits

Cross-compilation does not establish Windows or Linux runtime behavior. The Unix
launcher regression tests simulate OS/architecture identification and check
paths with spaces, argument forwarding and child exit status. PowerShell runtime
verification requires a PowerShell/Windows environment. The handler does not yet
start model tasks or the visualization service; those belong to the later
Hub/atlas integration.
