# Local console: YAML orders and immutable runs

The Handler's local console creates, imports, edits and runs orders on this
machine. The Hub selects and configures, then exports YAML. It does not execute
orders. Once the required data, images and weights are available locally, the
console can work without the Hub.

## Open it

The Handler serves the console at `http://127.0.0.1:8765/`, using
`handler.host` and `handler.port` from `config.yaml`. `Open NiB.<os>` starts the
Handler if needed and opens the page. `start` and `status` also print its URL.
The page and its fonts are embedded in the Handler; opening it needs neither
Docker nor Node.

On macOS, the launchd process survives closing Terminal. On Linux and Windows,
closing the Handler's background window stops it. Linux needs `xdg-open` to
open the browser; otherwise the opener prints the URL and exits non-zero.

## Create or import an order

Choose **Create order**, or import a `.yaml` or `.yml` file exported by the Hub.
ZIP package import and ZIP execution are no longer supported. Existing run
records remain visible as read-only history, with logs and results. Saved ZIP
files without runs are left on disk but are not listed as YAML orders.
Create a YAML order to start new runs from those settings.

The form uses `GET /v1/order-options` for available data sources, paired models,
verification methods, threshold bounds and units, region bounds, fixed geometry
and the sample limit. It exposes:

- Order ID, data source, event ID and UTC RFC3339 sample start times.
- Inference model selection and, for CMA data, the region centre coordinates.
- Read-only context frames, target frames, cadence, width and height.
- Optional verification models, methods, threshold levels and half-window sizes.
- Optional visualization models. Layers, display thresholds and playback remain
  controls in the existing diagnostics viewer.

Use **Validate draft** to check the configuration without saving, probing data
or running Docker. **Save defaults** creates an order or saves changes with its
current revision. A stale revision is rejected; the draft stays visible so it
can be compared with the reloaded order. **Export saved YAML** downloads the
saved configuration, including when the form contains unsaved edits.

**Check readiness** is a separate local action for the current draft. It checks
resources and data windows and may start a reader container. Individual checks
show their result and message. Editing the order or local settings invalidates
the result, including a response arriving after an edit. Every run start still
performs backend checks automatically; an earlier green result is not a bypass.

Saved YAML is an order definition, not a runtime package. Its object shape is:

```yaml
schema_version: nib.order.v1
order_id: example-order
data_id: <supported data ID>
event_id: <event ID>
sample_starts:
  - "2026-01-01T00:00:00Z"
models:
  - <paired model ID>
# Include region only for a data source that supports it:
# region: {center_lon: <longitude>, center_lat: <latitude>}
# Optional diagnostics, with model IDs drawn from models above:
# verification:
#   models: [<paired model ID>]
#   methods: [<supported method>]
#   levels: [<threshold in the data source's unit>]
#   half_windows: [<non-negative integer>]
# visualization:
#   models: [<paired model ID>]
```

The angle-bracket values are placeholders; use IDs and bounds from the options
endpoint. Order IDs must be safe local identifiers. Paths, images, shell
commands and Docker arguments are not order parameters. Fixed geometry comes
from the supported data contract and is not editable in YAML.

Omit `verification` or `visualization` to disable that diagnostic. Selecting a
model for inference does not implicitly enable diagnostics. Threshold-dependent
methods require explicit `levels`; FSS also requires integer `half_windows`
from 0 to 16. These fields are ignored when the selected methods do not use them.
Each diagnostic model list must be a subset of the inference models.

## Start and inspect runs

**Start new run** submits the saved revision and freezes its configuration for
that run. Saving defaults later never rewrites existing runs. Changes to data,
start times, region or inference models require a new run.

**Repeat inference** in run detail submits the historical run's frozen YAML with
the current local data folder and resource settings. It creates a new run and
does not change the saved defaults. It is available only for runs with a frozen
order, after their active inference finishes. If a response is lost, inspect
Runs before retrying because the request may already have created a run.

Independent runs of the same order may execute concurrently.
`runtime.max_concurrent_jobs` in `config.yaml` bounds execution (default `2`);
extra jobs remain queued and start in FIFO submission order. A repeated
idempotency key returns the existing run; a new submission identity creates
a separate run and directory. Execution is not serialized by a global lock.
Only one writer may work on a given run at a time, including diagnostic retries.

Run detail shows the backend stage, preflight checks and recorded execution
context (`data_path`, `catalog_path`, and `images`) alongside the frozen order.
Logs provide actual output; the console does not invent progress percentages.

