# Reading the platform

**Status:** draft, matching `nib_verify/catalog/`.

**Scope:** everything this module takes from the platform, and everything it
deliberately refuses to assume.

---

## The problem this document solves

The verification module needs three things it does not own:

1. where the platform's paths are;
2. which data and model packages exist;
3. which runs have happened.

None of these has a settled answer. `AGENTS.md` says so directly:

> Do not invent a catalog tree for data and models until that layout is designed.

and lists, under *Ask first*:

> A top-level catalog directory layout for data and models.

Meanwhile `contracts/README.md` says the JSON Schemas for all of this
"belong here **when that work is claimed**" — they do not exist yet.

So this module works from two rules. **Read what the platform already
publishes, and identify everything else by content.** Nothing in
`nib_verify/catalog/` hard-codes a directory name, and there is no test in the
suite that would break if every package were moved somewhere else.

---

## 1. Host settings come from `config.yaml`

`AGENTS.md`:

> Read bind addresses and paths from `config.yaml`. Flags override the file.
> Fail if the data root is missing. Never invent a host path.

`nib_verify/catalog/hostconfig.py` mirrors `handler/config.go` exactly, so the
handler and this module cannot disagree about which file is being read:

| Step | Rule |
|---|---|
| Discovery | `NIB_CONFIG`, else walk upwards from the cwd for `config.yaml` |
| Defaults | handler `127.0.0.1:8765`, review `127.0.0.1:8766`, artifacts `artifacts` |
| Relative paths | resolved against the directory the config file sits in |
| Data root | `paths.data_root`, overridden by `NIB_DATA_ROOT` |

`HostConfig.require_data_root()` raises when the data root is absent or does not
exist. There is **no fallback**, on purpose. A caller that cannot proceed without
a data root must say so, and the message names the flag that would fix it:

```
nowhere to look for runs: pass --runs DIR, or --root DIR, or set
paths.data_root in config.yaml. This module does not guess a host path.
```

`review.host` and `review.port` are read for the same reason: the handler is
documented to *start* the review page, so our future service must listen where
`config.yaml` says, not where we would have chosen.

---

## 2. Packages are recognised by what they declare

A package manifest is JSON that carries `offers` (data) or `requires` (a model) —
the one structural claim `nib-architecture/rfc/matching.md` is firm about:

```json
// data
{ "id": "sevir-vil", "offers": ["precip-nowcast.v1"], "fields": [ ... ] }
// model
{ "id": "wadepre", "requires": "precip-nowcast.v1",
  "context_frames": { "required_count": 5, "mode": "latest" },
  "input": [ ... ], "output": [ ... ] }
```

Discovery walks a root the caller names, finds files called `manifest.json`, and
keeps the ones shaped like the above. Two failure modes are kept apart:

| Situation | Behaviour |
|---|---|
| a `manifest.json` that is not shaped like a package manifest | **ignored** — other projects ship files with that name |
| one that *is* shaped like a package manifest but cannot be read | recorded in `skipped`, because that is a real problem |

### Parsing is tolerant on purpose

The manifest schema is `design` status, and the `FieldSpec` it defers to lives
in `prototype/interfaces/types.py`, which is not in any repository we have. So
known keys are read through a list of accepted spellings, and everything
unrecognised is kept verbatim in `PackageManifest.raw`:

| Wanted | Accepted spellings |
|---|---|
| field name | `name`, `variable`, `field`, `id` |
| unit | `unit`, `units`, `unit_string` |
| shape | `shape`, `spatial_shape`, `grid`, `grid_shape` |
| cadence | `cadence_minutes`, `cadence`, `time_step_minutes` |
| frame count | `frame_count`, `frame_counts`, `frames` |

A manifest is never rejected for carrying a key we have not seen. Losing a
package to a renamed field would be worse than reading a field twice.

---

## 3. Run configs

`contracts/README.md` documents a closed schema:

```yaml
version: 1
data:  { id: sevir-vil }
model: { id: wadepre }
run:   { start_time: "2017-06-13T15:05:00Z", forecast_horizon: 20 }
```

Three consequences follow, and they are all deliberate.

**Discovery is by content.** There is no defined location for a run config, so
candidate `.yaml`/`.yml`/`.json` files are parsed and the ones with the
run-config shape are kept. A file that *looks* like a run config and then fails
to parse is reported in `skipped`; a file that does not look like one is ignored.

**Unknown keys are errors**, because the contract says so. A file we would have
misread is worse than a file we refuse. `RunConfig.from_mapping(..., strict=False)`
exists for a caller that knows why it needs the looser behaviour.

