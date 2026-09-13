## Issue

Closes #

## What changed

## How to verify

CI cross-compiles Linux, macOS, and Windows binaries and uploads the
`nib-handler` artifact. Users do not build locally.

Handler contributors, optional:

```sh
cd handler && go test ./...
```

## Checklist

- [ ] The branch is `type/<issue-number>-slug` and commits mention `(#N)`
- [ ] Host Python / `uv` / root `pyproject.toml` was not added
- [ ] No weights, HDF5, ONNX, or secrets
- [ ] Handler tests pass, or this PR does not touch `handler/`