Each saved-order submission attempt uses a unique `Idempotency-Key`. A retry after an error
reuses that key while the page remains open; another explicit start after a
successful response gets a new key. The console requires unsaved changes to be
saved or discarded before starting the selected order.

Selecting an order filters the run list to that order. Previous/next controls
request pages of 50, including history beyond the first 500 runs. **All orders**
clears the filter. Run detail shows the frozen order, request, revision,
Forecasts, sample records and diagnostic operation history. Logs retain their
container prefixes and structured output. Results open in the existing viewer.

The run's independent diagnostic form submits new verification and/or
visualization settings against saved Forecasts. It offers models with saved
Forecasts; the server checks availability across every sample in a batch. It cannot
change inference inputs or the order's saved defaults. Old history without a
frozen YAML order is read-only. The five-second display refresh preserves order
drafts and diagnostic selections.

Coverage is available in the diagnostics viewer. A static validation does not
check data coverage: **not probed** means no availability check has been made;
it does not mean data is missing. Missing data is an observed result of a probe
or run, and must not be inferred from an unprobed configuration.

CMA metadata preflight checks time windows; regional footprint and pixel
availability remain unprobed until ingestion. CMA event IDs must match the
first sample date in YYYYMMDD format.

## Docker and lifecycle

Opening the page, reading history, editing/importing/exporting YAML and static
validation need no Docker. Starting inference, running diagnostics and checking
new data through a reader container do need Docker. The console reports Docker
readiness and displays submission errors beside the relevant action.

`stop`, `restart` and uninstall do not require Docker to be reachable. The
Handler rejects shutdown while its local work is active. When Docker answers,
it also checks for active Handler-owned projects before stopping.

The console shows Handler and Docker status separately. **Restart Handler** and
**Stop Handler** ask for confirmation; a busy response (HTTP 409) leaves the
Handler running and displays the rejection. Restart reinitializes services in
the same process, retaining the session token unless its configured value
changes. The page tolerates downtime and confirms restart only after a different
`instance_id` reports `running`. After
60 seconds without confirmation it pauses and offers **Refresh** to try again.

After stop is accepted, automatic polling stops. A lost stop response also
pauses polling but reports that the outcome is unknown. Use the local launcher
to start Handler, then **Refresh** to reconnect. Drafts remain in the open tab;
they are not persisted across closing or reloading the page. Polling otherwise
runs every five seconds while the tab is visible.

## Local settings and resources

**This Handler** shows the version, origin, configuration file, instance start
time, and runtime paths. Check these before editing settings when several
installations exist. The expandable settings form saves the image source policy
(`official`, `mirror`, or `auto`), official registry and mirror, and an existing
local data folder associated with a supported data source. Paths remain local
and are not included in exported YAML. Failed saves retain edits; switching
data sources or reloading settings asks before discarding an unsaved path.

**Inspect resources** explicitly rechecks local resources. **Prepare / retry
selected** submits only supported data/model IDs from the options endpoint.
The Handler chooses trusted image and weight references; there is no arbitrary
image field. Errors and actual stages or byte counts appear for each resource,
without estimated percentages. During preparation the page reads cached status
while visible; it does not repeatedly request an expensive recheck.

A **prepared** status records the last successful preparation. It does not
claim that the current Docker daemon still has those resources; inspection and
each execution check them again. Missing diagnostics images are prepared when
opening results or starting diagnostics, using the configured image sources.
Starting the Handler alone does not inspect or download images; resource work
begins with inspection, preparation, or an execution that needs it.

## Triggers

The Handler owns trigger evaluation. A trigger references a saved YAML order
and creates a new run; it does not edit that order. Legacy package triggers
remain visible but cannot execute or resume. They can be paused or deleted.
The creation form lists only valid, writable saved orders.

Triggers can check for new data every configured interval, or run on an interval
or at a daily local time. A scheduled run can use the order's own times or the
newest available data. Window selection supports newest-only or all new windows,
subject to the configured batch limit and one event per batch. Reader-based
checks need Docker and the configured data folder.

The table retains last outcome, errors, next due time, missed runs and
**Run now**, **Pause / Resume**, and **Delete** controls. Triggers run only while
the Handler runs. Distinct scheduled points and new data windows can create
independent runs for the same trigger. Missed scheduled points are reported
rather than silently caught up.
Trigger state stays in `nib-triggers.json` beside `config.yaml` and
`nib-settings.json`; it is not part of the YAML order.
The trigger cursor records its data source and resets when an edited order
switches sources, so earlier dates from the new source are not skipped.

Behaviour worth knowing:

