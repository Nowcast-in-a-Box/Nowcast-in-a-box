# Field access

**Status:** the contract we build against. **No platform implementation exists
yet.** A deterministic reference implementation ships with this module so work
does not have to wait — see [Providers](#providers) below.

**Scope:** how the verification module asks for a field of numbers. One
observation at one valid time, or one prediction at one lead. Nothing else.

---

## Why this is a separate contract

Verification needs two arrays that describe the same instant: what was observed,
and what was predicted. It needs them on grids that can be reconciled, at times
that match exactly, in units that mean the same thing, and with missing data
marked as missing.

It does **not** need to know where the bytes live. Paths, file formats, chunking,
and catalog layout are all somebody else's problem, and pinning them here would
make this module the place every storage decision has to be changed.

So the contract is deliberately narrow, and the module reads nothing else.

---

## The contract

Five methods. Times are timezone-aware UTC `datetime` objects.

```python
class FieldAccess(Protocol):

    def capabilities(self) -> Capabilities:
        """Declare cadence, available names, and whether history is served."""

    def list_observations(self, name: str, t0: datetime, t1: datetime) -> list[datetime]:
        """Cadence-aligned times in [t0, t1] for which `name` has data."""

    def get_observation(self, name: str, valid_time: datetime) -> Field:
        """One observation field at an exact `valid_time`."""

    def list_predictions(self, model_id: str, init_time: datetime) -> list[int]:
        """Lead times, in minutes, that this run actually produced."""

    def get_prediction(self, model_id: str, init_time: datetime, lead_minutes: int) -> Field:
        """One prediction field for `init_time` + `lead_minutes`."""
```

Note what is absent. There is no search, no query language, no time range on a
getter, no way to ask for a subset of a grid. A caller says *what* and *when*,
and gets one 2-D array with its coordinates.

### `Capabilities`

| Field | Meaning |
|---|---|
| `cadence_minutes` | the one time step the data lives on |
| `observation_names` | valid values for `name` in `get_observation` |
| `model_ids` | valid values for `model_id` in `get_prediction` |
| `retrospective` | whether history is served, not just the latest step |
| `units` | name or model id → unit string |

Configuration validation reads this before anything else runs.

### `Field`

| Field | Meaning |
|---|---|
| `data` | 2-D float array |
| `coords` | its own grid: 1-D, strictly increasing `lon` and `lat` |
| `valid_time` | the instant this field describes |
| `unit` | unit string, comparable across the two sides |
| `variable` | what is being measured |
| `name` | the observation name or model id |
| `mask` | `True` where there is no value; `None` if nothing is missing |

---

## The five guarantees

These are what the verification module actually depends on. Everything else in
this document is detail.

### 1. A field carries its own grid

`Field.coords` describes the array. The consumer never has to hold a second
table of grid definitions, and two fields from different providers are never
assumed to share a grid.

Resampling onto the config's grid is the **consumer's** job. A provider should
hand back what it natively has.

### 2. Time matches exactly, or it is an error

`get_observation` accepts a time on the declared cadence and returns the field
for *that* time. Off-cadence, past the horizon, or outside the archive: raise
`FieldUnavailable`.

**Never interpolate in time.** The cadence is part of the config's ruler, so
silently producing a field for an instant the data does not cover would change
the meaning of every number derived from it. There is no flag to turn this off.

### 3. Units are what they claim to be

`Field.unit` is the unit of the numbers in `data`. A threshold that means
"35 dBZ" is meaningless against a field in mm/h, so the consumer compares the
declared units before computing anything and refuses to continue on a mismatch.

### 4. Missing is marked, never zero

`mask[i, j] = True` means there is no value at that point. It does **not** mean
the value is zero, and it must not be written as zero.

This matters more than it looks. In a contingency table a fabricated zero lands
in the "below threshold" column, which inflates the number of correct negatives
and silently flatters every categorical score. A hole in the data has to stay a
hole all the way through to the metric, or the metric is wrong in a direction
nobody will notice.

`Field.values()` returns the array with missing points as `NaN`, never as zero.

### 5. History is served

A verification task is usually about a period that has already ended, and a
backfill has to be able to reach back over it. If a provider cannot serve a past
time, `capabilities().retrospective` says so and the planner plans around it.

---

## Providers

Two implementations ship with this module. Neither is a standard; they exist so
the module can be built and tested before the platform's provider arrives.

| Provider | Source | Purpose |
|---|---|---|
| `SyntheticAccess` | generated, seeded | unit tests, CI, metric correctness |
| `ReplayAccess` | `.npy` / `.npz` on disk | integration runs against real sample files |
| *(the platform's)* | TBD | production |

Selection is a single switch, because everything above this line treats the
provider as an interface:

```
--field-access mock     # default while the platform one does not exist
--field-access replay
--field-access real     # raises until it is implemented
```

`--field-access real` deliberately raises rather than falling back to generated
data. A build that silently verifies against made-up numbers would be worse than
a build that refuses to start.

### Why the mock matters

The synthetic provider generates a drifting Gaussian blob and can be driven to
produce whatever a test needs: a forecast identical to the truth, a known
displacement, a specific amount of missing data, a grid that differs from the
observation's.

That makes D1's acceptance condition testable **today**, with no model, no GPU,
no network, and no data:

> Changing a CSI threshold does not start ONNX again.

See `tests/test_done_when.py`. Its five tests assert that thresholds are
independent of one another, that a query never calls the provider, that
re-running a completed task reads nothing, and that five thresholds cost the
same number of prediction reads as two.

---

## Conformance checklist

A provider is usable when all of these hold.

- [ ] `capabilities()` returns a cadence, and every time it serves is on it.
- [ ] `get_observation` / `get_prediction` raise `FieldUnavailable` for an
      off-cadence time rather than interpolating.
- [ ] Every returned `Field` has `coords` matching `data.shape`.
- [ ] Every returned `Field` has a unit, and it is the unit of the numbers.
- [ ] Missing points are in `mask`; no missing point is written as `0`.
- [ ] `list_predictions` returns only leads that `get_prediction` can serve.
- [ ] Times are timezone-aware and in UTC.
- [ ] Historical times work, or `retrospective` is `False`.

---

## Open points

1. **Naming.** Should `name` for observations and `model_id` for predictions be
   two vocabularies, or one? They are separate today because a data package and
   a model package are different things, but a single registry would be simpler
   to validate against.
2. **3-D fields.** `Field` is 2-D. Multi-level or ensemble output would need a
   third axis, and would need a rule for whether members are averaged before
   thresholding or after.
3. **Units registry.** A free string is easy to get wrong. A closed vocabulary,
   or a canonical form with parsing, would let the mismatch check be exact.
4. **Provenance.** A `Field` does not say which file or run produced it. Useful
   for debugging; not needed for metrics.
5. **Whose resampling.** This document puts it on the consumer. If the platform
   would rather serve a requested grid, the contract shrinks and this module
   loses a component — worth deciding deliberately.
