# review/verify — the verification module (D1)

Metrics from stored prediction and truth, computed so that changing a threshold
is a query rather than a recomputation.

Part of the module map in `docs/modules.yaml`. The design is submitted for review
as an RFC; this directory is the implementation that follows it.

---

## Quick start

```bash
cd review/verify
pip install -e '.[dev,web]'         # .[dev] alone runs everything but the page
python -m pytest                   # 583 tests, no GPU, no network, no data
```

**The whole thing, end to end, in one command.** It fabricates a platform, serves
simulated fields on a real socket, verifies through it over HTTP, and brings up
the review page:

```bash
python -m nib_verify demo --open
```

There is a script for showing that to people — what each stage proves, what to
click, and the questions it invites — in [DEMO.md](DEMO.md).

The command line, stage by stage:

```bash
# what can be computed
python -m nib_verify metrics list

# the simulated data interface: serve a provider, and check one
python -m nib_verify datafeed serve --access mock --port 8770
python -m nib_verify datafeed check --url http://127.0.0.1:8770

# what the platform already has
python -m nib_verify host show                  # its config.yaml, resolved
python -m nib_verify catalog packages           # manifests it ships
python -m nib_verify catalog runs               # runs it has done
python -m nib_verify catalog match              # which data/model pairs are legal

# check a config against the provider before running anything
python -m nib_verify config validate cfg.json --access mock

# what this config would do, and what it cannot
python -m nib_verify task plan --config cfg.json --runs <RUNS> --probe

# run, then read
python -m nib_verify task run --config cfg.json --root .store --runs <RUNS>
python -m nib_verify query --method ts --config cfg.json --root .store \
    -s level=181,219 -g lead_minutes -g level

# or look at it
python -m nib_verify chart --method ts --config cfg.json --root .store \
    -s level=181 -x lead_minutes --series member --out csi.svg
```

`--root` is where we write: one SQLite file and one lock file, nothing else.
Where the platform's paths are involved we read its own `config.yaml` rather
than guessing — see [spec/catalog.md](spec/catalog.md).

The review page:

```bash
python -m nib_verify serve --root .store --configs ./configs \
    --packages <PACKAGES> --runs <RUNS> --access mock
# then open http://127.0.0.1:8766
```

It binds `review.host`:`review.port` from `config.yaml`, refuses a non-loopback
address, and comes up even when the field provider cannot — saying so rather than
failing to start.

---

## Two organising ideas

**1. One config, one set of verification tasks.**

A config is one *ruler*, one *roster*, one *metric list*. The ruler — truth
source, variable, unit, time step, extent, resolution — is **immutable**:
changing it produces a new config, because a reading taken under a different
ruler is not comparable with the old one. The roster is free to change through
effective intervals. Metrics are append-only.

**2. Three stages.**

```
① scheduling  ──tasks──▶  ② metrics  ──additive quantities──▶  ③ query
   config × runs            one frame pair in,                 sums in,
                            counts and sums out                 numbers out
```

The interface between ② and ③ is *counts and sums*, never ratios. That is why a
threshold change is a re-aggregation: the numbers the answer is built from are
still there, unaggregated.

---

## Layout

| Path | What lives there |
|---|---|
| `spec/field-access.md` | the contract we build against, and its five guarantees |
| `spec/field-access-http.md` | the wire binding, and the simulated data interface |
| `spec/verification-config.md` | the config format and its mutability rules |
| `spec/catalog.md` | how we read the platform, and what we refuse to assume |
| `nib_verify/access/` | the field contract, the wire format, the client, the reference service |
| `nib_verify/catalog/` | the host's config, package manifests, run configs, matching |
| `nib_verify/config/` | the config, validation, and change detection |
| `nib_verify/core/` | regridding, additive quantities, metric formulas, planning |
| `nib_verify/store/` | SQLite: quantities and task state |
| `nib_verify/runner/` | task execution, progress, the single-instance lock |
| `nib_verify/query/` | the read path |
| `nib_verify/report/` | the read path, drawn |
| `nib_verify/demo.py` | the whole chain, in one command, for showing people |
| `nib_verify/server/` | the web service, and the review page it serves |
| `nib_verify/cli.py` | the command line |
| `tests/` | 583 tests, all synthetic |

---

## Where the data comes from

Two separate questions, with two separate answers.

### Fields

The platform's provider does not exist yet. It is specified in
`spec/field-access.md`, the wire binding is in `spec/field-access-http.md`, and
this module builds and tests against **a service of our own that speaks it**:

