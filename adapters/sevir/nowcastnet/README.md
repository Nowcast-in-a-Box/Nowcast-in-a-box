# NowcastNet

This pairing follows the [model contract](../../../docs/reference/models.md).
Its [declaration](../../../contracts/models/sevir/nowcastnet.json) supplies identity,
physical requirements and weight provenance.

- Input: five physical float32 frames of SEVIR VIL in raw units at five-minute cadence,
  each 128 × 128, with no missing pixels. Runtime selects exact recent history.
- Output: twenty physical frames at 5 through 100 minutes, on the input grid,
  with UTC timestamps, initialization time, lead minutes and mask.
- Backend: the packaged ONNX model runs inside Docker. Preparation is private;
  verification reads physical fields, never backend tensors.
- Weights: NowcastNet.onnx, with SHA-256 and provenance in the declaration.
  Stage the external weight locally before inference.
  See [weight preparation](../../../docs/guides/weights.md).

Implement declaration queries, validate_input(observations), prepare(observations)
and predict(prepared, *, weights_path). Incompatible inputs fail without implicit
padding, resizing or interpolation. Runtime validates the forecast before saving
nib.run.v2; v1 remains readable.

The declared raw VIL field is distinct from normalized backend input. Do not
label prepared tensors as physical forecasts.

Follow the [contribution checks](../../../docs/guides/model-contribution.md#routine-checks):
routine CI uses small synthetic function tests. Real inference is reserved for
a named release candidate/platform, runs with network none, and fails when
required inputs are missing. There are no accuracy gates.
