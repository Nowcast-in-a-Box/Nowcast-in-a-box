# Runtime integration verification (#14)

## Actual SEVIR → WADEPre → verification container run

Executed on macOS/arm64 through Docker Desktop's explicit `desktop-linux` context.
All stages used `--network none`, read-only container roots, tmpfs `/tmp` and the
host Unix UID/GID. Data and weights were mounted read-only; outputs stayed in a
new run directory. This invocation used Docker CLI directly. Handler Compose is covered by the
later run below; browser access remains separate.

Run: `runs/docker-dadd3ba7f9434c24` in the integration worktree.
Sample: `S696651@2017-06-27T00:35:00Z`.
Init: `2017-06-27T01:00:00Z`; actual leads +5 through +100 minutes.

- Ingestion, WADEPre and diagnostics acceptance images built successfully.
- Real HDF5 context `(6,128,128)` and target `(20,128,128)` were read.
- Real WADEPre ONNX inference produced a finite float32 Forecast `(20,128,128)`
  in raw VIL units, without executing host inference.
- Weight SHA256:
  `55c396165f620934b1262eeac19997f14b00fae5f3a150015a5a84c9cbd6ee3b`.
- Forecast SHA256:
  `128dd468fe3f835f8322d9310bf933b3f915cfb523ffc67d473690a6f90de366`.
- Manifest schema checks passed, and observation/Forecast file digests matched.
- Verification attempt `953d2cb62b8e4c76b7354d2ec3e31a70` completed all 20 tasks;
  none failed or were unavailable. SQLite, summary, SVGs and HTML are retained.
- MAE/RMSE, CSI (`ts`, 100 records) and FSS (200 records) matched direct
  calculations from the saved native-grid arrays at `rtol=1e-7, atol=1e-7`.
  Thresholds use `>=`; FSS uses the published 0.6 neighbourhood coverage rule.
  This comparison does not reread SQLite.
- Container stdout/stderr was retained in the run's acceptance log. ONNX Runtime
  reported an unknown CPU-vendor warning; it did not prevent inference.

Image identities for this snapshot:

| Image | Docker image SHA256 |
|---|---|
| ingestion | `b62a20832f2f6dfc3e67abdc4f78fb108c945e5b583d94e0e5ced523cf42bf45` |
| WADEPre | `84095a152adc06f6aad08740f64c474f4b11e95bf24d6950bf583f33d3e6a4bc` |
| diagnostics | `975eb4a142ab3aa7c9f5424dbcb73f373cb88329e42ae2a031e539e041d4abd4` |

That first manifest recorded atlas revision `draft` because contract PR #2 was
still open. The published pin is main commit
`43e2b306c247ce5633dca0d65c06893dc37409f2`.

## Handler Compose run

Run `runs/20261013T043518Z-25840161d80787e5` was submitted through the Go handler
from the real Hub frozen package `c1918faad45611a33db71cda4d0884749b3cb105519578f2ed2cae56b6194d67`.
The handler wrote Compose files and started ingestion, WADEPre and diagnostics
with network none. The job finished `succeeded`.

- Manifest atlas revision is the merged pin above.
- Sample `S696651@2017-06-27T00:35:00Z`, init `2017-06-27T01:00:00Z`.
- Forecast is finite float32 `(20,128,128)`, raw units, leads +5 through +100.
  SHA256 matches the earlier direct container Forecast:
  `128dd468fe3f835f8322d9310bf933b3f915cfb523ffc67d473690a6f90de366`.
- Verification `e8cd6c6f7aa542a0bbd5cdbdeeda2c89` completed 20 tasks with no
  failures. This package requested MAE/RMSE only.
- Visualization `17abca7e54454e648cc9cea4ce71f5c9` wrote 20 frames, `map.png`,
  a 1,008,597-byte animation and an HTML gallery from the saved Forecast and
  the checked WMO cache. Container logs show ONNX Runtime executed in Docker.
- NowcastNet and exPreCast images were not built, so they correctly reported
  not ready. Optional-diagnostics permutations and a container diagnostic
  retry were not part of this run.
- The built Hub Runs page was opened from `https://127.0.0.1:8443` in headless
  Chromium. It connected to the loopback handler, reported Docker and data
  ready, and listed this run with its verification and visualization results.
  This does not prove a public HTTPS deployment origin or a user permission prompt.

## Regression evidence

- Verification full suite at its earlier integration snapshot: 659 passed,
  1 browser module skipped (Playwright absent), 1 dependency warning.
- Focused runtime/verification regression: 141 passed, followed by smaller checks
  on subsequent sample/diagnostic entrypoint changes. Go tests/race/vet passed on
  the current handler work.
- Real WMO cache: 3,431 PBF tiles plus supporting resources; all 3,446 SHA256
  entries passed. Visualization with that cache: 186 passed, 9 OCHA-dependent
  tests skipped, 3 existing NumPy fixture warnings. This is not a real model
  visualization acceptance.
- Real Hub package generated through its existing checkout/packaging services
  was accepted by Go's runtime parser with matching data/model/diagnostic bindings.
- Updated Hub backend: 167 tests and Ruff passed, with two existing dependency
  deprecation warnings. Updated frontend: 62 tests, typecheck, lint and production
  build passed. Current handler Go tests/race/vet passed. These checks do not
  substitute for the pending real browser/Compose acceptance.

No VM data was deleted to bypass the OrbStack startup failure. Docker Desktop was
started instead; the global default Docker context was left unchanged. Public
HTTPS Hub deployment, PowerShell and non-macOS execution remain unverified.