| Provider | Reads | Use |
|---|---|---|
| `mock` (default) | nothing — a seeded Gaussian blob, in process | tests, CI, development |
| `replay` | `.npy` / `.npz` on disk, in process | integration runs against real samples |
| `http` | a service over a socket | **the shape the platform's provider will have** |
| `real` | the same as `http` | spelled the other way |

`--access real` needs a `--data-url` and refuses without one. Inventing an
address would be the same mistake as inventing a host path, and there is exactly
one way to verify against generated numbers: type `--access mock`.

So the simulated data interface is two files, and between them they close the
gap:

```bash
# the service: any provider, at the contract's addresses
python -m nib_verify datafeed serve --access mock --port 8770

# is what is at that address actually a field provider?
python -m nib_verify datafeed check --url http://127.0.0.1:8770

# and then verify through it, exactly as we will through the platform's
python -m nib_verify task run --config cfg.json --root .store \
    --access real --data-url http://127.0.0.1:8770
```

`access/wire.py` is imported by **both halves**, so they cannot disagree about
the format. `access/mockfeed.py` wraps any provider rather than special-casing
the generator — which is why it is also the reference a platform team can copy.

Running the same config through `--access real` and `--access mock` gives
identical readings, and a test asserts it: the client is meant to be transparent.

### What the mock actually generates, and why it had to change

The generator's defaults are one drifting blob with a forecast that **equals the
truth**. That is exactly what the acceptance tests need, and it is the worst
possible thing to develop an interface against: every CSI comes back `1.0`, every
threshold looks identical, and a display that prints `1.0` forever looks correct.

So there are named scenarios, reachable in one flag:

```bash
python -m nib_verify datafeed serve --access mock --scenario convective
python -m nib_verify task run  --access mock --scenario convective ...
```

| Scenario | What it is |
|---|---|
| `simple` | one blob on the VIL scale, a perfect forecast, nothing missing |
| `convective` | discrete cells on a flow, two models of different skill, radar coverage gaps |
| `widespread` | smooth stratiform rain over most of the domain |
| `patchy` | every third run produces nothing, one lead is missing, a fifth of the grid is a hole |

Each is a bundle of constructor settings rather than a separate generator, and an
explicit `--mock` value wins over the bundle, so `--scenario convective --mock
peak=300` works. Leaving `--scenario` off gives the constructor's own defaults,
which is what every existing test uses — the new knobs are strictly additive and
`cells=1, drift=0` is still the same field to the last bit.

The knobs earn their place by making a specific mistake visible:

| Knob | What it turns on |
|---|---|
| `cells`, `drift`, `background` | a field with a distribution — cores, edges and a floor — so thresholds have something to disagree about |
| `forecast_lag_minutes` | a forecast that is the field from **earlier**, not a blurred one. Structure right, position wrong: a high threshold loses it entirely, a low threshold barely notices |
| `skill_spread` | models in the order declared, getting worse. A comparison needs a winner |
| `missing_kind=range` | a coverage gap that is *contiguous*, next to random speckle. A large hole catches a resampler that fills; scattered points catch one that smears |
| `missing_runs_every`, `drop_leads` | runs that produced nothing and leads that are missing, so the planner's rejections and the run's `unavailable` count do something instead of being read about |

With `convective`, CSI runs from about `0.93` at 16 down to `0.67` at 219, and
the better model wins at every threshold. Which is the point: numbers you can be
wrong about.

### What the wire format had to decide

Arrays travel as base64 of raw little-endian bytes, not as JSON text. A 200×200
field is 40,000 numbers — around 400 kB of text and a float-to-text round trip
per point. The bytes are exact and about a tenth of the size.

One decision was not about size. A payload can say "nothing is missing" while
carrying NaN, and NaN compared against a threshold is `False`, so it lands in the
correct-negative column and flatters every categorical score. **The decoder treats
undeclared NaN as missing** and records a warning: the numbers stay right, and
the provider's mistake becomes a line in `datafeed check` instead of a quiet
improvement in the scores. `tests/test_wire.py` is where that is pinned down.

### Packages and runs

These *do* exist, and `AGENTS.md` is explicit that we must not assume a layout
for them:

> Do not invent a catalog tree for data and models until that layout is designed.

The host's paths are read from its own `config.yaml`, the way `handler/config.go`
reads them. Package manifests and run configs are found by walking a root the
caller names and recognising files **by what they contain** — a manifest by its
`offers`/`requires`, a run config by its `data`/`model`/`run` sections. Move every
package somewhere else and discovery still works. See `spec/catalog.md`.

### What the mock buys

The acceptance condition for D1 is *"changing a CSI threshold does not start ONNX
again"*. With a synthetic provider that can produce a forecast identical to the
truth, or a known displacement, or a chosen amount of missing data, that
condition is **testable today** — no model, no GPU, no data.

