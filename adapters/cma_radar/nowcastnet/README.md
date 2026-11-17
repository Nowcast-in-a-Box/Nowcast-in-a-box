# CMA Radar NowcastNet demo

This pairing follows the [model contract](../../../docs/reference/models.md).
Its [declaration](../../../contracts/models/cma_radar/nowcastnet.json) supplies identity,
physical requirements and weight provenance.

- Input: five physical float32 frames of CREF in dBZ at six-minute cadence,
  each 128 × 128, with no missing pixels. Runtime selects exact recent history.
- Output: twenty physical frames at 6 through 120 minutes, on the input grid,
  with UTC timestamps, initialization time, lead minutes and mask.
- Backend: the packaged ONNX model runs inside Docker. Preparation is private;
  verification reads physical fields, never backend tensors.
- Weights: NowcastNet.onnx, with SHA-256 and provenance in the declaration.
  The demo image embeds the weight.
  See [weight preparation](../../../docs/guides/weights.md).

Implement declaration queries, validate_input(observations), prepare(observations)
and predict(prepared, *, weights_path). Incompatible inputs fail without implicit
padding, resizing or interpolation. Runtime validates the forecast before saving
nib.run.v2; v1 remains readable.

This is a cross-domain demonstration, not evidence of CMA forecast skill. See
[the demo guide](../../../docs/guides/cma-demo.md) for ingestion and limitations.

Follow the [contribution checks](../../../docs/guides/model-contribution.md#routine-checks):
routine CI uses small synthetic function tests. Real inference is reserved for
a named release candidate/platform, runs with network none, and fails when
required inputs are missing. There are no accuracy gates.
