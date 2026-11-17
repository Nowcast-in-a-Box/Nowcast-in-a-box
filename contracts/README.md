# Contracts

Schemas shared by the handler (preflight) and by code that runs inside
Docker. This directory is documentation and schema, not a Python package
and not something end users install.

## Order configuration (`nib.order.v1` YAML)

Hub exports a single YAML document. The local Handler console imports, creates,
edits and exports it. The Handler validates the same definition before saving,
preflight and execution. See [the example](examples/order.example.yaml) and
[the local console](../docs/local-console.md).

Required fields are `schema_version`, `order_id`, `data_id`, `event_id`,
`sample_starts` and `models`. The schema version is `nib.order.v1`.
Order IDs are lowercase portable filenames of 1–128 characters. Models must be
registered for the selected data source. Sample starts contain 1–32 distinct
UTC RFC3339 instants, also limited by the Handler's configured batch limit.
The Handler canonicalizes timestamps to `Z` and derives the context start from
the first sample. SEVIR samples share one event; CMA batch dates follow each
sample's start.

Optional sections:

- `region`: CMA center longitude and latitude, supplied together. Bounds are
  76–132°E and 15–51°N; the default is 118°E, 32°N. SEVIR does not accept a region.
- `verification`: nonempty model subset and methods, with `levels` for categorical
  or spatial metrics and `half_windows` for FSS. Thresholds use the data source's
  native units. Each required list has at most 16 distinct values. FSS windows
  are integer half-widths from 0 to 16 cells. Unused lists are removed.
- `visualization`: a nonempty model subset, independent of verification models.

Omitting a diagnostic section disables it. Unknown fields, duplicate YAML keys,
multiple documents and documents larger than 1 MiB are rejected. Frame counts,
grid, cadence and forecast horizon are fixed runtime capabilities, not editable
order fields. Machine paths, Docker arguments, images and executable code never
come from the order. The repository-root [`config.yaml`][cfg] remains the separate
Handler host configuration.

The file format is YAML; HTTP uses JSON envelopes containing either `yaml` text
or an `order` object. Canonical content determines the revision. Updates require
the revision read by the editor and return a conflict if another save won first.
Each admitted run saves its effective `order.yaml` plus the source revision in
`job.json`; trigger-selected windows are included in that run's execution hash.
Later order edits never change those snapshots.

ZIP import and execution endpoints are no longer supported. Historical runs
remain readable; a history-only order cannot start until a new YAML order is
created. Existing ZIP files and their results are not deleted.

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
