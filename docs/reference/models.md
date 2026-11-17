# Model contribution contract

Issue #68 defines the shared boundary below. Model declarations live at
`contracts/models/<data>/<model>.json` and use `schema: "nib.model.v1"`.

The declaration records identity, version, paired data source, task, status,
weights, and the physical `input` and `output` requirements. The adapter's query
methods read that declaration; backend tensor shapes are private implementation
details. See [the contribution guide](../guides/model-contribution.md) for files
and checks required for a new model.

## Lifecycle

The shared interface is [ModelAdapter](../../interfaces/model.py):

- `capabilities()`: identity, version, paired data source, task and status.
- `weights()`: weight provenance, filename, checksum and artifact location.
- `input_requirements()`: variable, units, context length, cadence, spatial
  shape, dtype and missing-value policy.
- `output_specification()`: variable, units, lead minutes, spatial shape,
  dtype, missing-value policy and grid policy.
- `validate_input(observations)`: reject observations that do not meet those
  requirements before backend preparation.
- `prepare(observations)`: return any backend-private type needed by inference.
- `predict(prepared, *, weights_path)`: use already available local weights
  and return a physical `Forecast`.

The runtime owns the sequence:

```text
read → select exact recent history → validate_input → prepare → predict
     → validate forecast → save
```

History selection uses the declaration's context length and cadence. It must end
at the requested initialization time. Missing or incompatible inputs fail;
neither the runtime nor the model may silently pad, resize or interpolate them.
The reader must deliver compliant observations. A documented ingestion grid
transformation is separate from silently repairing input at the model boundary.

Backend choice stays inside the model's Docker image. ONNX Runtime, PyTorch or
another backend can implement the same interface. Preparation may normalize,
reorder axes or create backend tensors, but it must preserve the declared
physical meaning. There is no shared backend tensor format or ONNX-only base.

## Physical fields

[Observations and Forecast](../../interfaces/fields.py) exchange physical
`float32` arrays in `(time, y, x)` order, without batch or channel axes.
Each field has a variable identity, real units, explicit UTC timestamps, a mask,
and a grid containing its CRS, x/y coordinates and `axis_units`. Do not invent
coordinates, units, valid pixels or timestamps to satisfy a shape check.

A forecast also carries `init_time` and explicit `lead_minutes`. Its valid
timestamps must agree with initialization plus those leads. Output validation
checks the declaration and the forecast's own metadata. A model that declares
an explicit output grid must provide that grid; do not relabel it as the input
grid or copy observation metadata over it.

Stored runs write `nib.run.v2` and retain read compatibility with
`nib.run.v1`. Forecasts retain their own grid, variable, units, timestamps and
mask. Verification reads these physical fields, never the tensors returned by
`prepare`. Incompatible truth/forecast grids or units fail without implicit
resampling. See [the verification interface](interfaces-we-consume.md).

## Pairings

| Data source | Model | Declaration | Adapter notes |
| --- | --- | --- | --- |
| SEVIR VIL | NowcastNet | [JSON](../../contracts/models/sevir/nowcastnet.json) | [README](../../adapters/sevir/nowcastnet/README.md) |
| SEVIR VIL | exPreCast | [JSON](../../contracts/models/sevir/exprecast.json) | [README](../../adapters/sevir/exprecast/README.md) |
| SEVIR VIL | WADEPre | [JSON](../../contracts/models/sevir/wadepre.json) | [README](../../adapters/sevir/wadepre/README.md) |
| CMA Radar | NowcastNet demo | [JSON](../../contracts/models/cma_radar/nowcastnet.json) | [README](../../adapters/cma_radar/nowcastnet/README.md) |

Atlas products are registered manually. A local declaration or adapter does not
automatically export or publish a Hub product. Weight and image preparation
follow the [actual Handler behavior](../guides/weights.md).
