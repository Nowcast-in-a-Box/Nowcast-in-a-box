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

## Diagnostics request

`nib-handler diagnostics` reads a different file. It is not the run YAML
above, and it is not the evaluation draft. Required fields are
`observation`, `forecasts`, and `metrics`. Unknown top-level keys are
copied into the container request and are not interpreted. Paths in the
example are placeholders.

See [`diagnostics-request.example.yaml`][diag] and
[`diagnostics/README.md`][dimg].

## Package manifests

Data and model declarations live in this directory, not beside adapter
code. `data/*.json` describes a data reader. `models/<data-id>/*.json`
describes a model paired with that data source. `adapters/catalog.json`
is the web index and must match these files. A later external scan may
read the JSON here; it must not import adapter Python.

Model images also point at a weight artifact (see [weights.md][weights]).
JSON Schema files belong here when that work is claimed. Do not add a
host-side `uv` project to hold them.

[weights]: ../docs/weights.md
[cfg]: ../config.yaml
[diag]: examples/diagnostics-request.example.yaml
[dimg]: ../diagnostics/README.md
