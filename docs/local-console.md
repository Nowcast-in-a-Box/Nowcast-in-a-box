# Local console: offline orders and runs

Some machines reach the Hub only briefly. The local console is the page those
machines use afterwards, offline: it lists order packages on this machine,
starts them, and watches run state. Order generation, catalog, and checkout
stay on the Hub; this page never edits an order.

## Open it

The Handler serves the console itself at `http://127.0.0.1:8765/` (the
`handler.host`/`handler.port` from `config.yaml`; no new port). Each release
zip ships an `Open NiB.<os>` file next to `Start NiB.<os>` that starts the
Handler if needed and opens the page in the default browser. `start` and
`status` also print a `console <url>` line.

Platform limits of `Open NiB`:

- macOS uses the launchd start; the Handler survives closing Terminal, as with
  `Start NiB.command`.
- Linux and Windows start the Handler in a background window when it is not
  running. Closing that window stops it again — the same behavior as closing
  the `Start NiB` terminal. Use `Start NiB.<os>` for a Handler you keep.
- Linux requires `xdg-open`; without it the script prints the URL and exits
  non-zero. It never starts browsers from the Handler itself.

The page is compiled into the Handler binary (`handler/console_page.html`,
embedded with `go:embed`), so it exists on every platform the Handler runs on
and needs neither Docker nor Node.

## Where orders come from

Orders are generated on the Hub. To use one offline, get its package ZIP onto
this machine while you still have the Hub:

- Download the package in Hub Checkout, then import the ZIP on the console
  page (`Import order package`). `POST /v1/orders` stores it under
  `paths.orders` (`orders/<package_id>.zip`, default `orders/`).
- Or start it once from the Hub. Every run keeps its submitted
  `runs/<run-id>/package.zip`, and the console lists those packages too.

An order that never reached this machine does not appear offline. The console
is a view of local inventory, not a copy of the Hub's order store.

## What needs Docker

Starting a run is the only operation that needs Docker. Everything else works
without it:

| Action | Docker |
| --- | --- |
| Open the page, list orders, import a ZIP | no |
| `stop`, `restart`, `uninstall`, SIGTERM | no |
| Start / Run again | yes |

A Docker daemon that is not running cannot hold containers, so it cannot be
hiding an orphan from a previous run. `stop` therefore ignores an unreachable
daemon and only refuses when Docker answers and still reports a Handler-owned
project as running.

When Docker is not running, `POST /v1/orders/{package_id}/runs` and
`POST /v1/jobs` answer `503 Docker is not running; start Docker and retry`
before creating a run, and the console shows that message next to the orders
instead of creating a run that fails immediately. The Hub receives the same
message as its submit error.

## What the page does

- Status: handler liveness and the Docker preflight from `GET /v1/status`.
  When Docker is not ready the page says so and refuses to start a run.
- Orders: `GET /v1/orders` lists saved packages plus packages of past runs
  (saved copies win; a package with several runs shows the newest run as its
  source). A package that fails validation shows the reason and cannot start.
- Start / Run again: `POST /v1/orders/{package_id}/runs` reads the saved ZIP
  and goes through the exact admission of `POST /v1/jobs` (checksums, frozen
  bindings, idempotency key, single active run per package). The first start
  uses idempotency key `package:<package_id>` — the same key the Hub uses —
  so an order already started from the Hub returns its existing run instead of
  silently starting a second one. `Run again` uses a fresh `rerun:` key and
  creates a new run.
- Runs: state, stage, errors, and the log tail via the existing
  `GET /v1/runs` and `GET /v1/jobs/{id}/log`. Forecast and verification
  results open in the existing diagnostics pages
  (`GET /v1/runs/{id}/files/{entry}`); the console does not redraw them.

The page refreshes every five seconds while it is open.

## Triggers

A trigger re-runs one saved order on its own. The Handler owns this: it is the
only process that can tell whether new data landed or the clock passed. The Hub
never evaluates a trigger, and a trigger never edits an order — it only
replaces the start window of the package it points at.

Two conditions:

