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
python -m pytest                   # 248 tests, no GPU, no network, no data
```

The command line:

```bash
# what can be computed
python -m nib_verify metrics list

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
| `spec/verification-config.md` | the config format and its mutability rules |
| `spec/catalog.md` | how we read the platform, and what we refuse to assume |
| `nib_verify/access/` | the field contract, and the mock providers |
| `nib_verify/catalog/` | the host's config, package manifests, run configs, matching |
| `nib_verify/config/` | the config, validation, and change detection |
| `nib_verify/core/` | regridding, additive quantities, metric formulas, planning |
| `nib_verify/store/` | SQLite: quantities and task state |
| `nib_verify/runner/` | task execution, progress, the single-instance lock |
| `nib_verify/query/` | the read path |
| `nib_verify/server/` | the web service, and the review page it serves |
| `nib_verify/cli.py` | the command line |
| `tests/` | 248 tests, all synthetic |

---

## Where the data comes from

Two separate questions, with two separate answers.

### Fields

**The provider does not exist yet.** It is specified in
`spec/field-access.md` and not built, so this module ships two substitutes:

| Provider | Reads | Use |
|---|---|---|
| `mock` (default) | nothing — a seeded Gaussian blob | tests, CI, development |
| `replay` | `.npy` / `.npz` on disk | integration runs against real samples |
| `real` | — | raises, deliberately |

`--access real` raises rather than falling back. A build that silently verified
against generated numbers would be worse than one that refuses to start.

The rest of this module only knows the interface, so switching to the platform's
provider later is one flag.

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
| `tests/test_cli.py` | exit codes, progress modes, end-to-end run and query |
| `tests/test_cli_platform.py` | the platform-facing commands, end to end |
| `tests/test_server.py` | the API, and the three rules it refuses to break |
| `tests/test_web_page.py` | every path and file the page calls actually exists |

Nothing here needs a GPU, a network, or a data file.

---

## Not done yet

Deliberately out of scope so far:

- the real field provider;
- deriving `extent` and `resolution` from the data (they are entered by hand);
- inserting a verification task into the platform's own scheduler;
- charts — the results table is a table.

`spec/*.md` closes with the open questions that are still genuinely open rather
than merely unimplemented.
