# AGENTS.md

## Model and reader changes

Trace one existing caller to its interface, implementation and declaration before editing.
Keep one source for each shared fact, even when a change touches several implementations.

- Model identity, input/output requirements and weights belong in
  `atlas/contracts/models/<data>/<model>.json`. Use `DeclaredModelAdapter` in
  `interfaces/model.py` to read them. Implement backend preparation and inference
  in `adapters/<data>/<model>/adapter.py`. Name each data directory after its data source.
  Place each model adapter only under its paired data source. Follow the
  [model template](docs/templates/model/adapter.py) and [SEVIR NowcastNet example](adapters/sevir/nowcastnet/adapter.py).
- Reader declarations live in `atlas/contracts/data/<data-id>.json`. Implement
  reader behavior in `adapters/<data-id>/source.py` or its existing shared base.
  Use `DataAdapter` in `interfaces/data.py` for operations every reader supports.
  Use `RuntimeDataAdapter` for runtime window operations. When a shared operation
  changes, update the interface, affected implementations, callers and contract tests together.
  The [sample probe](adapters/reader_probe/__main__.py) shows how callers use `adapter_for()`.
- Atlas is a pinned submodule. Shared protocol schemas also belong in `atlas/contracts/`.
  `atlas/runtime/adapters-catalog.json` is the public reader index.
  Follow the [contribution guide](docs/guides/model-contribution.md) for packaging and manual Atlas registration.
- Ask before adding a second editable source for an Atlas declaration, a new top-level
  catalog layout, or a reader entry point that bypasses an adapter interface.
  Explain which existing declaration or interface you checked and what it cannot express.

## Repository layout

| Path                  | Responsibility                                                                                                                                           |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `handler/`            | Host daemon. `handler/cmd/nib-handler/` is its entry point.                                                                                              |
| `bin/<os>/`           | Prebuilt binaries and platform launchers for Linux, macOS and Windows.                                                                                   |
| `adapters/`, `utils/` | Packaging-time adapter code and optional conversions. `utils/` provides path resolution and weight downloads. Runtime reads go through the Data Adapter. |
| `diagnostics/`        | Container code for verification metrics and visualization.                                                                                               |
| `scripts/`            | Maintainer tools only, including cross-build, formatting, packaging and export.                                                                          |
| `tests/`              | Handler, adapter, runtime and diagnostics tests. Put Go handler tests in `tests/handler/`.                                                               |
| `docs/`               | Architecture and process documentation in Markdown.                                                                                                      |
| `orders/`             | Local order definitions in YAML (gitignored).                                                                                                            |
| `runs/`               | Per-run forecasts, logs and diagnostic outputs (gitignored).                                                                                             |
| `artifacts/`          | Downloaded weights and generated Docker files (gitignored).                                                                                              |

## Runtime boundaries

- The Hub selects and configures. Users run a prebuilt Go handler on Linux, macOS or Windows.
  The handler owns loopback HTTP, URI scheme handling, preflight and jobs. It invokes
  Docker Compose for inference. Each algorithm has one model container.
  Metrics and visualization run in the diagnostics container.
- Keep `interfaces/`, `utils/` and `adapters/` out of the host runtime. Do not add a root
  `pyproject.toml` or `uv.lock`. Ask before moving inference to the host, installing arbitrary
  adapter code at runtime, or introducing a second task profile.
- Bind the handler, review page and published Docker ports to loopback. Ask before changing
  that boundary. Reject Docker arguments and shell commands from the browser.
- Read bind addresses and paths from `config.yaml`. Flags override the file. Use `filepath`
  and `os.PathSeparator` for host paths so they work on Windows too. If the data root is
  missing, fail without inventing a host path. Mount `/inputs/data` read-only.
- Store weights under `artifacts/` with a pinned Hugging Face revision and SHA-256.
  Use `--network none` at runtime. Keep failed runs inspectable.
- Uninstall keeps `runs/` and `artifacts/` by default. An explicit `--purge` may remove
  generated files. Always preserve the user’s data directory, including during a purge.
- Keep weights, ONNX, HDF5, observation files, run outputs, PDFs, Word and Visio files out of git.
  Do not copy `~/Dev/AINPP/prototype` trees wholesale.

## Working in this repo

- Start code changes from an assigned GitHub issue. Use the issue number in the commit
  subject and `Closes #12` in the PR body for issue 12. Use branches such as `feat/short-slug`.
  E1 and C2 are planning IDs in `docs/modules.yaml`. Follow [CONTRIBUTING.md](CONTRIBUTING.md) for commit syntax.
- Verify APIs, flags, versions, package names and commit SHAs against source or docs.
  Keep responses concise. Omit emojis and em dashes.
- The handler targets Go 1.22 and uses the standard library plus `gopkg.in/yaml.v3`.
  Run `go vet ./...` and `go test ./...` for handler changes. Build locally with
  `go build -o "bin/nib-handler$(go env GOEXE)" ./handler/cmd/nib-handler` or use
  `scripts/cross-build.sh` for all supported platforms.
- Run `scripts/run-tests.sh` for the full local precheck. Its header lists the Python lockfiles.
  For Python changes, use the root `ruff.toml` and run `ruff format .` and `ruff check .`.
  `scripts/fetch-demo-data.py` downloads samples into the gitignored `testdata/demo-data/`.
  File-backed tests read `NIB_DATA_ROOT` and skip when the required samples are missing.
- For launch, uninstall and release instructions, see the [README](README.md).