- **New data arrives**: every *N* minutes the Handler asks the data reader for
  the windows it declares (`--list-samples`, the same call the sample endpoint
  uses) and runs the ones it has not run yet. Polling needs Docker and the
  configured data folder for that data source, because the answer comes from the
  reader container, not from a file timestamp.
- **On a schedule**: every *N* minutes, or every day at a local `HH:MM`. The
  start window is either the newest ready data at that moment, or the time
  frozen in the order itself (that is how you re-run one fixed window).

Each trigger also chooses how many windows one fire covers: **newest only**, or
**all new windows** in one batch run (bounded by `runtime.max_batch_samples`; a
batch carries one event id, so windows from another event are reported as
skipped instead of run).

State lives in `nib-triggers.json` next to `config.yaml`, beside
`nib-settings.json`. It is not part of an order and not part of `config.yaml`,
because it changes while the Handler runs.

Behaviour worth knowing:

- A trigger that already has a queued or running job skips its turn instead of
  queueing another run; the next tick tries again. `stop` therefore never waits
  on a trigger-built queue.
- Each fire uses the idempotency key `trigger:<id>:<start window>` (or
  `trigger:<id>:order:<scheduled point>` for the order's own window), so the
  same window never runs twice, not even across a Handler restart.
- A scheduled point that passed while the Handler was not running is **not**
  caught up. It is counted in the trigger's `missed_count` and shown in the
  console ("missed 2 scheduled runs while the Handler was stopped"). `Run now`
  is the manual catch-up.
- A failed fire (Docker down, data folder missing, order package deleted by
  `--purge`) records `last_error` and leaves the trigger enabled; the next point
  or poll tries again.
- Triggers only fire while the Handler runs. On Linux and Windows that is the
  same background window as `Start NiB`.

The console lists each trigger with its last outcome, next due time, missed
count, and `Run now` / `Pause` / `Delete`. The API:

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/triggers` | Stored triggers plus the firing-loop interval. |
| `POST /v1/triggers` | Create one: `package_id`, `kind`, `poll_minutes` or `interval_minutes`/`daily_at`, `window_mode`, `start_source`. |
| `PATCH /v1/triggers/{id}` | Change the condition or `enabled`. The order is fixed; delete and re-create to point elsewhere. |
| `DELETE /v1/triggers/{id}` | Remove it. Runs it already started stay. |
| `POST /v1/triggers/{id}/fire` | Fire now, regardless of the condition. The outcome is in `last_outcome` / `last_error`. |

## Security shape

- Everything stays loopback-only; non-loopback Host headers are refused as
  before, and the write endpoints keep requiring the session Bearer token.
- The token is injected only into the `Cache-Control: no-store` page response
  and lives in page memory; it is not logged or printed.
- The same-origin page is allowed by the existing "Origin equals request
  host" rule; the console is not added to `runtime.allowed_origins`, and
  `/v1/pair` remains Hub-only.
- Saved file names allow only letters, digits, dot, underscore and dash (no
  path separators), reject Windows-reserved stems and trailing dots, and are
  checked to stay inside `paths.orders`. Package contents go through the same
  `ParseRuntimePackage` validation as a Hub upload, including the 8 MiB
  bound; nothing from the browser reaches Docker arguments or a shell.

## Handler API summary

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Console page (no-store, token injected). |
| `GET /console/fonts/{name}` | Embedded Inter glyphs for the page (loopback host check, ETag). |
| `GET /v1/orders` | Saved packages plus packages of past runs. |
| `POST /v1/orders` | Save `{package, sha256}` (same body as `POST /v1/jobs`). Idempotent per content; same id with different content is refused. |
| `POST /v1/orders/{package_id}/runs` | Start a saved package. `Idempotency-Key` header as in `/v1/jobs`. |
| `GET /v1/triggers` | Stored triggers and their state. |
| `POST /v1/triggers` | Create a trigger for a saved order. |
| `PATCH /v1/triggers/{id}` | Change condition or enable/disable. |
| `DELETE /v1/triggers/{id}` | Remove a trigger. |
| `POST /v1/triggers/{id}/fire` | Run the trigger now. |

A later Hub slice can call `POST /v1/orders` directly ("save to this
computer") so the download step disappears; this repository needs no Hub
change for the manual import flow.