`tests/test_done_when.py` asserts:

1. CSI at 181 is identical whether or not 16, 74 and 133 were also computed.
2. A query never calls the provider (the call counter does not move).
3. A second pass over a completed period reads nothing.
4. Five thresholds cost the same number of prediction reads as two.

---

## The review page

The page is three tabs — configs, a wizard, results — over a JSON API. It is
plain JavaScript with no framework and no build step, because the repository root
has no `pyproject` and no `node_modules`, and a reviewer should be able to read
the whole interface in one sitting.

| | |
|---|---|
| `GET /api/health` | whether the process is up, and where it thinks it is |
| `GET /api/host` | the resolved `config.yaml` |
| `GET /api/catalog` | packages, runs, and which pairs the Matcher allows |
| `GET /api/metrics`, `/api/metrics/{m}/dimensions` | what can be computed, and how it can be split |
| `GET /api/chart` | the same query as `/api/scores`, drawn, as `image/svg+xml` |
| `GET/POST/DELETE /api/configs[/{id}]` | the configs |
| `POST /api/configs/{id}/roster[...]` | add a member, or retire one |
| `POST /api/configs/{id}/metrics` | append a metric group |
| `POST /api/plan` | what a config would do, and what it cannot |
| `POST /api/runs`, `GET /api/jobs/{id}` | start a run, then watch it |
| `GET /api/scores` | aggregate stored quantities |

FastAPI publishes the same thing as OpenAPI at `/docs`.

**The page holds no rules of its own.** The three invariants are enforced in the
service, not in the browser, so the wizard cannot be talked into breaking them by
a crafted request:

* a config change touching the ruler is refused, not written;
* metrics are appended, and one already present is refused rather than duplicated;
* an empty plan caused by rejections comes back as such, with reasons.

Run progress is polled rather than streamed. A run is short and the page is
served by the same process, so reconnection semantics would be more code and
more ways to be wrong than asking again.

---

## Rules the code enforces

These are the invariants the design rests on. Each one has a test.

| Rule | Where |
|---|---|
| Only counts and sums are stored; no ratios | `store/schema.sql`, `core/quantities.py` |
| Writes are upserts — a recomputation overwrites, never appends | `store/db.py` |
| Missing is marked, never written as zero | `access/base.py`, `core/regrid.py` |
| Time matches exactly; there is no temporal interpolation | `access/synthetic.py`, `core/regrid.py` |
| Units are compared before anything is computed | `runner/executor.py`, `core/regrid.py` |
| The ruler is immutable; changing it needs a new config | `config/schema.py` |
| Metrics are append-only | `config/schema.py` |
| A failure in one task never stops the run, and never propagates | `runner/executor.py` |
| A missing data root is a refusal, not a default | `catalog/hostconfig.py` |
| A package is identified by what it declares, not where it sits | `catalog/packages.py`, `catalog/runs.py` |
| An empty plan caused by rejections is an error, not a quiet success | `cli.py`, `server/app.py` |
| The interface enforces nothing; the service enforces everything | `server/app.py`, `server/library.py` |
| A failure in the background is data on a job, never a 500 | `server/jobs.py` |

The unit rule is worth calling out, because its absence would be invisible. Every
threshold in a config is written in the ruler's unit. If a provider hands back a
field in a different unit the comparisons still run, still produce numbers, and
mean nothing. The executor refuses instead, and the task is recorded as failed.

---

## Charts

A chart is a view of a query, not a second way to ask. There is exactly one path
from the store to a line on a chart and it goes through the same
`query_scores` the table uses, so a chart cannot show a number the table would
refuse to return, and a filter means the same thing in both.

```bash
# skill against lead time, one line per model, at one threshold
python -m nib_verify chart --method ts --config cfg.json --root .store \
    -s level=181 -x lead_minutes --series member --out csi.svg

# and a fixed scale, to put two charts side by side honestly
python -m nib_verify chart ... --y-min 0 --y-max 1
```

Which dimension is which follows the convention `query_scores` already documents:
**the last expansion runs along x, the ones before it separate the lines.** The
review page reads that back from the expansion chips the user picked and asks the
service to draw it — nothing in the page decides what a chart is.

It is **SVG, rendered on the server**. Drawing in the browser would mean a second
renderer that drifts from this one, a chart that cannot be exported without a
browser, and the rule about gaps living in JavaScript where nothing can test it.
The page loads it with `<img src="/api/chart?...">`, so a chart is also a URL you
can save, link to, or paste into a slide.

Four decisions, each of which a naive implementation gets wrong:

