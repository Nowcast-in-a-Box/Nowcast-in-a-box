# Contribute a model

Start with an assigned issue and the [repository contribution rules](../../CONTRIBUTING.md).
Use the [model contract](../reference/models.md) as the shared boundary. Model
code and its backend dependencies run inside Docker, not in the Go Handler.

## Required files

For a model paired with an existing data reader, provide:

| Path | Purpose |
| --- | --- |
| `atlas/contracts/models/<data>/<model>.json` | `nib.model.v1` declaration: identity, input/output requirements and weight provenance. |
| `adapters/<data>/<model>/adapter.py` | Contract queries, validation, preparation, and prediction. |
| `adapters/<data>/<model>/__init__.py` | Package entry following neighboring adapters. |
| `adapters/<data>/<model>/requirements.txt` | Dependencies for the chosen backend inside the image. |
| `adapters/<data>/<model>/Dockerfile` | Reproducible image with its shared runner, adapter, declaration and dependencies. |
| `adapters/<data>/<model>/runtime.yaml` | Handler image, command, mounts, product bindings and weight mode. |
| `adapters/<data>/<model>/README.md` | Input/output meaning, backend, weights, limitations, and test instructions. |
| `tests/adapters/model/` | Small synthetic function tests for the model boundary. |

If adding a reader, also provide its data declaration, source, dependencies,
Dockerfile, runtime manifest, and reader tests. See
[runtime resources](../reference/runtime-resources.md).

Use the model starter under `docs/templates/model/` and replace all example identity
and requirements. Complete the required files listed above for the pairing.
Atlas registration is a separate manual maintainer step; no automatic export is
part of this contribution contract.

## Implement the lifecycle

1. Declare the required physical variable, units, dtype, spatial shape, cadence,
   exact context length, and missing-value policy. Declare physical output leads
   and grid policy separately. Query methods must agree with the JSON.
2. Accept `Observations` from the runtime. The runtime selects exactly the
   recent history declared by the model, then calls `validate_input`.
   Reject incompatible shape, cadence, units, grid or missing data. Do not pad,
   resize or interpolate an incompatible input into acceptance.
3. Implement `prepare(observations)`. Its return type is private to the model:
   normalization and backend tensor construction belong here.
4. Implement `predict(prepared, *, weights_path)` using the chosen backend
   inside Docker. It must not fetch weights or data during inference.
5. Return a physical `Forecast` with `float32 (time, y, x)` values, UTC
   timestamps, initialization time, lead minutes, true units, mask and its own
   CRS/x/y/axis units. Runtime validates this result before saving `nib.run.v2`.
   Existing `nib.run.v1` records remain readable.

A backend's batch/channel axes and normalized tensors never become verification
inputs. A new backend does not require changing the shared field contract.

## Routine checks

Routine CI uses lightweight synthetic function tests. Test declaration/query
agreement, exact history selection, valid preparation, invalid-input rejection,
and physical output metadata. Use small arrays or a backend stub for the
lifecycle; routine checks must not require real observations, weights, downloads
or real model inference. Include a stored-field round trip and v1-read coverage
when changing persistence. Follow existing tests rather than duplicating the
runtime suite for every model.

Use `scripts/run-tests.sh` as the repository test entry; its header describes
the contributor environment. Run `ruff format .` and `ruff check .` for Python edits.
Record the checks actually run in the PR. Do not claim forecast accuracy from
function tests or from a successful container build.

## Release-candidate inference

Real inference is a separate acceptance check for a **named release candidate
and target platform**. Record the candidate/version, OS and architecture, image
and weight identities, input identity, command, output validation and outcome.
Run with `--network none` and read-only inputs/weights. Missing required data,
weights, images or platform support is **FAIL**, not a skip or a passing gate.
Retain logs and failed runs for inspection without committing private inputs.

Check the lifecycle and declared physical output, including timestamps, grid,
mask, and checksum. There are no accuracy gates. Metrics may describe a run but
do not certify model skill. The CMA pairing is explicitly a cross-domain demo.
See [the manual runbook](local-acceptance-runbook.md) for a full local workflow.
