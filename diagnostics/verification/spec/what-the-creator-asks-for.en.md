# Catalogues we would like the platform to provide

For the Nowcast-in-a-Box maintainers. The sibling of
`spec/data-access-we-ask-for.en.md`: that one is about reading **fields**, this one about
reading **declarations**.

Related: `interfaces/data.py` · `interfaces/model.py` · `adapters/catalog.json` · `contracts/`

## In one sentence

**Our config-creating program reads declarations only -- never data, never a model -- so the declarations have to be complete, machine-readable and enumerable.**

```
system config (declarations) --► creating program --► verification config (with paths) --► running program
                                   ^ this document is about what it needs
```

## What the creating program does

Given what the platform has already produced, it produces one verification configuration:

1. **Count what exists** -- which data sources, which models, which runs
2. **Build a ruler for each source** -- which truth, spatial extent and resolution, time step, units
3. **Keep only the pairs that fit** -- units, shape and cadence must agree
4. **Write the paths in** -- where the truth is, where each forecast is
5. **Write the metrics** -- grouped by family (`yes_no` / `continuous` / `spatial` / `moments`)

**It does not read a single byte of weather data and does not load an ONNX file.**

## 1. What already exists and should stay

| Needed | Present upstream | |
|---|---|---|
| Which data sources | `adapters/catalog.json` + `contracts/data/<id>.json` | ok |
| Which models | `contracts/models/<data-id>/<model-id>.json` | ok |
| **Which model pairs with which source** | `data_adapter_id` -- **the pairing is data, not code** | good, keep |
| Variables, dtypes, spatial shape, frame counts, cadence | `ChannelSpec.variables`, `output.spatial`, `native_cadence_minutes` | ok |
| Versions | `version` on both sources and models | ok |
| Where weights come from | `weights.download_url` / `sha256` / `artifacts_relpath` | ok |
| Validation rules | `interfaces/` is standard library only; `ConstraintError` carries a readable reason | ok |
| **No importing adapter code** | `adapters/README.md`'s closing `TODO(ci)` **already asks for exactly this** | ok |

That last one is worth quoting, because it is the same requirement we have:

> "A later job outside this repo should scan `contracts/data/*.json` and
> `contracts/models/<data-id>/*.json` and compare them with `adapters/catalog.json`.
> **It must not import adapter Python and must not install satpy.**"

**The creating program is that job.** We suggest promoting it from `TODO` to a stated
requirement.

## 2. Three real gaps

### a. No spatial extent and no resolution -- and a ruler cannot be built without them

Today a declaration gives the **shape**:

```json
"output": { "spatial": [128, 128] }        <- 128 by 128 grid points
```

A ruler needs to know **which part of the Earth those 128x128 cover** and **how many
degrees a cell is**. `DataCapabilities.spatial` has only `crop` and `bbox_order`. And
SEVIR's `region.bbox` is supplied *by the request* (`[0.0, 0.0, 1.0, 1.0]`) rather than
declared by the data -- the direction is backwards.

**Without this we cannot build a ruler**, and the only thing left is to use the *model's*
grid as the ruler -- which is wrong: **the ruler has to be the observation's grid**, and
resampling a model onto it is our job.

**Request**: declare the extent (bbox) and the resolution in `contracts/data/<id>.json`.
Sources that can crop (CMA radar) should also declare the range they can crop to.

### b. Nothing documents what a run writes, or where -- the biggest gap

The creating program has to put paths into the configuration, so it must be able to answer:

```
Which runs have there been?  (model x data source x init time)
Where does each run write its forecast, and in what format?
```

Upstream does not say:

- `config.yaml`'s `paths.runs: runs` is commented *"One directory per **diagnostics** invocation"* -- that is the diagnostics directory, not a model run
- the persistent workspace is gitignored and its layout is undocumented
- `predict()` returns an in-memory `Forecast` and stops
- the only mention of a path is `forecasts: [{id, path}]` in the diagnostics request -- which shows forecasts *are* written, without saying by whom or how

**Request** (the mirror image of section 6 of the data-access document):

```
one run        =  one directory
in it          a manifest naming: model id / data source id / init time / the forecast file(s)
the file(s)    a fixed, machine-readable format -- readable without importing an adapter
```

**Until this is settled the creating program does not know what to write.**

### c. An open-channel source declares no variables and no units

`ChannelSpec.variables` is only populated when `mode = "closed"`. For an `open` source the
creating program cannot learn **which variables it provides or in what units**, so it can
neither build a ruler nor pick a roster.

**Request**: declare the variables and units in both modes. `open` can mean "these are the
ones whose units I know; the rest are not guaranteed" -- but it cannot mean saying nothing.

## 3. One property to preserve: declarations must be stable

Our ruler carries a fingerprint. A ruler that changes is a different set of verifications,
and its scores are not comparable with the old ones. That depends on declarations being
stable:

```
same id + same version   =>  the same ruler
extent / step / units / variable names changed  =>  the version must change
```

**Request**: `version` is a semantic version, not a timestamp. Anything that changes a
score changes the version.

**Otherwise we draw two sets of incomparable numbers on one chart and nothing shows it.**

## 4. Things we explicitly do not want

| Not this | Why |
|---|---|
| Requiring the creating program to import anything under `adapters/` | those pull satpy, h5py, onnx; creating a config should not install them (upstream already asks for this) |
| Requiring it to **open a data file** to learn which variables exist | that is what the declaration is for |
| Requiring it to **run a model** to learn the output shape | `output` declares it |
| Putting "which runs exist" in **code** (knowable only by executing) | it has to be readable as data |
| Hiding the pairing inside `validate_input()`'s **implementation** | today `data_adapter_id` is data -- keep it that way |

## 5. Acceptance: a test that does not read the code

```
Given a repository's declarations (contracts/ + adapters/catalog.json),
importing no adapter Python, installing no satellite library, reading no data file,

the creating program must be able to list:
  - for each data source: variables, units, cadence, spatial extent, resolution, version
  - for each model: which source it pairs with, output dims/shape/frames, weight URL and checksum
  - which (truth, model) pairs are legal, with a reason for each illegal one

and must produce a valid verification configuration.
```

**And a reverse acceptance**: given a declaration whose resolution changed but whose
version did not, the creating program must refuse it -- or at least say so loudly. See
section 3.

## 6. How this relates to the sibling document

| | Creating program (this one) | Running program (the other) |
|---|---|---|
| Reads | **declarations**: what exists, with what properties | **fields**: one field by name, time and lead |
| When | once, when a config is generated | per task, when a config is executed |
| Biggest gap | **resolution** and **run output layout** | **fields carry no coordinates or units**, and the cache ownership |

**Together the two documents are the whole of this module's dependency on upstream.**