| | |
|---|---|
| **A gap is a gap** | The store returns a null for a quantity with no samples. That is not a zero — an undefined CSI and a CSI of 0 say opposite things about a forecast. The line **breaks** over it, and the legend says `(gap)` |
| **No coordinate is NaN** | `x="nan"` does not warn, it makes the element vanish; the chart is then merely emptier than it should be. Every coordinate is checked, and there is a test |
| **The axis snaps to its ticks** | Bounds are round numbers so gridlines land on the corners. Otherwise the axis quietly starts a gridline below where it claims, because `floor(0.6 / 0.1) * 0.1` is `0.6000000000000001` and that tick is simply not drawn |
| **The y axis is not forced to zero** | A CSI curve living between 0.85 and 0.95 is unreadable against a zero-based axis, and the decay over lead time is the reason to plot it at all. The axis is fitted to the data and **every tick is labelled**, which is what makes a fitted axis honest. `--y-min`/`--y-max` override it |

The tests parse the SVG as a document rather than searching the text for
substrings: geometry is checked against the canvas, so a legend that runs off the
right edge fails. That one was found by looking at a rendered chart, and it is
the reason the check exists.

---

## Command line contract

A handler is expected to call this on a timer without reading any of our config,
so three things are promised:

1. **Exit codes** — `0` means everything asked for succeeded, including "there
   was nothing to do". Non-zero means something failed. Partial failure is
   non-zero.
2. **Progress** — one line per task on stdout, following `NIB_PROGRESS`:
   `quiet` says nothing at all, `plain` (default) prints text, `json` prints one
   object per line.
3. **A lock** — `--root` holds a single-instance lock, so a slow run cannot be
   started twice. A stale lock from a dead process is taken over.

---

## Tests

```bash
python -m pytest -q                        # everything
python -m pytest tests/test_done_when.py   # just the acceptance conditions
python -m pytest -q -k "not cli"           # skip the process-level tests
```

| File | Covers |
|---|---|
| `tests/test_done_when.py` | the acceptance conditions (5) |
| `tests/test_quantities.py` | contingency tables, sums, neighbourhood windows |
| `tests/test_metrics.py` | every formula, against hand-worked numbers |
| `tests/test_regrid.py` | alignment, missing-data propagation, unit and time guards |
| `tests/test_config.py` | the three mutability rules |
| `tests/test_runner.py` | isolation, gaps, task state, the lock |
| `tests/test_catalog.py` | host config, content-based discovery, the Matcher's rules |
| `tests/test_planner.py` | every rejection reason, probing, several members |
| `tests/test_cli.py` | exit codes, progress modes, end-to-end run, query and chart |
| `tests/test_cli_platform.py` | the platform-facing commands, end to end |
| `tests/test_server.py` | the API, and the three rules it refuses to break |
| `tests/test_web_page.py` | every path and file the page calls actually exists |
| `tests/test_page_in_browser.py` | the page, driven in a real browser (skips without one) |
| `tests/test_wire.py` | the encoding, both ways, including the undeclared-NaN rule |
| `tests/test_http_access.py` | the client against the service, over a real socket |
| `tests/test_conformance.py` | the checklist, and proof that broken providers fail it |
| `tests/test_cli_datafeed.py` | the datafeed commands, and that `real` still cannot be a hole |
| `tests/test_scenario.py` | the named scenarios, and that they are not a perfect forecast |
| `tests/test_chart.py` | the chart as a document: no NaN, nothing off the canvas, gaps stay gaps |
| `tests/test_demo.py` | the fabricated workspace is consistent with itself |

Nothing here needs a GPU, a network, or a data file. The browser tests skip unless
playwright can find a browser; with Edge or Chrome installed no download is
needed, and without one they degrade to a skip rather than to a false pass.

They earn that place. The static tests read `app.js` and check that the paths and
ids it mentions exist — which catches a typo in a URL and does *not* catch a call
to a renamed function, a helper that stringifies an array instead of rendering
it, or a required field whose value gets sent anyway because a disabled input
still holds its text. All three of those shipped past the static tests and were
caught by the browser.

---

## Not done yet

Deliberately out of scope so far:

- the platform's own field provider — the contract, the client, a working
  reference service and simulated data good enough to develop against are all
  done, and pointing at theirs is a URL;
- **real sample files.** There are none yet, so `ReplayAccess` is unexercised
  beyond its own tests. `--scenario convective` is what stands in for them;
- deriving `extent` and `resolution` from the data (they are entered by hand);
- inserting a verification task into the platform's own scheduler.

`spec/*.md` closes with the open questions that are still genuinely open rather
than merely unimplemented.
