# Diagnostics prerequisite verification (#10)

This prerequisite preserves the delivered Visualization MVP under
`diagnostics/visualization/`, stages the existing diagnostics CLI and request
examples, and records outputs under `runs/<unique-id>/`. It depends on the
handler layout migration (#6). PR #12 supplies the separate verification module.
This handoff does not implement the common computation entry point, the real
Forecast reader, integration of metrics into that entry point, HTTP jobs or the
shared result web server.

The Unix/Windows launchers keep `diagnostics` before its flags. Generated IDs
use a UTC timestamp plus 64 random bits; existing run directories are rejected
rather than overwritten. Runner tests check requests, image overrides, mounts,
network isolation arguments, product protocol, timeout cleanup and failed-run
preservation. These tests do not prove that the scaffold image computes metrics.

## Evidence

- Integrated-main baseline: applied the diagnostics handoff to main `05df086` after PRs #11 and #12. `go test -race ./...`, Go 1.22.12 tests/vet and all six cross-builds passed again. No verification module files changed. The original working-tree files and all delivered visualization source bytes remain preserved.

- Source delivery integrity: all 45 entries in `visualization/SHA256SUMS.txt` matched. All 46 delivered Visualization files matched the original branch byte-for-byte; rendering code and source records were not rewritten.
- A module-local Python 3.12.12 uv virtual environment installed the exact 29 versions in `requirements-dev.lock.txt`, then installed `nib-visualization` with `--no-deps`.
- `.venv/bin/python -m pytest -q`: **170 passed, 25 skipped**, with three NumPy 2.5 deprecation warnings from the existing terrain test fixture.
- `.venv/bin/python -m ruff check src scripts tests`: passed.
- Real WMO/OCHA cache paths were not configured, so the 25 gated real-cache checks were skipped. This is library regression evidence, not real-basemap acceptance.
- New scaffold/CLI changes pass `git diff --check`. The byte-preserved handoff Markdown retains its original explicit line-break whitespace.

- `go test ./...`, `go vet ./...`, `go test -race ./...` and `scripts/cross-build.sh` passed on macOS/arm64 with Go 1.27.2. All six Linux/macOS/Windows amd64/arm64 targets built.
- The final targeted launcher and generated-ID checks passed: daemon/diagnostics argument order, paths with spaces, flag overrides, child exit code propagation and distinct run IDs at a fixed timestamp.
- All six Unix shell scripts passed syntax checks. The actual compiled macOS diagnostics launcher accepted the subcommand and flags, preserved a rejected request and failed status, and refused a duplicate run ID without modifying the original result. PowerShell execution was not available locally.
- The handler layout prerequisite passed native macOS process checks and every CI/release job stayed parked.

- After the direct-review fixes, `go test -race ./...`, `GOTOOLCHAIN=go1.22.12 go test ./...`, `GOTOOLCHAIN=go1.22.12 go vet ./...` and all six cross-builds passed again. These are the final code checks for this prerequisite.

Direct CLI review found and fixed native Windows Docker path conversion,
timeout classification when the Docker CLI reports only an exit code, and
symlink/special-file bypasses of the input socket restriction. Regression tests
cover these boundaries; the post-review handler tests and vet pass. The separate
background review child lacked provider credentials and supplied no independent
review evidence.

## Limits

The delivered Dockerfile still names an absent `diagnostics` module and only
installs the scaffold dependencies. Its entry point and rendering-library
installation are deliberately deferred to the next integration phase. This
limitation is stated in the diagnostics README and config comments.

Real basemap acceptance needs the caches named in the visualization README;
skipped real-cache checks must not be reported as full acceptance. Model
inference and browser-driven Hub/handler execution belong to the post-merge
integration plan.
