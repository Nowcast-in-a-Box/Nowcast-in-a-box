# CMA xarray handoff verification (#24)

Date: 2026-10-17. Scope: the single-run Python data provider, not the later
Handler lifecycle or Hub-flow work. This change is stacked on the CMA demo
branch from PR #23; that prerequisite must land before this can target main.

## Delivered behavior

`diagnostics.run_source.RunSource` implements `list_groups`, `get_group`,
`read_truth` and `read_forecast` over a saved CMA run. Returned eager xarray
fields contain native axes, auxiliary geographic coordinates, CRS/CF metadata,
units and exact valid times. No resampling, inference or metric execution occurs.
The caller supplies an explicit metric configuration and group version.

The [contract](interfaces-we-consume.md) and
[example](../diagnostics/examples/run_source_example.py) document the handoff.
SEVIR support for the new provider remains explicitly deferred; existing
providers were included in regression tests.

## Automated results

The final code passed all configured suites in the diagnostics/contributor
Python 3.12 environment. Visualization is run from its module directory because
its artifact tests use that directory's lockfile. Neither a root Python project
nor end-user Python installation was added.

| Check | Result |
|---|---|
| `pytest -q -ra tests/adapters tests/runtime` | 84 passed, 2 skipped |
| `cd diagnostics/verification && pytest -ra` | 659 passed, 1 skipped |
| `cd diagnostics/visualization && pytest -q -ra` | 170 passed, 25 skipped |
| Total Python | **913 passed, 28 skipped** |
| `go test ./...` | passed |
| `go vet ./...` | passed |
| Ruff E4/E7/E9/F/I/UP/B for new Python files | passed |
| Black with `black.toml` for changed Python files | passed |
| `git diff --check` | passed |

The Python import path included the repository root, `diagnostics/verification`,
`diagnostics/visualization/src` and `diagnostics/visualization`. Verification's
web test extras, including uvicorn, were installed in the isolated contributor
environment. Initial setup failures were resolved before the final full run;
they are not counted as passes.

Skipped checks were two file-backed adapter checks without `NIB_DATA_ROOT`, one
standalone verification Playwright test without Playwright, and 25 visualization
checks requiring prepared WMO/OCHA caches. Existing FastAPI/Starlette and NumPy
warnings remain. No claim is made that those skipped integration paths passed.

## Scientific identity and container check

The existing CMA/NowcastNet run `20261015T120704Z-fd2fe9ad20dfbd50` was read without
running models. For every one of its 20 leads (+6 through +120 minutes), truth
and forecast arrays and masks were compared element-by-element with
`StoredFields`; native axes, units and valid times matched. The provider loaded
three cubes (context, target and the selected forecast) once each. Checksums of
the manifest, job and source arrays were unchanged after the comparison.

The diagnostics image was rebuilt with the new module and packaged example.
The actual example was then executed with a read-only run mount, read-only root
filesystem, temporary `/tmp`, and `--network none`. It returned matching 128x128
CREF/dBZ fields, native 3 km LAEA axes, auxiliary lon/lat and WKT, with three cube
loads and no written outputs. This establishes data access, not forecast skill.

## Review regressions

Read-only review identified two defects before delivery:

- Invalid target or forecast CRS could escape the uniform exception contract.
  Initialization now wraps pyproj `ProjError` in `RunSourceError`.
- A cached artifact path could be reused with a conflicting descriptor, skipping
  the second declaration's checksum/time/shape checks. The shared reader now
  compares against a deep-copied descriptor snapshot before returning a cache
  hit. Equal descriptors still reuse the same verified arrays.

Seven targeted regression cases failed before the fixes, then all 50 related
provider/storage tests passed. The complete final suites above were rerun after
these fixes. A separate read-only re-review found both issues resolved and no
additional actionable issue within that scope.

## Firefox DevTools check

A separate Firefox 158.0 profile was connected through the native Firefox
DevTools Remote Debugging Protocol to the running loopback Handler's existing
CMA result page. The page reported `Run result`; all ten registered metric SVG
images completed with non-zero natural dimensions; the DevTools console had no
cached page/console errors. The temporary browser was stopped after inspection.

This was a compatibility smoke check of existing saved results. A-stage adds no
new webpage; it does not establish the later scheme, Local/Cloud, connection or
new tabbed-result interactions. Those require their own Firefox checks when
implemented. No production browser profile was modified.

## Handoff and remaining limits

A separate, gitignored local document explains the concrete run's calling
commands and container mounts for the metrics developer. It is temporary and
can be deleted; data and generated results were not committed.

The provider is not a persistent group-version registry. The platform must not
reuse the same group ID/version for a changed configuration. The model sample is
a cross-domain demonstration with unvalidated forecast skill. Handler lifecycle,
remote Hub browser access, cloud execution and the later result-page work are
not included in this delivery.
