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

A later Hub slice can call `POST /v1/orders` directly ("save to this
computer") so the download step disappears; this repository needs no Hub
change for the manual import flow.