**`forecast_horizon` is a frame count, not a duration.** The conversion to lead
times needs the cadence, which comes from the package manifest, so it happens in
exactly one place — `RunConfig.leads_within_horizon(cadence_minutes)` — and
nowhere else.

### What a run config does not say

It does not say where the output went, and it does not say whether the run
succeeded. Both questions are answered by the field provider, which either can
serve a lead or cannot. That is why `runs.py` is short.

---

## 4. Matching is a stand-in for A2

`nib_verify/catalog/matching.py` implements the hard errors and warnings from
`rfc/matching.md` verbatim:

**Hard errors** — profile not offered, cadence mismatch, spatial shape mismatch,
too few context frames, output length ≠ target length, a field the data never
declares.

**Warnings** — dtype differs, declared region differs.

One behaviour is worth stating because it is a judgement call: **a comparison we
cannot make is a warning, not an error.** If neither side declares the cadence
there is nothing to compare, and refusing the pair would make an incomplete
manifest unusable while the schema is still in flux.

This file is a placeholder. When A2 is implemented, delete it and call theirs.

---

## 5. What a ground truth can answer for the ruler

A verification config's ruler is, by design, immutable. Most of it is not a
decision anybody makes — it is a fact the data package already declares:

| Ruler field | Where it comes from |
|---|---|
| `unit` | the package's first named field |
| `time_step_minutes` | the package's cadence |
| `extent` | `extent` (or `bounds`) on the package or the field, if declared |
| `resolution` | `resolution` on the package or the field, if declared |

`PackageManifest.as_dict()` carries this as `ruler_defaults`, and it is exposed
through `GET /api/catalog` so the form can fill its boxes without asking a second
question. Where a manifest declares nothing, the value is `None` and the box
stays **empty**.

Two rules, and both matter more than they look:

- **Nothing is derived from a region name.** Manifests usually name a region
  (`east-china`) rather than a grid. A name is not an extent, so no corners are
  invented from it. The extent decides the mask, and a guessed mask decides which
  samples the numbers are made of.
- **Blanks are filled, never overwritten.** `POST /api/configs` applies the same
  derivation, so a caller posting only a ground truth gets the same config the
  form would have built. A value the caller supplied is left alone: the step in
  particular is editable on purpose, because a config may want a coarser one than
  its source produces.

## 6. Planning states intent; running states fact

```
one config  +  many runs  →  many tasks
```

The planner decides which `(member, init_time, lead)` triples the config asks
for, and reports **why** each run it could not use was rejected:

| Reason | Meaning |
|---|---|
| `not-on-roster` | the run's model is not a member of this config |
| `inactive` | the member was not on the roster on that date |
| `no-start-time` | the run is identified by `sample_id`, so it has no place on a time series |
| `beyond-horizon` | no requested lead falls inside the run's forecast horizon |
| `incompatible-pair` | the Matcher refuses the data/model pair |

A plan that is empty because every run was rejected is an **error**, not a
success with nothing to do. Those are different outcomes and the CLI exits
differently for them.

What the planner does **not** do is decide whether the provider can serve a
frame. Files can be pruned after a run, so a plan built on "does the file exist"
would be wrong the moment it was written. A frame the provider cannot serve is
recorded as `unavailable` by the executor, which is a statement about fact.

`--probe` closes the loop on demand: it asks `list_observations` for the window
and reports how much of the plan the observations actually cover. It costs one
call per window, not one per task, and it never pulls a prediction field.

---

## Open points

1. **Where run output lives.** Not defined by the platform. We currently assume
   nothing, and get the answer from the provider. If a run directory layout is
   ever specified, `RunConfig.directory` is the hook.
2. **A sidecar for run outcome.** "Failed runs remain inspectable" is an
   invariant, but nothing records *that* a run failed. Today a failed run simply
   has no servable leads, which is indistinguishable from a pruned one.
3. **Manifest schema ownership.** A3 owns the manifest parse. When it lands with
   a real FieldSpec, the tolerant readers here should be replaced by the strict
   one, and the alias table deleted.
4. **Run config v2.** `E3` in the module map describes a v2 with an ordered list
   of model ids. This module reads v1 only, and will need a version branch.
5. **Scanning cost.** Discovery walks a whole root. For a large workspace this
   wants an index, or a hook from the handler that says "a run was written".
6. **Where `extent` and `resolution` belong.** Deriving the ruler needs them to
   be declarable, and today they are read from either the package or its first
   field under a short list of aliases, because no manifest schema says where
   they go. When A3 does, that becomes a plain field lookup.
