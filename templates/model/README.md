# Model contribution template

Copy `adapter.py`, `Dockerfile`, `requirements.txt`, and this README into
`adapters/<data-id>/<model-id>/`. Move the template `declaration.json` to
`contracts/models/<data-id>/<model-id>.json`; that file is the single declaration
authority. Do not keep a second declaration in the adapter directory. Replace
the example IDs and Dockerfile paths, then implement `prepare` and `predict`.
Both methods raise `NotImplementedError` until implemented. The template has no
working backend or fabricated forecast.

`DeclaredModelAdapter.declaration_path` points to that contract file. Its
inherited `capabilities()`, `weights()`, `input_requirements()`, and
`output_specification()` queries read it without loading a backend.
`validate_input(observations)` enforces the declared physical input contract.
Existing `contracts/models/sevir/*.json` files are concrete schema examples.
Register the pair in the catalog separately.

The public boundary is:

```python
Observations(source, variable_id, units, values, times, grid, mask)
Forecast(variable_id, units, values, times, grid, mask, init_time, lead_minutes)

adapter.validate_input(observations)
prepared = adapter.prepare(observations)
forecast = adapter.predict(prepared, weights_path="/weights/model.bin")
```

Values are physical `float32` arrays shaped `(time, y, x)`, without a batch axis.
Times must be explicit UTC. Supply a grid with `crs`, increasing `x` and `y`
coordinates, and `axis_units`, plus a boolean mask matching the values (`True`
means missing). Forecast valid times equal initialization plus the declared
positive lead minutes. `prepare` may return any backend-private representation,
including tensors, normalized values, or batches. `predict` must undo those
conversions before returning a `Forecast` and preserve its physical metadata.
Use `utils.model_contract.validate_forecast` to check the result.

Choose your own backend and pin its dependencies in `requirements.txt`. The
example only includes the shared runtime's NumPy dependency. Declare a real
weight filename and SHA256, and use an immutable upstream revision when providing
a download URL. Never fetch weights during inference. A missing weight or hash
mismatch must fail offline; verify it in `predict` too for direct adapter callers.
The template's null weight metadata deliberately cannot pass release validation.

Build from the repository root. The image must contain its declaration, shared
interfaces/utilities, and the module `containers.models.run`. Set `NIB_MODEL` and
`NIB_ADAPTER_MODULE` in the image. The runtime invocation is:

```sh
python -m containers.models.run --run /workspace/<id> --weights /weights/model.bin
```

## Manual release prerequisites

Release validation is maintainer tooling, not a host inference installation.
Use a trusted local machine or a separately provisioned self-hosted Linux runner
with labels `self-hosted`, `linux`, `nib-model-release`. This change does not
provision or start a runner. Dispatch only reviewed code on that runner.

Prepare these before executing a release:

- A Docker daemon and Buildx capable of building and running the ONE selected
  platform, natively or with explicitly provisioned emulation. No automatic
  all-model or all-platform inference runs are provided.
- Python 3.12 with the repository's pinned dependencies from
  `tests/requirements-dev.lock.txt` and
  `diagnostics/verification/requirements-dev.lock.txt`. Set
  `PYTHONPATH` to the checkout and `diagnostics/verification`; the workflow does
  this and accepts `NIB_RELEASE_PYTHON` as a repository variable selecting the
  preinstalled interpreter. Missing dependencies fail the release.
- All contributed image source files tracked in the reviewed checkout. The script
  stages tracked source under `adapters`, `contracts/models`, `containers/models`,
  `interfaces`, and `utils`, restricted to Python, JSON, text dependency files,
  and Dockerfiles. A small explicit list also includes the new shared
  `interfaces/fields.py` and `utils/model_contract.py` during integration.
  Do not store observations in these directories. If a backend needs other
  source assets, review and extend this allowlist explicitly.
- A fresh, explicitly prepared ingestion run, named by its manifest ID, with
  context and target NPZ fields and no forecasts or failed status. Truth must
  cover every output lead, with compatible variables, units, and grid. Use a
  dedicated release run: the candidate writes its forecast there and failed
  runs remain inspectable. Prepare a new run for retries; nothing is deleted.
- A local weight file matching the declaration's SHA256. The script verifies a
  private copy, exposes it read-only at `/weights`, and supplies a `weights`
  Buildx context for existing Dockerfiles that embed that verified file.
- The public `utils.stored_fields.StoredFields` reader must expose matching
  physical forecast/truth fields for every output lead, including grid, times,
  variable, units, and jointly valid cells. This check is mandatory for every
  data/model pair. CMA Radar additionally passes the four-method
  `diagnostics.run_source.RunSource` handoff. Other datasets explicitly report
  that the diagnostic handoff is unsupported; their model and field checks
  still run and must pass.
- An explicit GHCR candidate tag including the data pair, revision and platform
  to avoid overwriting another pair or architecture. Authenticate locally with
  Docker before requesting `--push`; the manual workflow uses its GHCR token.

Keep CMA real samples, prepared runs, weights, and release logs outside the
checkout on the private machine. Never upload CMA data to GitHub, caches,
artifacts, or images. The model workflow keeps detailed build/inference/check
logs in the private run's `release.log` and uploads no run artifacts. It sends
only staged source and the verified weight copy to Docker, never the run or
sample directories. Use a trusted local builder, not a remote build service.

For example, after preparing the private paths and environment:

```sh
export PYTHONPATH="$PWD:$PWD/diagnostics/verification"
python scripts/validate-model-release.py \
  --model cma_radar/nowcastnet --platform linux/amd64 \
  --image ghcr.io/your-owner/nib-nowcastnet:cma-review-revision-amd64 \
  --run /private/nib-runs/release-check \
  --weights /private/nib-weights/NowcastNet.onnx
```

Add `--push` to build, validate, and publish in the same invocation. Without it,
the script validates only. In both cases it builds with `--load`, runs the exact
local image digest with `--network none --read-only --tmpfs /tmp`, validates
saved forecasts against the selected declaration, and reads each forecast/truth
pair through `StoredFields`. CMA additionally exercises all four diagnostic
handoff methods. Missing inputs, bad weights, stale outputs, incompatible fields,
failed inference, or reader failures stop before push. Mandatory checks cannot be
skipped, and routine PR CI does not run model inference.

The manual `publish images` workflow selects `model`, `diagnostics`, or
`ingestion` and one platform. Support-image publication remains separately
selectable. It does not merge architecture tags or update a shared multi-platform
runtime tag. Model Dockerfiles and shared runtime/reader integration are owned
outside this template; ensure their module entry point and shipped declarations
match the contract before dispatching a release.
