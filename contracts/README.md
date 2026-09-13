# Contracts

Schemas shared by the handler (preflight) and by code that runs inside
Docker. This directory is documentation and schema, not a Python package
and not something end users install.

## Run config (YAML v1)

The Hub writes this file. The handler validates it before starting a
container.

```yaml
version: 1
data:
  id: sevir-vil
model:
  id: wadepre
run:
  start_time: "2017-06-13T15:05:00Z"
  forecast_horizon: 20
```

`start_time` is UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ`, or `sample_id`
instead. Unknown keys are errors. `forecast_horizon` is a frame count.

This is the *run* YAML, different from the repo-root [`config.yaml`][cfg]
that the handler reads for host, port, and paths.

## Package manifests

Each data or model package ships a `manifest.json` that declares fields,
dtypes, spatial shape, frame counts, and cadence. Model images also
point at a Hugging Face artifact (see [weights.md][weights]).

JSON Schema files belong here when that work is claimed. Do not add a
host-side `uv` project to hold them.

[weights]: ../docs/weights.md
[cfg]: ../config.yaml
