# Field access over HTTP

**Status:** the wire binding we build against, with a reference service that runs
today. **No platform implementation exists yet.**

**Scope:** [field-access.md](field-access.md) says *what* a provider answers. This
says *how* it answers over a socket. Nothing here changes the contract; if the
two ever disagree, that file wins and this one is wrong.

---

## Why write this down

The contract in `field-access.md` is five Python methods. A platform service
cannot implement Python methods. Something has to fix the bytes in between, and
if that something is not written down then two implementations of the same
contract cannot be compared — which is the only reason a contract is worth
having.

So this document is the interface specification, and
`nib_verify/access/mockfeed.py` is a working implementation of it. A team
building the platform's provider can read one and run the other.

---

## The addresses

One base URL. Everything is a `GET`; nothing has a body; nothing has side
effects, so everything is safe to retry.

| | |
|---|---|
| `GET /health` | is this a field service, and which protocol |
| `GET /capabilities` | cadence, names, models, units, retrospective |
| `GET /observations?name=&t0=&t1=` | cadence-aligned times in `[t0, t1]` |
| `GET /observation?name=&valid_time=&encoding=` | one field |
| `GET /predictions?model_id=&init_time=` | the leads a run produced |
| `GET /prediction?model_id=&init_time=&lead_minutes=&encoding=` | one field |

The base URL may carry a path prefix, so `https://host/nib/data/capabilities` is
not a special case.

**Times** are ISO-8601 with an offset, and a bare `Z` is preferred:
`2026-01-01T00:00:00Z`. A timestamp without an offset is **refused**, by both
sides. Assuming UTC is how a run silently shifts by the machine's offset, and
every number derived from it shifts too.

**Only loopback is expected.** The reference service binds `127.0.0.1` by
default. A provider reachable from elsewhere is the platform's decision, not
this module's.

---

## Answers

```jsonc
// GET /capabilities
{
  "protocol": 1,
  "cadence_minutes": 10,
  "observation_names": ["radar-composite"],
  "model_ids": ["mlcast-det"],
  "retrospective": true,
  "units": {"radar-composite": "kg m-2", "mlcast-det": "kg m-2"}
}

// GET /observations
{"name": "radar-composite", "times": ["2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"]}

// GET /predictions
{"model_id": "mlcast-det", "lead_minutes": [10, 20, 30]}

// GET /observation, GET /prediction
{
  "name": "radar-composite",
  "variable": "vil",
  "unit": "kg m-2",
  "valid_time": "2026-01-01T00:00:00Z",
  "lon": <array>,          // 1-D, strictly increasing
  "lat": <array>,          // 1-D, strictly increasing
  "values": <array>,       // 2-D, shape (len(lat), len(lon))
  "mask": <array>,         // optional; omitted when nothing is missing
  "missing": 150           // how many points the mask covers, for humans
}
```

An `<array>` is:

```jsonc
{"encoding": "base64", "dtype": "float64", "shape": [24, 32], "data": "…"}
```

### Why base64

A 200×200 field is 40,000 numbers. As JSON text that is roughly 400 kB and a
float-to-text round trip per point. As raw little-endian bytes it is 320 kB
before encoding and exact — `0.1` comes back as the same `0.1`, not as the
nearest decimal that was printed. A test asserts the bit pattern survives, not
just the value.

`"encoding": "list"` is also understood, and the service can be asked for it with
`?encoding=list`. It exists for debugging: `curl` output a person can read.

The bytes are **little-endian** regardless of the sender's architecture, and the
`dtype` is a numpy name (`float64`, `float32`, `uint8`). A mask travels as
`uint8`, `1` meaning missing.

---

## Failures

An error is a JSON object, and the HTTP status says which kind:

| Status | `error` | Client raises |
|---|---|---|
| 404 | `unavailable` | `FieldUnavailable` |
| 404 | `unknown-name` | `FieldUnavailable` |
| 404 | `off-cadence` | `FieldUnavailable` |
| 404 | `no-such-run` | `FieldUnavailable` |
| 400 | `bad-request` | `HttpAccessError` |
| 500 | `internal` | `HttpAccessError` |
| anything else | — | `HttpAccessError` |

