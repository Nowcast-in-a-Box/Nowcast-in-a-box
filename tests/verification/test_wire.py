"""The wire format.

Both ends of the contract import this module, so these tests are the only thing
standing between a format change and two halves that disagree. They are pure:
no socket, no service, no fixture.

The one with something to say is
:func:`test_nan_the_payload_did_not_declare_is_widened_into_the_mask`. It is the
guarantee in `spec/field-access.md` §4 expressed as a test — a provider that
writes NaN and calls it a value must not be able to slip a hole past the metrics
as a zero-ish comparison.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from nib_verify.access import Capabilities, Coords, Field, WireError
from nib_verify.access import wire

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def grid(ny: int = 3, nx: int = 4) -> Coords:
    return Coords(lon=np.linspace(110.0, 120.0, nx), lat=np.linspace(30.0, 42.0, ny))


def a_field(**over) -> Field:
    ny, nx = over.pop("shape", (3, 4))
    data = over.pop("data", np.arange(ny * nx, dtype=float).reshape(ny, nx))
    return Field(
        data=data,
        coords=grid(ny, nx),
        valid_time=over.pop("valid_time", T0),
        unit=over.pop("unit", "dBZ"),
        variable=over.pop("variable", "reflectivity"),
        name=over.pop("name", "radar-composite"),
        mask=over.pop("mask", None),
    )


# ======================================================================
# arrays
# ======================================================================
@pytest.mark.parametrize("dtype", ["float64", "float32", "int32", "uint8"])
@pytest.mark.parametrize("encoding", ["base64", "list"])
def test_an_array_survives_a_round_trip(dtype, encoding):
    values = np.arange(12, dtype=dtype).reshape(3, 4)
    back = wire.decode_array(wire.encode_array(values, encoding=encoding))
    assert back.shape == values.shape
    assert back.dtype == values.dtype
    assert np.array_equal(back, values)


def test_base64_is_bit_exact_for_awkward_numbers():
    """Text would not be. This is the reason the format is bytes."""
    values = np.array([1e-300, 0.1, 1 / 3, 1e300, -0.0, np.pi])
    back = wire.decode_array(wire.encode_array(values))
    assert np.array_equal(back, values)
    assert np.array_equal(back.view(np.uint64), values.view(np.uint64))


def test_a_big_endian_array_goes_on_the_wire_as_little_endian():
    values = np.array([[1.5, 2.5]], dtype=">f8")
    back = wire.decode_array(wire.encode_array(values))
    assert np.array_equal(back, values)
    assert back.dtype.byteorder in ("=", "<", "|")


def test_the_decoded_array_can_be_written_to():
    """A read-only array from frombuffer would surprise whatever gets it next."""
    back = wire.decode_array(wire.encode_array(np.zeros((2, 2))))
    assert back.flags.writeable
    back[0, 0] = 5.0


def test_a_payload_shorter_than_its_shape_is_refused():
    obj = wire.encode_array(np.zeros((3, 4)))
    obj["data"] = obj["data"][:8]
    with pytest.raises(WireError, match="bytes but shape"):
        wire.decode_array(obj)


def test_data_that_does_not_fit_its_shape_is_refused():
    """Only reachable for ``encoding: list`` — with bytes the length check gets
    there first, which is itself worth knowing."""
    obj = wire.encode_array(np.zeros(12, dtype=float), encoding="list")
    obj["shape"] = [5, 5]
    with pytest.raises(WireError, match="does not fit shape"):
        wire.decode_array(obj)


def test_a_byte_payload_of_the_wrong_length_is_caught_before_reshaping():
    obj = wire.encode_array(np.zeros(12, dtype=float))
    obj["shape"] = [5, 5]
    with pytest.raises(WireError, match="bytes but shape"):
        wire.decode_array(obj)


def test_base64_that_is_not_base64_is_refused():
    obj = wire.encode_array(np.zeros((2, 2)))
    obj["data"] = "!!!not base64!!!"
    with pytest.raises(WireError, match="not valid base64"):
        wire.decode_array(obj)


def test_an_unknown_array_encoding_is_refused():
    with pytest.raises(WireError, match="unknown array encoding"):
        wire.decode_array({"encoding": "protobuf", "dtype": "float64", "shape": [1], "data": ""})
    with pytest.raises(WireError, match="unknown array encoding"):
        wire.encode_array(np.zeros(1), encoding="protobuf")


def test_a_missing_header_field_is_refused():
    with pytest.raises(WireError, match="missing 'dtype'"):
        wire.decode_array({"shape": [1], "data": ""})


# ======================================================================
# times
# ======================================================================
def test_a_time_round_trips():
    assert wire.time_to_wire(T0) == "2026-01-01T00:00:00Z"
    assert wire.time_from_wire("2026-01-01T00:00:00Z") == T0


def test_both_ways_of_writing_utc_are_understood():
    assert wire.time_from_wire("2026-01-01T00:00:00Z") == T0
    assert wire.time_from_wire("2026-01-01T00:00:00+00:00") == T0


def test_another_offset_is_converted_not_kept():
    """An offset time is not wrong; leaving it in a second frame of reference is."""
    parsed = wire.time_from_wire("2026-01-01T08:00:00+08:00")
    assert parsed == T0
    assert parsed.tzinfo == UTC


def test_sub_second_precision_survives():
    moment = T0 + timedelta(microseconds=250)
    assert wire.time_from_wire(wire.time_to_wire(moment)) == moment


def test_a_naive_time_is_refused_rather_than_assumed():
    with pytest.raises(WireError, match="not timezone-aware"):
        wire.time_from_wire("2026-01-01T00:00:00")
    with pytest.raises(WireError, match="naive datetime"):
        wire.time_to_wire(datetime(2026, 1, 1))


def test_a_time_that_is_not_a_time_is_refused():
    with pytest.raises(WireError, match="not an ISO-8601 time"):
        wire.time_from_wire("yesterday")
    with pytest.raises(WireError, match="expected an ISO-8601 string"):
        wire.time_from_wire(20260101)


# ======================================================================
# fields
# ======================================================================
def test_a_field_round_trips_with_its_coordinates():
    original = a_field()
    back = wire.decode_field(wire.encode_field(original))
    assert np.array_equal(back.data, original.data)
    assert np.array_equal(back.coords.lon, original.coords.lon)
    assert np.array_equal(back.coords.lat, original.coords.lat)
    assert back.valid_time == original.valid_time
    assert (back.unit, back.variable, back.name) == ("dBZ", "reflectivity", "radar-composite")
    assert back.mask is None


def test_a_field_with_nothing_missing_carries_no_mask():
    """Cheap to check, and it keeps ``Field.missing`` an allocation nobody pays."""
    obj = wire.encode_field(a_field())
    assert "mask" not in obj
    assert obj["missing"] == 0
    assert wire.decode_field(obj).mask is None


def test_a_mask_round_trips():
    mask = np.zeros((3, 4), dtype=bool)
    mask[0, 1] = mask[2, 3] = True
    obj = wire.encode_field(a_field(mask=mask))
    assert obj["missing"] == 2
    back = wire.decode_field(obj)
    assert np.array_equal(back.missing, mask)
    assert np.isnan(back.values()[0, 1])


def test_a_fill_value_under_the_mask_stays_where_it_is():
    """A provider may keep anything under the mask. Rewriting it here would make
    this module the authority on what missing means."""
    data = np.full((3, 4), -9999.0)
    mask = np.zeros((3, 4), dtype=bool)
    mask[1, 1] = True
    back = wire.decode_field(wire.encode_field(a_field(data=data, mask=mask)))
    assert back.data[1, 1] == -9999.0
    assert np.isnan(back.values()[1, 1])


def test_nan_the_payload_did_not_declare_is_widened_into_the_mask():
    """The guarantee, at the boundary where it can still be enforced.

    NaN compared against a threshold is False, so an undeclared NaN lands in the
    correct-negative column and makes every categorical score look better. It has
    to be a hole by the time anything computes with it.
    """
    data = np.arange(12, dtype=float).reshape(3, 4)
    data[2, 0] = np.nan
    warned: list[str] = []

    back = wire.decode_field(wire.encode_field(a_field(data=data)), on_warning=warned.append)

    assert back.missing[2, 0], "the NaN was left looking like a value"
    assert len(warned) == 1 and "not declared missing" in warned[0]
    assert np.isnan(back.values()).sum() == 1


def test_nan_inside_the_declared_mask_is_not_worth_a_warning():
    data = np.arange(12, dtype=float).reshape(3, 4)
    data[1, 1] = np.nan
    mask = np.zeros((3, 4), dtype=bool)
    mask[1, 1] = True
    warned: list[str] = []
    wire.decode_field(wire.encode_field(a_field(data=data, mask=mask)),
                      on_warning=warned.append)
    assert warned == []


def test_a_mask_of_the_wrong_shape_is_refused():
    obj = wire.encode_field(a_field())
    obj["mask"] = wire.encode_array(np.zeros((2, 2), dtype=np.uint8))
    with pytest.raises(WireError, match="mask shape"):
        wire.decode_field(obj)


def test_a_field_without_a_unit_is_refused():
    obj = wire.encode_field(a_field())
    del obj["unit"]
    with pytest.raises(WireError, match="missing the string 'unit'"):
        wire.decode_field(obj)


def test_coordinates_that_do_not_match_the_data_are_refused():
    obj = wire.encode_field(a_field())
    obj["lon"] = wire.encode_array(np.linspace(0, 1, 9))
    with pytest.raises(WireError, match="does not hold together"):
        wire.decode_field(obj)


def test_coordinates_that_are_not_increasing_are_refused():
    obj = wire.encode_field(a_field())
    obj["lon"] = wire.encode_array(np.array([1.0, 0.0, -1.0, -2.0]))
    with pytest.raises(WireError, match="strictly increasing"):
        wire.decode_field(obj)


def test_a_field_is_also_readable_as_readable_json():
    """``?encoding=list`` exists so that a person can read a response."""
    obj = wire.encode_field(a_field(), encoding="list")
    assert isinstance(obj["values"]["data"], list)
    assert np.array_equal(wire.decode_field(obj).data, a_field().data)


# ======================================================================
# capabilities
# ======================================================================
def caps(**over) -> Capabilities:
    base = dict(
        cadence_minutes=10,
        observation_names=("radar-composite",),
        model_ids=("mlcast-det",),
        retrospective=True,
        units={"radar-composite": "dBZ", "mlcast-det": "dBZ"},
    )
    base.update(over)
    return Capabilities(**base)


def test_capabilities_round_trip():
    back = wire.decode_capabilities(wire.encode_capabilities(caps()))
    assert back.cadence_minutes == 10
    assert back.observation_names == ("radar-composite",)
    assert back.model_ids == ("mlcast-det",)
    assert back.retrospective is True
    assert back.unit_of("mlcast-det") == "dBZ"


def test_an_empty_unit_map_is_allowed_but_visible():
    back = wire.decode_capabilities(
        wire.encode_capabilities(caps(units={}))
    )
    assert back.units == {}
    assert back.unit_of("radar-composite") is None


def test_a_different_protocol_version_is_refused_with_both_numbers():
    obj = wire.encode_capabilities(caps())
    obj["protocol"] = wire.PROTOCOL + 1
    with pytest.raises(WireError, match=f"protocol {wire.PROTOCOL + 1}.*speaks {wire.PROTOCOL}"):
        wire.decode_capabilities(obj)


def test_a_capabilities_object_with_no_protocol_is_still_read():
    """Tolerating a missing version is the difference between a strict reader
    and an unusable one; the version is checked when it is offered."""
    obj = wire.encode_capabilities(caps())
    del obj["protocol"]
    assert wire.decode_capabilities(obj).cadence_minutes == 10


@pytest.mark.parametrize("bad", [0, -1])
def test_a_cadence_that_cannot_be_used_is_refused(bad):
    with pytest.raises(WireError, match="must be positive"):
        wire.decode_capabilities(wire.encode_capabilities(caps(cadence_minutes=bad)))


def test_a_cadence_that_is_not_a_number_is_refused():
    obj = wire.encode_capabilities(caps())
    obj["cadence_minutes"] = "ten"
    with pytest.raises(WireError, match="not a number"):
        wire.decode_capabilities(obj)


def test_a_name_list_given_as_a_bare_string_is_refused():
    """``"radar-composite"`` iterates into twelve single characters, and every
    one of them would look like a valid name."""
    obj = wire.encode_capabilities(caps())
    obj["observation_names"] = "radar-composite"
    with pytest.raises(WireError, match="not a string"):
        wire.decode_capabilities(obj)


def test_units_that_are_not_a_mapping_are_refused():
    obj = wire.encode_capabilities(caps())
    obj["units"] = ["dBZ"]
    with pytest.raises(WireError, match="units must be an object"):
        wire.decode_capabilities(obj)


# ======================================================================
# errors
# ======================================================================
def test_an_error_round_trips():
    assert wire.decode_error(wire.encode_error("off-cadence", "not on the cadence")) == (
        "off-cadence", "not on the cadence",
    )


def test_an_error_that_is_just_text_is_still_readable():
    assert wire.decode_error("boom") == (wire.INTERNAL, "boom")
