# AGENTS.md

Nowcast-in-a-Box local runtime. Users run a Go handler on Linux, macOS, or
Windows. The handler talks to Docker Compose for model inference. Evaluation
and visualization live in `review/` and run on the host; the handler starts
that page. The Hub only selects and configures.

Architecture notes live under `docs/` as markdown. Visio, Word, and PDF
drafts stay on disk and are not committed.
Contribution: [CONTRIBUTING.md][contrib]. GitHub UI: [github-setup.md][gh].
Weights: [weights.md][weights].

Work ids are GitHub issue numbers. Branches `feat/12-short-slug`, commits
`feat: … (#12)`, PRs `Closes #12`. Letter codes such as E1 or C2 are a
planning map in `docs/modules.yaml`, not git ids.

## Layout

```text
handler/          host daemon (Go). This is what users run.
review/           evaluation + visualization web UI, started on the host
artifacts/        downloaded weights and docker config (not committed)
contracts/        YAML/manifest schemas. Not a Python package.
docs/             architecture and process
scripts/          run.sh, run.ps1, start.command
config.yaml       bind addresses, ports, paths
```

There is no repository-root `pyproject.toml` or `uv.lock`. Python belongs
inside a Model Container, or later in `review/` when that work is claimed.
Do not invent a catalog tree for data and models until that layout is
designed.

## Commands

Users run a CI-built binary. They do not install Go.

```sh
scripts/run.sh          # Linux / macOS; Finder: scripts/start.command
scripts/run.ps1         # Windows
```

The script uses `handler/dist/nib-handler-<os>-<arch>`, or fetches it
from the latest GitHub Release. Host and port come from `config.yaml`.

Handler source only (contributors with Go 1.22+):

```sh
cd handler && go test ./...
cd handler && go build -o "nib-handler$(go env GOEXE)" .
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
review/ (host process)            evaluation + visualization page
artifacts/                        downloaded weights and docker config
Persistent workspace              run outputs (gitignored)
```

Adapters are optional packaging-time conversion. Runtime data access is
Data Ingestion.

## Invariants

Always:

- Bind the handler and the review page to loopback (`127.0.0.1` or `::1`).
  Published Docker ports stay loopback.
- CI builds the handler for Linux, macOS, and Windows. Users do not compile.
- Read bind addresses and paths from `config.yaml`. Flags override the file.
- Fail if the data root is missing. Never invent a host path.
- One container per AI algorithm. Evaluation and visualization stay in
  `review/`, outside the model image.
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

Handler: Go 1.22, stdlib plus `gopkg.in/yaml.v3`, tests in `handler/`.
Use `filepath` and `os.PathSeparator` for host paths; do not assume `/`.
Container modules follow their own image. File an issue and get it
assigned before you write code.

[contrib]: CONTRIBUTING.md
[gh]: docs/github-setup.md
[weights]: docs/weights.md
