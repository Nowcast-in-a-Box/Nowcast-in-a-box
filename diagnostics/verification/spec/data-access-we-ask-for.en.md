# The data access interface we would like the platform to provide

For the Nowcast-in-a-Box maintainers. English is not our first language; corrections welcome.

Related: `interfaces/data.py` (the current `DataAdapter`) · `diagnostics/README.md` (the diagnostics request)

## In one sentence

**We want to ask for one field by name, time and lead -- and nothing else.**

```python
truth   = source.get_observation(name, valid_time)              # name + time
predict = source.get_prediction(model_id, init_time, lead)      # name + time + lead
```

`Field` carries its own coordinates, its own units and its own validity time.
**Do not tell us where the file is, what format it is, or whether xarray is involved.**

---

## 1. Why the current shape does not fit

`interfaces/data.py` is designed to feed a model: one `load(request)` returns a whole
batch (context frames and target frames), because opening an HDF5 file once, seeking one
row and reading everything out of it is the cheap way to do that. **We agree with that
design and suggest keeping it.**

But nobody doing verification needs that batch. We need this:

> "The truth at 2017-06-27T00:35Z, and the forecast at lead 40 minutes -- one 2-D field each."

To get one field today, the caller has to know and assemble:

```
source / data_dir / data_root / initial_time / history_steps /
interval_minutes / channels / bbox                                   -- 8 fields
+ ChannelSpec.mode / SpatialSpec.crop / bbox_order / native_cadence_minutes
```

None of that is part of the question "which field do I want". `history_steps` and
`interval_minutes` are the worst of it: they say how many frames a **model** consumes and
how far apart, while the person verifying only wants the truth at one instant.

## 2. What we would like (five methods)

```python
class FieldAccess(Protocol):

    def capabilities(self) -> Capabilities:
        """Cadence, available observations, available models, units. Does not open files."""

    def list_observations(self, name: str, t0, t1) -> list[datetime]:
        """Cadence-aligned times in [t0, t1] for which name really has data."""

    def get_observation(self, name: str, valid_time) -> Field:
        """One observation field at one exact valid time."""

    def list_predictions(self, model_id: str, init_time) -> list[int]:
        """The leads in minutes this run actually produced."""

    def get_prediction(self, model_id: str, init_time, lead_minutes: int) -> Field:
        """One forecast field for one init time and one lead."""
```

```python
Field(
    data,        # 2-D array
    coords,      # its own grid: 1-D lon / lat, strictly increasing
    valid_time,  # timezone-aware
    unit,        # a string
    variable,
    name,
    mask,        # missing points; **None when there are none -- not NaN instead**
)
```

## 3. Three requirements that are not negotiable

**a. A field must carry its own coordinates.**

Do not make us recover them from the `bbox` we passed in the request. That bbox is *our
question*, not the data's *declaration*. Today `DataBundle` carries no coordinates at all
(only `data`, `source_files`, `metadata`), while `Forecast` does carry `coords` -- that
asymmetry should go away, in favour of carrying them.

**b. A field must carry its own units.**

Units exist only in `DataCapabilities.channels.variables`, and `variables` is empty when
`mode = "open"` -- so an open-channel source has no units to offer. Verification needs
units: a score computed between a forecast and a truth in different units is meaningless.
(This is not a preference. Our core refuses the comparison.)

**c. No data must be an error, not an empty array.**

```
the thing does not exist   ->  raise (we record it as "this run produced nothing")
it exists but has gaps     ->  mark them in `mask`, **not as 0 and not as NaN**
```

We distinguish **a gap** from **a zero**. Writing a gap as zero looks identical on a chart
and makes the conclusion wrong. This is the thing this module cares about most.

## 4. One performance requirement -- easy to get backwards

**One read per (source, init time).**

If a per-time interface opens the file once per field, it is slower than what we have now.
So:

```
the interface is one field at a time     <- what the caller sees
underneath it is one batch at a time     <- DataAdapter.load() stays
the cache belongs inside the implementation  <- not demanded of the caller
```

Asking for the simple interface **without** saying this is asking for one file open per
field. `probe.py` in our module counts reads, so this can be checked rather than promised.

## 5. Things we explicitly do not want

| Not this | Why |
|---|---|
| Telling us paths, filenames, directory layout | that is the reader's business |
| Telling us the file format (HDF5 / NetCDF / …) | same |
| Requiring xarray | our core depends on numpy only |
| Requiring `history_steps` / `interval_minutes` to fetch one observation | those belong to the model, not to verification |
| NaN as "there is no data here" | see 3c |

## 6. This also settles a question nobody has answered

We looked, and **nothing anywhere says where a forecast is stored or in what format.**
`predict()` returns an in-memory `Forecast` and stops there, while the diagnostics request
carries `forecasts: [{id, path}]` -- so the platform evidently does write them somewhere,
but that convention is not written down.

**With the interface above, that question does not need answering:**

```
get_prediction(model_id, init_time, lead) -> Field
                                            ^ the storage format becomes an internal matter
```

We will not need to know where forecasts live, any more than we need to know HDF5's row
layout.

## 7. If you would rather not build it, ours can be a stopgap

| | |
|---|---|
| **Suggestion** | put the interface in `interfaces/`, beside `data.py`, implemented upstream |
| **Why** | metrics and visualization **both** need it; two implementations of one thing is where silent disagreement lives |
| **In the meantime** | we implement one ourselves (an adapter over `DataAdapter`, with the cache), so we are not blocked |
| **To be explicit** | **that one is a stopgap.** When yours is ready we swap the provider -- one branch in a factory -- rather than keeping two forever |

## 8. A minimal acceptance test

This should run, and the caller should contain no filename, no format name and no xarray:

```python
source = <your implementation>
truth = source.get_observation("sevir", datetime(2017, 6, 27, 0, 40, tzinfo=utc))
assert truth.unit == "raw"
assert truth.coords.shape == truth.data.shape
assert truth.missing.dtype == bool

leads = source.list_predictions("wadepre", datetime(2017, 6, 27, 0, 35, tzinfo=utc))
predict = source.get_prediction("wadepre", datetime(2017, 6, 27, 0, 35, tzinfo=utc), leads[-1])
assert predict.unit == truth.unit          # our core refuses the comparison otherwise
```

**And one performance acceptance**: fetching the 20 leads of a single init time must open
the file **once**. An implementation should be able to report that count.
