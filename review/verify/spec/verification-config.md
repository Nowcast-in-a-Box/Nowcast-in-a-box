# Verification config

**Status:** draft, matching the implementation in `nib_verify/config/schema.py`.

**Scope:** the file that says what gets verified, against what, and how.

---

## The shape

One config is **one ruler, one roster, one metric list**.

```json
{
  "group": { "id": "vil-radar-v1", "name": "VIL verification, radar composite" },
  "ruler": {
    "ground_truth": "radar-composite",
    "unit": "kg m-2",
    "time_step_minutes": 10,
    "extent": [110.0, 30.0, 120.0, 42.0],
    "resolution": 0.5,
    "fingerprint": "b41f9c2a7de80315"
  },
  "members": [
    { "name": "mlcast-det", "variable": "vil", "unit": "kg m-2",
      "leads": [10, 20, 30, 60, 120], "active_from": null, "active_to": null }
  ],
  "metrics": [
    { "family": "categorical", "items": ["ts", "bias", "ets"],
      "levels": [16, 74, 133, 181, 219], "half_windows": [] },
    { "family": "continuous", "items": ["me", "mae", "rmse"] },
    { "family": "spatial", "items": ["fss"],
      "levels": [16, 74, 133, 181, 219], "half_windows": [1, 2, 4] }
  ]
}
```

A config is a file. It goes into version control, it can be reviewed, and it can
be diffed. Nothing about it lives only in a running process.

---

## The ruler

The ruler decides the **denominator and the mask** of every metric.

| Field | Meaning |
|---|---|
| `ground_truth` | which observation source is the truth |
| `unit` | the unit of the numbers and of every threshold |
| `time_step_minutes` | the one time step; no interpolation, ever |
| `extent` | `[lon_min, lat_min, lon_max, lat_max]` |
| `resolution` | grid spacing of the verification grid |

There is **no `variable`**. The ground truth package already names the field it
serves, so a second place to write it down is a second place to get it wrong —
and nothing ever compared the two.

`unit`, `time_step_minutes`, `extent` and `resolution` are **derived from the
ground truth package** wherever the manifest declares them, and left empty where
it does not. A package usually names a *region* rather than a grid, so the extent
and the resolution are often the caller's to supply: they decide the mask, and a
guess here would silently decide which samples the numbers are made of. The unit
is not editable (every member has to agree with it); the other three are.

The manifest is the source of the derivation, so the form and the service read
the same values — see [catalog.md](catalog.md) for `ruler_defaults`.

### The ruler is immutable

Changing any ruler field produces a **new config**, not an edit. The module
enforces this: `VerificationConfig.allows_modification_of` returns `("ruler",)`
whenever the ruler differs from the one a config was loaded with, and the CLI
refuses the write.

The reason is not tidiness. Change the truth source and the set of grid points
changes. Change the extent and the CSI denominator changes. Change the
resolution and both the grid and the regridded values change. A reading taken
under a different ruler is **not comparable** with the old one, and letting it
overwrite the old one would quietly destroy the only thing the numbers were for.

`ruler.fingerprint` is a hash of exactly these six fields. It is stored in the
config and reported with every query result, so a reader can tell at a glance
whether two numbers came from the same ruler.

`extent` and `resolution` are entered by hand. Deriving them from the data is
planned but not done — and it is a convenience, not a correctness feature.

### The time step is fixed

`time_step_minutes` must match the provider's declared cadence, and every
prediction member's. There is no runtime reconciliation: a time the data does
not cover raises `FieldUnavailable` rather than being interpolated. Two members
on different cadences cannot share a config.

---

## The roster

Members are the forecast models being scored.

| Field | Meaning |
|---|---|
| `name` | the model id, as the provider knows it |
| `variable` | the model's own output field; from the ground truth package |
| `unit` | must match the ruler's unit |
| `leads` | which lead times, in minutes, to verify |

| `active_from`, `active_to` | the window in which this member is on the roster |

`leads` is **proposed from the model's own run configs**: `forecast_horizon` is a
frame count, so it becomes `[1..horizon] × time_step_minutes`. The proposal is a
starting point, not a fence — the box is editable, and at planning time each run
clips to what its own horizon reaches, reporting the leads it dropped.

Members are **added and retired, never deleted**. Retiring sets `active_to`, so
the history of what was being verified stays readable.

A leading `None` in `active_from` or `active_to` means unbounded.

---

## The metric list

Metrics are grouped by family, because members of a family share one set of
parameters and one row of stored quantities.

