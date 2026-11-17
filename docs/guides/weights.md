# Weights and image preparation

Weight provenance belongs in `contracts/models/<data>/<model>.json`. Record the
filename, source, and SHA-256. Release preparation should use an immutable source
revision; a URL containing `main` is not an immutable revision even when the
expected file bytes are checksum-pinned. Never commit weights or credentials.

## Handler behavior

The Go Handler reads each model's `runtime.yaml`:

- External weights live at `artifacts/<data>/<model>/<file>`. A matching
  SHA-256 reuses the local file. When it is missing or mismatched, resource
  preparation copies the file from `paths.weight_source`, with the same
  directory layout. A missing source or wrong checksum fails preparation.
- With `weights.embedded: true`, weights come from the model image. The
  Handler does not copy or mount a host weight file. The model verifies the
  embedded file at inference startup. The CMA NowcastNet demo uses this mode.

The Handler does not download external weight files from Hugging Face.
The separate packaging/development helper `utils.weights.ensure_weights` can
fetch a declared URL and verify its checksum; it is not the Handler's resource
preparation path. Provision weights before offline inference.

Missing container images can be pulled from the configured official registry or
optional mirror, then tagged with the local manifest name. See
[runtime resources](../reference/runtime-resources.md) for the image-source
settings. Execution uses `pull_policy: never` and `network_mode: none`;
resource preparation and inference are separate steps.

`cdn.ainowcast.net` is a reserved name for documentation only. Its service
type, routes and availability are undecided. It is not a configured registry,
weight download service or fallback, and no download implementation is implied.

See [the CMA demo](cma-demo.md) for its embedded-weight build and
[model contributions](model-contribution.md) for release-candidate checks.
