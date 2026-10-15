# CMA demo verification record — 2026-10-15

Reference setup: July 2026 CMA CREF files, regional center 118°E/32°N,
128×128 local LAEA grid with exact 3000 m projected spacing. Input context starts
2026-07-01T00:00:00Z and ends 00:30Z. The forecast has 20 leads (+6..+120 min).

Local inventory: 8,164 July binary files represent 7,439 unique observation times
and 7,389 complete retrospective windows. August observations are excluded from
the demo index. All original 8,554 files, including the incomplete August dates,
were preserved. Removal of 74 empty later date trees was refused by the sandbox;
zero files or date directories were removed.

The local ONNX weight was copied to the CMA artifact directory and its SHA256
matched both the SEVIR source and the declaration:
`773b6cbe0b1e0c8cea3b62932dc60b108b60885a54f7445a0074a48d4810b37d`.
Actual inference ran in Docker with `--network none`, a read-only root filesystem,
and read-only source data/weights. The selected target window has no masked
pixels. Outputs have shape (20,128,128), CREF/dBZ metadata and the actual 6-minute
lead sequence. The weight is SEVIR pretrained and forecast skill remains
unvalidated.

Firefox DevTools acceptance traversed Local runs → actual sample listing →
CMA NowcastNet/verification/visualization selection → region configuration →
local preflight → validation → immutable order/package → local execution →
diagnostics → Runs → result page. All four progress stages succeeded. Firefox
loaded all ten metric SVGs, the 1440-pixel comparison PNG and the 1080-pixel GIF.
The GIF was independently checked to contain twenty frames.

Reference run ID: `20261015T120704Z-fd2fe9ad20dfbd50`. Its initialization is the
last context frame, not the wall-clock run timestamp. The final stored manifest,
including model display names, forecasts and diagnostic attempts, validates
against the pinned Atlas run schema at
`6ef5c5920fe57b430ed1e72c1aaf770590afacf1`. All immutable catalog refs load through
the Hub catalog loader with matching SHA256 values. Atlas PR: #7.

Local evidence is deliberately gitignored: `runs/<run-id>/`,
`.pi/reviews/firefox-cma-demo.log`, and background command logs. Actual datasets,
ONNX, PNG/GIF and SQLite output are not committed.

Checks already completed:

- `go test ./...` passed, including CMA read-only directory routing.
- Runtime/adapters/verification suites: 698 passed, 3 skipped; visualization:
  170 passed, 25 skipped. A combined full run also passed 868 tests with 28
  skips. The final added non-finite-output and real PNG/GIF checks passed in
  the seven-test CMA regression file; the final combined suite is recorded
  below when complete.
- Hub backend: 278 passed; Ruff passed.
- Hub frontend: 103 passed, TypeScript and production build passed. ESLint
  reported no errors and the existing fast-refresh export warning in
  `LocalPreflight.tsx`.

Skipped file/dependency-dependent tests do not establish other data sources,
other operating systems or production forecast skill. The real CMA inference
and rendering checks above did execute with local observation files and weights.

Implementation checkouts: runtime task branch in the main workspace; Atlas and
Hub in `.pi/worktrees/atlas-cma-demo` and `.pi/worktrees/hub-cma-demo`. The original
Hub checkout's pre-existing `backend/uv.lock` edit was preserved. Independent
checkouts were necessary because stale Git locks and sandbox write boundaries
prevented updating the original external checkout.
