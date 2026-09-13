# Development

The host program is a Go daemon in `handler/`. End users run a binary
that CI already built for Linux, macOS, or Windows. They do not install
Go or Python.

Model code, ONNX Runtime, and algorithm-specific Python run inside
Docker images the handler starts. Evaluation and visualization are a
separate host process in `review/`. There is no repository-root
`pyproject.toml`.

## Run (no Go)

`scripts/run.sh` (Linux/macOS), `scripts/run.ps1` (Windows), or
`scripts/start.command` (macOS Finder) start the matching file under
`handler/dist/`. If that file is missing, the script downloads it from
the latest GitHub Release.

CI on every pull request and on `main` cross-compiles six binaries
(Linux / macOS / Windows × amd64 / arm64) with
[`scripts/cross-build.sh`](../scripts/cross-build.sh) and uploads them
as the `nib-handler` artifact. That artifact is for checking a PR. It
is not the user download, and it is not in git.

## Releases

Git does not store handler binaries. Users get them from a
[GitHub Release][releases].

1. Merge the work to `main`.
2. Tag it and push the tag (this is what triggers the release workflow):

   ```sh
   git checkout main
   git pull
   git tag v0.1.0
   git push origin v0.1.0
   ```

3. [`.github/workflows/release.yml`](../.github/workflows/release.yml)
   builds the six binaries and attaches them to a Release named after
   the tag (`nib-handler-linux-amd64`, `nib-handler-darwin-arm64`,
   `nib-handler-windows-amd64.exe`, …).

Do not create the Release in the GitHub UI first. Pushing the tag is
the whole publish step. `scripts/run.sh` / `run.ps1` then fetch
`releases/latest`.

To ship a fix, tag `v0.1.1` the same way.

```sh
curl -sS http://127.0.0.1:8765/health
curl -sS http://127.0.0.1:8765/v1/status
```

If you changed `handler.port` in [`config.yaml`](../config.yaml), curl
that port instead.

Non-loopback `-host` values, or a non-loopback host in the config, exit
with status 2. Requests whose `Host` is not loopback (`127.0.0.1` or
`::1`) are 403.

## Handler source

Only people changing `handler/` need [Go][go] 1.22+. Bind addresses
live in `config.yaml`. Flags override the file.

```sh
cd handler
go test ./...
go build -o "nib-handler$(go env GOEXE)" .
./nib-handler -config ../config.yaml
```

`go env GOEXE` is `.exe` on Windows and empty on Unix.

To rebuild all six binaries locally (same as CI):

```sh
scripts/cross-build.sh
```

The `go test` matrix on Ubuntu, Windows, and macOS is in the workflow
file but parked until there is enough coverage to gate merges.

Host paths must use `path/filepath`, not string concatenation with `/`.

## Config

[`config.yaml`](../config.yaml) is the startup file:

- `handler.host` / `handler.port`: loopback HTTP for the daemon
- `review.host` / `review.port`: loopback HTTP for the eval/viz page
- `paths.artifacts`: downloaded weights and docker config
- `paths.data_root`: optional; otherwise `NIB_DATA_ROOT`

```sh
./nib-handler -config ../config.yaml -port 9001
```

`-config`, `-host`, and `-port` are the flags. `NIB_CONFIG` and
`NIB_DATA_ROOT` are the env vars.

## Container work

When you take an issue for a Model Container, add a Dockerfile and the
code that image needs. Pin dependencies in that Dockerfile. Do not add
them to a root Python project.

Weights are downloaded into `artifacts/`. See [weights.md][weights].

## Review page

`review/` is where evaluation (MAE, RMSE, CSI, …) and visualization
(frame PNGs, comparisons) will live. The handler starts that process on
the host; it is not a Model Container. Leave the folder empty until that
issue is claimed. Prototype reference: `evaluation/` and `post_process/`
in AINPP.

## Style

- Handler: Go stdlib + yaml.v3, tests next to the code, exact equality.
- Container code (later): follow that image's existing files.
- Errors fail closed. Do not invent a data path or a metric.

[go]: https://go.dev/dl/
[weights]: weights.md
[releases]: https://github.com/Nowcast-in-a-Box/Nowcast-in-a-box/releases
