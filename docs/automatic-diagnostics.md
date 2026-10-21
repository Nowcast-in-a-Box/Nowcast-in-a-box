# Automatic diagnostics and local result views (#28)

After every selected model finishes, Handler executes the diagnostics recorded
in the frozen package. This occurs in the background, under the same serialized
execution resource as inference. No diagnostics are selected implicitly; an
inference-only job finishes with `succeeded / finished`.

An initial automatic diagnostic and an explicit retry use the same Compose
preparation/execution path. Each operation has its own ID, request, status,
container log, and exact result report. All selected verification and visualization
models come from the frozen bindings. Retry consumes saved Forecasts and does
not call ingestion or inference.

`job.json` names the initial `diagnostic_operation_id` and `report_entry`.
`GET /v1/runs/{id}` additionally exposes `operation`, the latest explicit/automatic
diagnostic operation. A durable pointer selects it; random ID suffix order does
not define “latest.” A new inference request supersedes old diagnostics until its
own operation exists. The UI does not open a previous success as this request's
result. Legacy `ready` jobs retain their old registered report links.

The diagnostics container accepts `operation_id` in its internal version-2
request. The report records this ID and the exact diagnostic attempt IDs. Handler
only accepts a matching operation report after checking the manifest schema/run
identity and that the report has both a registration path and an entry.
Upgrade/rebuild the diagnostics image together with Handler. An old image that
does not register a matching report results in an actionable partial job, rather
than an invented success.

A diagnostic failure leaves stored Forecasts available. The run is `partial`,
with the operation's failure and log visible; other successful diagnostic content
remains available in the unified result. The single diagnostics result viewer
serves **Visualization** and **Metrics** in one document with real tabs; the
Metrics view is the merged verification page and its interactive query, not a
second static report. Viewing or switching views is read-only. Unselected,
partially available and failed diagnostics have different messages. Historical
attempts/reports and unselected forecast registrations survive an explicit
inference rerun of the same sample/model configuration.

The Visualization view displays existing images/GIFs/SVGs from the diagnostic
packages. Per-model labels remain attached to each image and the metrics' stored
output includes all selected model results.

## Crash and cleanup recovery

An instance mismatch is enough to display an interrupted operation, but it is
not proof its Docker container stopped. Before admitting a new job/retry, at the
queued-job execution handoff, and before ordinary stop, Handler checks the owned
historical Compose projects against Docker's actual states. Historical polling
or a terminal failure record does not erase this check. Created, running, paused
or restarting projects block new work. Unavailable/unreadable Docker state also
blocks it with an actionable error. A cleanup command's nonzero exit is an error,
including when no Go process-spawn error occurred.

Handler does not force-kill those projects, silently retry interrupted work or
restart an old model. Inspect the named project in Docker and wait for it to
finish, then explicitly retry from saved Forecasts if desired. Stop refuses active
work; this is not a full cancellation system. The source trees and generated run
records must remain available for inspection.

## Browser workflow

Hub's small execution selector persists Local/Cloud. The first Local visit
connects Handler before showing the Catalog. A later disconnection preserves the
draft and observation destination while blocking new Starts. Cloud says execution
is unavailable and never submits to the local service.

Checkout shares its local directory/sample component with Runs. Validate checks
the configuration; local preflight approval is bound to the connection's instance,
configuration, draft and local environment revision. A directory/environment
refresh invalidates it. Start freezes one package, submits using that package's
stable idempotency key and navigates to Runs. Ambiguous network retry reuses the
same package even after revalidation. Session/mode/draft/environment changes and
unmount cancel unsubmitted work through download, FileReader and the POST boundary.
A job already accepted by Handler continues independently.

Orders uses the same package identity and Runs observation. Advanced **Freeze
package** remains available as a clearly named configuration-download action; it
does not execute anything. Runs' report links only navigate to the local result.
A separate explicit retry button computes diagnostics from stored Forecasts.