- Distinct due points or new data windows may run concurrently, up to
  `runtime.max_concurrent_jobs`. Extra runs queue in submission order.
  `stop` refuses while any job is queued or executing.
- A delayed scheduler tick fires only the latest due point. For a five-minute
  interval, a tick at minute 17 handles minute 15; the next point is minute 20.
  Older elapsed points are not replayed on subsequent ticks.
- Each fire uses the idempotency key
  `trigger:<id>:<data-id>:<start window>`, or
  `trigger:<id>:<data-id>:order:<scheduled point>` for the order's own window.
  The same key returns its existing run, including after a Handler restart.
- Scheduled points missed while the Handler was stopped are counted in
  `missed_count` rather than caught up. **Run now** is the manual catch-up.
- A failed fire records `last_error` and leaves the trigger enabled; the next
  point or poll tries again.

## Handler API

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Embedded console with injected session token; no-store. |
| `GET /console/fonts/{name}` | Embedded Inter fonts. |
| `GET /v1/status` | Handler and Docker status. |
| `POST /v1/control/restart` / `POST /v1/control/stop` | Confirmed lifecycle actions with the same-origin session token; busy returns 409. |
| `GET /v1/settings` / `PATCH /v1/settings` | Read settings; patch `{image_source, image_registry, image_mirror}` or `{data_id, data_root}`. |
| `GET /v1/resources?data_id=...` | Cached diagnostics/execution resource groups; add `refresh=true` for deliberate inspection. |
| `POST /v1/resources/prepare` | Prepare trusted resources selected by `{data_id, models: [...]}`. |
| `POST /v1/preflight` | Check `{order: object}` or `{yaml: string}`; returns `{ok, checks, execution_context}`. |
| `GET /v1/order-options` | Supported data/model pairs, methods, fixed fields, bounds and batch limit. |
| `GET /v1/orders` | `{orders, total}` including saved YAML and read-only history. |
| `POST /v1/orders` | Create/import from `{order: object}` or `{yaml: string}`. |
| `GET /v1/orders/{order_id}` | Saved detail, normalized order, YAML and revision. |
| `PATCH /v1/orders/{order_id}` | Save `{order: object, revision: current}`. |
| `POST /v1/orders/validate` | Validate `{order: object}` or `{yaml: string}` without saving or Docker. |
| `GET /v1/orders/{order_id}?download=1` | Download YAML using bearer authentication. |
| `POST /v1/orders/{order_id}/runs` | Start `{revision: current}` with an `Idempotency-Key` header. |
| `GET /v1/runs?offset=0&limit=50&order_id=...` | Paged run listing; the order filter is optional. |
| `GET /v1/runs/{id}` | Frozen order, revision, request, manifest fields and diagnostic operations. |
| `POST /v1/runs/{id}/rerun` | Repeat frozen YAML with current local paths; returns a new job. |
| `GET /v1/jobs/{id}/log` | Run log tail. |
| `GET /v1/runs/{id}/files/{entry}` | Existing diagnostics results and Coverage viewer. |
| `POST /v1/runs/{id}/diagnostics` | New operation with optional `verification` and `visualization` objects; at least one must be enabled. |
| `GET /v1/triggers` | Stored triggers and state. |
| `POST /v1/triggers` | Create a trigger referencing a saved YAML order via `package_id`. |
| `PATCH /v1/triggers/{id}` | Change conditions or enable/disable. |
| `DELETE /v1/triggers/{id}` | Delete a trigger; keep its runs. |
| `POST /v1/triggers/{id}/fire` | Fire an eligible trigger now. |

Saved-order detail includes `order`, `yaml`, `revision`, `order_id`,
`package_id` (equal to `order_id`), summary fields, `source: saved` and
`read_only: false`. History summaries have `read_only: true`; they are not
editable or executable order definitions. Diagnostic request objects use the
same fields as the corresponding YAML diagnostic objects.

The console expects status fields `instance_id`, `started_at`, `handler_state`,
and `active_jobs`, plus the existing Docker and configuration fields. Checks
use `{id, ok, message}`; run checks and execution context are in `run.job`.
Resource responses contain `diagnostics` and `execution` groups, each with
`status`, `message`, and `items`. The repeat action sends an `Idempotency-Key`,
but retry deduplication depends on the rerun endpoint honoring it.

The Handler remains loopback-only. Requests retain the existing Host and Origin
checks. The console keeps its injected bearer token in page memory and uses an
authenticated fetch plus a Blob download for YAML export. Server validation
remains authoritative for safe identifiers, supported parameters, revisions
and Forecast availability; browser fields cannot supply Docker arguments or
shell commands.