```json
{"error": "off-cadence", "detail": "2026-01-01T00:00:01Z is not on the 10-minute cadence"}
```

The contract has exactly one exception, so the four 404 codes all arrive as
`FieldUnavailable`. They exist so that "the model does not produce that lead" and
"the archive has nothing at that hour" do not read identically in a server log.
`off-cadence` is reserved: the reference service classifies what it can from the
declared capabilities and answers `unavailable` for the rest.

### A 404 that is not ours is not a missing field

This is the trap worth spelling out. Point the client at a mistyped address and
*every* request 404s. If that came back as `FieldUnavailable`, the planner would
report a run with nothing in it and the run would look like it succeeded.

So a 404 only means "unavailable" when the body carries this protocol's error
object. Anything else is `HttpAccessError`:

> `http://host:8770/nib/data/capabilities` is not a field access endpoint
> (404, and the body is not this protocol's error object). Check the address.

---

## Where the guarantees are enforced

`spec/field-access.md` lists five. They matter more than the format, so it is
worth being clear about which side of the wire each one lives on.

| # | Guarantee | Enforced |
|---|---|---|
| 1 | A field carries its own grid | `wire.decode_field` refuses coordinates that do not match the data, or that are not strictly increasing |
| 2 | Time matches exactly, or it is an error | the provider raises; the client never interpolates, and there is no flag to make it |
| 3 | Units are what they claim | the provider declares; the client compares against the config and refuses to compute on a mismatch |
| 4 | Missing is marked, never zero | the provider sends the mask; **the client widens the mask over any undeclared NaN** |
| 5 | History is served | `retrospective` is read before anything is planned |

Number 4 is the one the wire format had to make a decision about. A payload can
say "nothing is missing" while carrying NaN, and NaN compared against a threshold
is `False` — so it lands in the correct-negative column and flatters every
categorical score. The decoder therefore treats undeclared NaN as missing, and
records a warning. The numbers stay right, and the provider's mistake becomes a
line in `datafeed check` instead of a quiet improvement in the scores.

What is *not* checkable: whether a provider wrote a hole as `0` and covered it
with a mask. A mask makes any fill value legal, including `0`, so the two cases
are indistinguishable from outside. That is why the checklist requires the mask
and does not attempt to police what is underneath it.

---

## The reference service

```bash
# serve the generator
python -m nib_verify datafeed serve --access mock --port 8770

# serve files on disk (see nib_verify/access/replay.py for the layout)
python -m nib_verify datafeed serve --access replay --mock root=/data/samples

# is it one?  (exit code is the answer)
python -m nib_verify datafeed check --url http://127.0.0.1:8770
```

and then point the verifier at it, which is the whole point:

```bash
python -m nib_verify task run --config cfg.json --root .store \
    --access real --data-url http://127.0.0.1:8770
```

`datafeed check` runs the conformance checklist from `field-access.md` against
whatever is at the address. It is meant to be usable as a gate before a long run,
and against the platform's provider once it exists.

`--access real` is an alias for `--access http`. It **fails without
`--data-url`** — this module does not guess an address, the same way it does not
guess a host path. That is what keeps the old promise that a build cannot quietly
verify against made-up numbers: there is exactly one way to do that, and it is to
type `--access mock`.

---

## Open points

1. **Whose format is this?** Nobody's yet. It is a reasonable shape built to make
   the missing-field distinction explicit, and it is disposable if the platform
   would rather serve something else. The contract is `field-access.md`; this
   file is downstream of it.
2. **Chunking.** One field per request is fine at 48×64 and wasteful at 2000×2000
   over a slow link. A batch address, or a range of leads in one reply, would
   change the shape of the caching conversation but not the contract.
3. **Compression.** Responses are not compressed. A field of smoothly varying
   floats compresses well and this is a mock, so it was left out rather than
   guessed at.
4. **Auth.** None. Loopback only, and the platform's service will have to decide
   this for itself.
5. **The prefix convention.** `base_url` + a path prefix is supported and
   untested against a real proxy. A provider that mounts itself at
   `/nib/data/` (with the trailing slash) may need the rule pinned down.
