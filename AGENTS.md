# AGENTS.md

Nowcast-in-a-Box local runtime. Users run a Go handler on Linux, macOS, or
Windows. The handler talks to Docker Compose for model inference. Metrics
and visualization run in the diagnostics container, outside the model
image. The handler diagnostics CLI can invoke that container; its computation
entry point and HTTP integration are not implemented yet. The Hub only selects
and configures.

Architecture notes live under `docs/` as markdown. Visio, Word, and PDF
drafts stay on disk and are not committed.
Contribution: [CONTRIBUTING.md][contrib]. GitHub UI: [github-setup.md][gh].
Weights: [weights.md][weights].

Work IDs are GitHub issue numbers. Branches `feat/short-slug`, commits
`feat: … (#12)`, PRs `Closes #12`. Letter codes such as E1 or C2 are a
planning map in `docs/modules.yaml`, not git ids.

## Layout

```text
handler/          host daemon source (Go)
handler/cmd/      nib-handler process entry
bin/              prebuilt binaries and OS entry points
  linux/          run.sh
  macos/          start.command
  windows/        run.ps1
diagnostics/      image scaffold and rendering library; computation entry pending
runs/             diagnostics run outputs (gitignored)
artifacts/        downloaded weights and docker config (not committed)
contracts/        YAML/manifest schemas. Not a Python package.
  data/           data-reader declarations
  models/<data>/  model declarations grouped by data source
interfaces/       data and model contracts. Not a host package.
utils/            path resolution and weight download. Not a host package.
adapters/         packaging-time source; not a host package
  catalog.json    web index of data readers and paired models
  <data-id>/      one data source; source.py
    <model-id>/   one model; its program is adapter.py
docs/             architecture and process
scripts/          cross-build.sh; wrappers that call bin/
tests/             handler, runtime, verification, and visualization tests
config.yaml       bind addresses, ports, paths
```

No repository-root `pyproject.toml` or `uv.lock`. Python belongs
inside a Model Container, or in the diagnostics image.
`adapters/` is that packaging source. The handler does not import it.
Data directory names are data names. Model adapters live only under the
data source they were paired with. `catalog.json` is the web index.
Observation samples stay outside git. File-backed tests use
`NIB_DATA_ROOT`, which this repository does not set.

## Commands

Users run a prebuilt binary. They do not install Go. CI and release
workflows are parked until there is code worth building.

```sh
bin/linux/run.sh           # Linux
bin/macos/start.command    # macOS (Finder or terminal)
bin/windows/run.ps1        # Windows
```

`scripts/run.sh`, `scripts/run.ps1`, and `scripts/start.command` call the
matching `bin/` entry. Each entry uses `bin/<os>/nib-handler-<os>-<arch>`,
or fetches it from the latest GitHub Release. Host and port come from
`config.yaml`.

Handler source only (contributors with Go 1.22+):

```sh
go test ./...
go build -o "bin/nib-handler$(go env GOEXE)" ./handler/cmd/nib-handler
scripts/cross-build.sh
```

## Architecture

```text
NiB HUB / Catalog                 selection + config; does not execute
        ↓
Handler (host daemon)             loopback HTTP, URI scheme, preflight, jobs
        ↓ Docker Compose
Data Ingestion                    read-only /inputs/data
Model Container                   one image per algorithm
        ↓
diagnostics container             metrics and frames; not the model image
artifacts/                        downloaded weights and docker config
runs/                             diagnostics outputs (gitignored)
```

Adapters are optional packaging-time conversion. Runtime data access is
Data Ingestion.

## Invariants

Always:

- Bind the handler and the review page to loopback (`127.0.0.1` or `::1`).
  Published Docker ports stay loopback.
- Users do not compile the handler. CI will build Linux, macOS, and Windows binaries when that workflow is turned on.
- Read bind addresses and paths from `config.yaml`. Flags override the file.
- Fail if the data root is missing. Never invent a host path.
- One container per AI algorithm. Metrics and visualization run in the
  diagnostics container, outside the model image.
- Weights land in `artifacts/` (pinned Hugging Face revision + sha256).
  Runtime `--network none`.
- Failed runs remain inspectable.

Ask first:

- A second task profile.
- Binding a non-loopback address.
- Installing arbitrary adapter code at runtime.
- Putting inference back on the host as a Python package.
- A top-level catalog directory layout for data and models.

Never:

- Accept Docker arguments or shell commands from the browser.
- Commit weights, ONNX, HDF5, run PNGs, PDF, Word, or Visio files.
- Add a root uv/Python project so end users `uv sync`.
- Copy `~/Dev/AINPP/prototype` trees wholesale.

## Style

Handler: Go 1.22, stdlib plus `gopkg.in/yaml.v3`, tests in `tests/handler/`.
Use `filepath` and `os.PathSeparator` for host paths; do not assume `/`.
Container modules follow their own image. File an issue and get it
assigned before you write code.

Python: Black. Config is `black.toml`, not a root `pyproject.toml`.
Run `scripts/format-python.sh` before sending Python changes.

[contrib]: CONTRIBUTING.md
[gh]: docs/github-setup.md
[weights]: docs/weights.md
