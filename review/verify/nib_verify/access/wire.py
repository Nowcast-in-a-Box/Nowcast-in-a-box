"""The wire format for the field access contract.

Both the client and the reference service import this module, so the two sides
cannot drift apart. That is the point of putting it in one file: an encoding that
exists twice is an encoding that will eventually disagree with itself.

See ``spec/field-access-http.md`` for the prose version.

Arrays travel as base64 of the raw little-endian bytes, with the dtype and shape
beside them. Text was the obvious alternative and it is the wrong one: a 200x200
field is 40,000 numbers, which is around 400 kB of JSON and a float-to-text round
trip per point. Bytes are exact and roughly a tenth of the size.

``encoding: "list"`` is also understood, and the service can be asked for it with
``?encoding=list``. It exists for debugging — ``curl`` output a person can read.

What this module does *not* do is paper over a provider that violates the
contract. A time that is not timezone-aware is an error here rather than an
assumption, and a field whose coordinates do not match its data is an error
rather than a reshape. The one place it is deliberately lenient is the mask: see
:func:`decode_field`.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import numpy as np

from .base import Capabilities, Coords, Field

#: Bumped when a change would break an existing client. Sent on every response.
PROTOCOL = 1

#: What ``encode_array`` writes unless told otherwise.
DEFAULT_ENCODING = "base64"
ENCODINGS = ("base64", "list")

#: Error codes the service uses, and what the client does with them.
#:
#: The contract has exactly one exception, ``FieldUnavailable``, and these codes
#: exist so the reasons stay distinguishable in a log without turning into an
#: exception hierarchy the platform would have to implement.
UNAVAILABLE_CODES = ("unavailable", "unknown-name", "off-cadence", "no-such-run")
BAD_REQUEST = "bad-request"
INTERNAL = "internal"


class WireError(ValueError):
    """A payload does not follow the contract.

    This is a programming error on one side or the other, not a missing field.
    It is never raised for a time the provider simply does not have — that is
    :class:`~nib_verify.access.base.FieldUnavailable`.
    """


# ======================================================================
# times
# ======================================================================
def time_to_wire(t: datetime) -> str:
    """``2026-01-01T00:00:00Z``. Timezone-aware UTC, always."""
    if not isinstance(t, datetime):
        raise WireError(f"expected a datetime, got {type(t).__name__}")
    if t.tzinfo is None:
        raise WireError(f"naive datetime cannot go on the wire: {t!r}")
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def time_from_wire(text: Any) -> datetime:
    """Parse the above, and refuse anything that is not absolute.

    A naive timestamp is refused rather than assumed to be UTC. Assuming is how
    a run silently shifts by the machine's offset, and every number derived from
    it shifts too.
    """
    if not isinstance(text, str):
        raise WireError(f"expected an ISO-8601 string, got {type(text).__name__}")
    raw = text.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise WireError(f"not an ISO-8601 time: {text!r}") from exc
    if parsed.tzinfo is None:
        raise WireError(f"time is not timezone-aware: {text!r}")
    return parsed.astimezone(timezone.utc)


# ======================================================================
# arrays
# ======================================================================
def encode_array(values: np.ndarray, *, encoding: str = DEFAULT_ENCODING) -> dict:
    """A numpy array as a JSON-safe object."""
    array = np.asarray(values)
    if encoding == "base64":
        # astype with copy=False keeps a native little-endian x86 array as it is
        little = array.astype(array.dtype.newbyteorder("<"), copy=False)
        payload = base64.b64encode(little.tobytes(order="C")).decode("ascii")
    elif encoding == "list":
        payload = array.tolist()
    else:
        raise WireError(f"unknown array encoding {encoding!r}; expected {ENCODINGS}")
    return {
        "encoding": encoding,
        "dtype": array.dtype.name,
        "shape": [int(n) for n in array.shape],
        "data": payload,
    }


def decode_array(obj: Any) -> np.ndarray:
    """The inverse of :func:`encode_array`, for either encoding."""
    if not isinstance(obj, Mapping):
        raise WireError(f"expected an array object, got {type(obj).__name__}")
    try:
        dtype = np.dtype(obj["dtype"])
        shape = tuple(int(n) for n in obj["shape"])
    except KeyError as exc:
        raise WireError(f"array object is missing {exc.args[0]!r}") from exc
    except (TypeError, ValueError) as exc:
        raise WireError(f"malformed array header: {exc}") from exc

    encoding = obj.get("encoding", DEFAULT_ENCODING)
    payload = obj.get("data")
    if encoding == "base64":
        if not isinstance(payload, str):
            raise WireError("base64 array data must be a string")
        try:
            raw = base64.b64decode(payload, validate=True)
        except Exception as exc:  # noqa: BLE001 - binascii raises several kinds
            raise WireError(f"array data is not valid base64: {exc}") from exc
        expected = int(np.prod(shape)) * dtype.itemsize if shape else dtype.itemsize
        if len(raw) != expected:
            raise WireError(
                f"array data is {len(raw)} bytes but shape {shape} of {dtype} "
                f"needs {expected}"
            )
        # frombuffer gives a read-only view of the response bytes; callers get a
        # writable copy so nothing downstream has to know where it came from.
        flat = np.frombuffer(raw, dtype=dtype.newbyteorder("<"))
        array = np.array(flat, dtype=dtype, copy=True)
    elif encoding == "list":
        try:
            array = np.asarray(payload, dtype=dtype)
        except (TypeError, ValueError) as exc:
            raise WireError(f"array data cannot be read as {dtype}: {exc}") from exc
    else:
        raise WireError(f"unknown array encoding {encoding!r}; expected {ENCODINGS}")

    try:
        return array.reshape(shape)
    except ValueError as exc:
        raise WireError(f"array data does not fit shape {shape}: {exc}") from exc


# ======================================================================
# fields
# ======================================================================
def encode_field(field: Field, *, encoding: str = DEFAULT_ENCODING) -> dict:
    """A :class:`Field` as a JSON-safe object.

    ``data`` goes out as it stands and ``mask`` as its own array, rather than
    sending ``values()``. A provider is allowed to keep a fill value under the
    mask, and rewriting it here would make this module the authority on what
    "missing" means.
    """
    mask = field.mask
    payload = {
        "name": field.name,
        "variable": field.variable,
        "unit": field.unit,
        "valid_time": time_to_wire(field.valid_time),
        "lon": encode_array(np.asarray(field.coords.lon, dtype=float), encoding=encoding),
        "lat": encode_array(np.asarray(field.coords.lat, dtype=float), encoding=encoding),
        "values": encode_array(field.data, encoding=encoding),
    }
    if mask is not None:
        payload["mask"] = encode_array(np.asarray(mask, dtype=np.uint8), encoding=encoding)
        payload["missing"] = int(np.count_nonzero(mask))
    else:
        payload["missing"] = 0
    return payload


def decode_field(
    obj: Any, *, on_warning: Callable[[str], None] | None = None
) -> Field:
    """The inverse of :func:`encode_field`.

    Lenient in exactly one place. If the values carry NaN that the payload did
    not declare as missing, the mask is widened to cover them and a warning is
    raised. The alternative — trusting the declared mask and passing NaN through
    as a value — lands NaN in a contingency table, where every comparison
    against a threshold is False, so it is silently counted as a correct
    negative and flatters the score. A hole has to stay a hole.
    """
    if not isinstance(obj, Mapping):
        raise WireError(f"expected a field object, got {type(obj).__name__}")

    try:
        values = decode_array(obj["values"])
        lon = decode_array(obj["lon"])
        lat = decode_array(obj["lat"])
    except KeyError as exc:
        raise WireError(f"field object is missing {exc.args[0]!r}") from exc

    for label, axis in (("lon", lon), ("lat", lat)):
        if axis.ndim != 1:
            raise WireError(f"{label} must be 1-D, got shape {axis.shape}")

    declared: np.ndarray | None = None
    if obj.get("mask") is not None:
        declared = decode_array(obj["mask"]).astype(bool)
        if declared.shape != values.shape:
            raise WireError(
                f"mask shape {declared.shape} does not match values {values.shape}"
            )

    missing = declared if declared is not None else np.zeros(values.shape, dtype=bool)
    if np.issubdtype(values.dtype, np.floating):
        undeclared = np.isnan(values) & ~missing
        if undeclared.any():
            if on_warning is not None:
                on_warning(
                    f"{int(np.count_nonzero(undeclared))} point(s) were NaN but not "
                    f"declared missing; treated as missing"
                )
            missing = missing | undeclared

    for key in ("name", "variable", "unit"):
        if not isinstance(obj.get(key), str):
            raise WireError(f"field object is missing the string {key!r}")

    try:
        coords = Coords(lon=lon, lat=lat)
    except ValueError as exc:
        raise WireError(f"the coordinates are unusable: {exc}") from exc

    try:
        return Field(
            data=values,
            coords=coords,
            valid_time=time_from_wire(obj.get("valid_time")),
            unit=obj["unit"],
            variable=obj["variable"],
            name=obj["name"],
            mask=missing if missing.any() else None,
        )
    except ValueError as exc:
        # Field and Coords check their own invariants. Catching them here keeps
        # one rule: anything wrong with the payload arrives as a WireError, so
        # the client has exactly one thing to translate.
        raise WireError(f"the field does not hold together: {exc}") from exc


# ======================================================================
# capabilities
# ======================================================================
def encode_capabilities(caps: Capabilities) -> dict:
    return {
        "protocol": PROTOCOL,
        "cadence_minutes": int(caps.cadence_minutes),
        "observation_names": list(caps.observation_names),
        "model_ids": list(caps.model_ids),
        "retrospective": bool(caps.retrospective),
        "units": dict(caps.units),
    }


def decode_capabilities(obj: Any) -> Capabilities:
    if not isinstance(obj, Mapping):
        raise WireError(f"expected a capabilities object, got {type(obj).__name__}")
    seen = obj.get("protocol")
    if seen is not None and int(seen) != PROTOCOL:
        raise WireError(
            f"the provider speaks protocol {seen} and this client speaks {PROTOCOL}; "
            f"one of the two needs updating"
        )
    try:
        cadence = int(obj["cadence_minutes"])
    except KeyError as exc:
        raise WireError("capabilities are missing 'cadence_minutes'") from exc
    except (TypeError, ValueError) as exc:
        raise WireError(f"cadence_minutes is not a number: {exc}") from exc
    if cadence <= 0:
        raise WireError(f"cadence_minutes must be positive, got {cadence}")

    def strings(key: str) -> tuple[str, ...]:
        raw = obj.get(key) or []
        if isinstance(raw, str):
            raise WireError(f"{key} must be a list of strings, not a string")
        try:
            return tuple(str(x) for x in raw)
        except TypeError as exc:
            raise WireError(f"{key} must be a list of strings: {exc}") from exc

    units = obj.get("units") or {}
    if not isinstance(units, Mapping):
        raise WireError("units must be an object mapping name to unit string")

    return Capabilities(
        cadence_minutes=cadence,
        observation_names=strings("observation_names"),
        model_ids=strings("model_ids"),
        retrospective=bool(obj.get("retrospective", True)),
        units={str(k): str(v) for k, v in units.items()},
    )


# ======================================================================
# errors
# ======================================================================
def encode_error(code: str, detail: str) -> dict:
    return {"error": code, "detail": detail}


def decode_error(obj: Any) -> tuple[str, str]:
    """``(code, detail)`` from an error payload, tolerating a plain string."""
    if isinstance(obj, Mapping):
        return str(obj.get("error", INTERNAL)), str(obj.get("detail", ""))
    return INTERNAL, str(obj)
