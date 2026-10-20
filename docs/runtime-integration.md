# SEVIR local runtime integration (#14)

The runtime uses stored-run contract `nib.run.v1`, defined in atlas commit
`e9ecd84f217eef9658d433133bc588a3128d05e0`. Users run a prebuilt Go handler; Python stays in Docker. The Hub is a
remote provisioning site, and its browser talks directly to the user's loopback
handler. The Hub server never proxies the user's localhost.

## Prepare resources

Initialize the atlas submodule and build images from the repository root:

```sh
git submodule update --init --recursive
docker build -f adapters/sevir/Dockerfile -t nib-ingestion:dev .
docker build -f adapters/sevir/wadepre/Dockerfile -t nib-wadepre:dev .
docker build -f adapters/sevir/nowcastnet/Dockerfile -t nib-nowcastnet:dev .
docker build -f adapters/sevir/exprecast/Dockerfile -t nib-exprecast:dev .
docker build -f diagnostics/Dockerfile -t nib-diagnostics:automatic-flow .
```

`paths.resources` is the root of the packaged runtime tree. Each supported data
source declares `runtime.yaml` under `adapters/<data-id>/`, and each model
declares one under its data directory; the Handler reads those manifests and
never hard-codes a dataset, data file, catalog path, or image tag in
`config.yaml`. See [runtime resources](runtime-resources.md) for the schema and
the release layout.

The selected data source's folder is saved per data id in `nib-settings.json`
next to `config.yaml` as `{"data_roots": {"<data-id>": "<folder>"}}`; that file
is gitignored, survives a Handler restart, and `NIB_DATA_ROOT` remains a fallback
for older single-root settings. The folder must contain the paths declared by
that data source's manifest; no path is invented when it is missing. Weights
stay under `artifacts/<data-id>/<model-id>/...` and are checked against the model
manifest's SHA256 before offline inference. A missing weight is copied from
`paths.weight_source` (a folder laid out like `artifacts/`) and verified; the
Handler does not download it over the network. Image layers are expected to be
present in Docker's local cache: `GET /v1/resources?data_id=<id>` reports each
selected image as ready or missing, and `POST /v1/resources/prepare` copies
missing weights and re-checks the selected images. Compose definitions use
`pull_policy: never` so execution never hides a download.
The initial reader requires six context and twenty target 128x128 frames. It
rejects event/time ambiguity and events with missing pixels that the processed
file can no longer mask reliably. Sample listings show this limitation.

Maps require an explicitly prepared WMO cache under `artifacts/basemap/wmo`.
Acquire it before execution with the existing visualization acquisition script;
runtime containers never download basemaps or weights. Metrics-only jobs do not
need a basemap. Scientific VIL units are `raw`; they are not dBZ or rain rate.

## Connection and execution

`config.yaml` controls loopback addresses, paths (including the runtime resources
root), optional Docker context, and explicitly allowed Hub origins. Images and
data/model mounts come from the runtime manifests. Register/start the platform launcher once,
then use Hub's connection button and confirm its browser `nib://` application
prompt if needed. The token is transferred over trusted loopback HTTP, remains
in browser memory, and is never printed or manually copied. Browsers may also
require local-network permission when a remote site accesses loopback.
See [lifecycle and connection](handler-lifecycle.md) for background startup,
stop/restart, configuration ownership, trusted deployments and limitations.

A successful CORS preflight is not evidence that permission was granted.

Choose a data product and an explicit event/window, then models and optional
evaluation or visualization in the existing Hub catalog/Checkout. Run locally from Checkout (or an existing frozen order): the browser uploads the
actual ZIP and package checksum once, then observes the job in Runs. Handler
executes the selected diagnostics automatically after inference, independently
of the browser. See [automatic diagnostics](automatic-diagnostics.md) for exact
operation/report identity, partial results and crash recovery. The handler
checks bounded ZIP entries, paths, checksums, supported product versions and
frozen data/model/diagnostic bindings. Template bytes are never executed.

The handler writes controlled Compose definitions. Each stage is offline, has a
read-only root, tmpfs `/tmp`, read-only input/weight mounts, and runs as the host
UID on Unix. Algorithms have distinct images. All data and generated outputs
remain in configured `runs/<unique-id>/`; no outputs live in Hub or atlas.

Execution is serialized in this first integration. Jobs record queued/running/
succeeded/failed/partial states with a stage, request identity and logs. A repeated
idempotency key returns the same run, including after restart. A new explicit
submission may reuse the same sample/model run only after its current execution
finishes; an active run cannot be replaced by another idempotency key.
Restarted unfinished jobs are marked interrupted,
not silently resumed. Successful Forecasts survive diagnostic failure.

## Results

The manifest records explicit sample identity, context start, init time, valid
times and actual leads, plus field paths/digests/grid/units/masks. Forecast init is
the last context frame; targets are +5 through +100 minutes. The storage reader
loads each verified cube once for all requested leads.

Verification compares the matching native LAEA grid without display resampling.
Geographic default behavior stays compatible; projected regridding or different
CRSs are refused. Each diagnostic attempt has a fresh ID and retains its config,
provenance, SQLite or rendering manifest, summary, logs and webpage. Adding a new
threshold supplements quantities from stored Forecasts; it never invokes ONNX.
Existing threshold queries use SQLite only.

Handler starts one diagnostics result viewer on a loopback address from
`config.yaml`; the viewer serves Visualization and Metrics in one document and
Handler proxies it through the existing result listener. There is no second
metrics container and no iframe. Runs is an independent Hub destination, not
merely a link from an order. Its diagnostic retry starts a new attempt from
saved Forecasts, never overwrites previous attempts and never reruns models.

## Validation status

This feature is still in integration. See the actual verification record and PR
for completed container/browser checks; library tests or successful image builds
alone do not prove a real inference run. Public HTTPS Hub deployment, other host
platform execution and unavailable input cases must be reported separately.

## Batch sample windows

A package may put `sample_starts` on its single data item. The Handler keeps
the one-window layout when exactly one start is present and uses a batch run
with `samples/<index>` children when more are selected. Children are ingested
and inferred independently; only verification aggregates their init times.
The first selected child is used for the temporary single-case Visualization.