| Family | Metrics | Parameters |
|---|---|---|
| `categorical` | `ts` (alias `csi`), `bias`, `ets`, `pod`, `far`, `dts` | `levels` |
| `continuous` | `me`, `mae`, `rmse` | — |
| `moments` | `corr` | — |
| `spatial` | `fss` | `levels`, `half_windows` |

A config is created with **three families already on it**: `continuous`,
`categorical` and `spatial`. Everything in a family is verified — there is
nothing to tick. The only things to fill in are the parameters: `levels` for the
categorical family, and `levels` plus `half_windows` for `fss`. `moments` stays
a valid family (hand-written configs may carry it), but it is not among the
defaults.

On screen the families are named for the question each one answers — `Yes or no`,
`Continuous`, `Special` — because a family outlives the first metric that landed
in it.

### Metrics are append-only

Adding `pod` next to `ts` is allowed. Removing `ets`, or renaming it, is not:
the numbers already published under it would silently change meaning.

`with_metric_group` refuses to append a metric that is already present, rather
than ignoring the duplicate.

### A level is a threshold

`levels` are in the ruler's unit, and are compared as
`value >= level`. A level is where the cost lives — see below.

---

## What a config produces

A config is a **scheduling basis**, not a task list. It expands into one task per
`(member, init_time, lead)`, drawn from the runs that exist.

```
one config  +  many runs  →  many tasks
```

Tasks are keyed `(group_id, member, init_time, lead_minutes)` and move through
`pending → running → done | skipped | failed | unavailable`.

A task records `done` and is never recomputed unless asked. That is what makes a
second pass over a narrower level set free: the rows are already there.

---

## What gets stored

Only **additive quantities**. No ratios, no scores, no averages.

| Family | Columns | Feeds |
|---|---|---|
| `cat` | `H F M C` | `ts`, `bias`, `ets`, `pod`, `far` |
| `cont` | `T E A S` | `me`, `mae`, `rmse` |
| `mom` | `T MX MY SX SY SXY` | `corr` |
| `spatial` | `pob pfo fbs n_valid` | `fss` |

Every row carries the full dimension tuple
`(group_id, member, init_time, valid_time, lead_minutes, level, half_window)`,
so any sub-selection is a `SUM` over a subset of rows. Writes are upserts:
recomputing a period overwrites it, never appends to it.

| Change | Cost |
|---|---|
| Add a metric to an existing family | **no new rows** — same row, one more formula |
| Add a level | new rows — a level is a threshold |
| Add a member | new rows, one set per member |
| Change a threshold set | a re-query; no recomputation, no inference |
| Change the ruler | a new config, and recomputation |

`fss` is stored as the three sums that define it, `pob`, `pfo`, `fbs`, because
`FSS = 1 - fbs / (pob + pfo)`. Storing the score would make it unaggregatable.

`dts` is the same: a formula over `H`, `F`, `M`, `C` — **the block `ts` already
uses**, so adding it to a config costs a query and nothing else.

```
DTS = ( H/(H+F+M) + C/(M+F+C) ) / 2
```

The event's threat score and the no-event's, averaged. `ts` alone only asks how
well the event was found; `dts` also asks how the negatives came out, which is
what separates two forecasts that `ts` calls equal. When either denominator is
zero the score is **undefined** — a gap, not MetEva's `IV = 999999`, because a
number that means "not a number" is how an average gets taken over a value that
does not exist.

---

## Validation

A config is rejected when any of these fail.

- `unit` is empty — thresholds would be meaningless.
- The extent is backwards, or the resolution or step is not positive.
- A member's unit differs from the ruler's.
- A member has no leads, or a non-positive lead, or ends before it starts.
- The roster has a duplicate name.
- A metric needs a level or a window and none was given.
- The same metric appears twice.

The checks that need the provider — cadence agreement, lead availability, and
whether the named truth source and members exist at all — run against
`FieldAccess.capabilities()` before a config is accepted.

---

## Open points

1. **Who owns `extent` and `resolution`.** They are hand-entered today because no
   package manifest declares a geographic domain. If one did, this becomes a
   default rather than a decision.
2. **`leads` versus a range.** An explicit list is unambiguous but verbose. A
   `[start, stop, step]` form would read better and needs a rule for what happens
   when a model does not produce the last one.
3. **Ruler changes that are genuinely comparable.** A resolution change invalidates
   comparison; adding a level does not. A finer-grained rule than "the ruler
   changed" might allow an in-place change that is currently forced to a new file.
4. **Multiple rulers per config.** Not supported, deliberately. Two rulers in one
   file would let incomparable numbers end up in one table.
5. **Retirement scheduling.** `active_to` can be set today, but nothing warns
   before a member silently stops being verified.
