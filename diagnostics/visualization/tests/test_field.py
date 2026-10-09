"""VIS-002 field contract tests: validation semantics and single-frame access.

Behavior groups (see doc_log/plans/visualization-mvp-tasks/2026-09-21-vis-002-field-contract.md):

- F1 valid representative inputs (lat/lon singleton lead, projected multi-lead)
- F2 shape / grid / CRS / units failures, one mutation from one valid baseline
- F3 time and interval semantics
- F4 partial NaN vs all-NaN frames

All inputs are tiny in-file DataArrays (2 y cells x 3 x cells, few leads);
no basemap, real forecast data or VIS-003 fixtures are involved. Expected
values are constructed explicitly in this file; the validator under test
never generates its own expectations. Error-code strings are asserted as
literals from the shared contract, not from implementation constants.
"""

from __future__ import annotations

import copy

import numpy as np
import pytest
import xarray as xr
from pyproj import CRS

from nib_visualization.field import (
    FieldValidationError,
    VisualizationField,
    frame_at,
    validate_field,
)

INIT = np.datetime64("2026-09-22T00:00:00", "ns")
Y_LAT = np.array([47.0, 48.0])
X_LON = np.array([5.0, 6.0, 7.0])
LEADS_3H = np.array([0, 3600, 7200], dtype="timedelta64[s]")
LATLON_DATA = np.array(
    [
        [[0.0, 1.0, 2.0], [3.0, np.nan, 5.0]],
        [[6.0, 7.0, 8.0], [9.0, 10.0, 11.0]],
        [[12.0, 13.0, 14.0], [15.0, 16.0, 17.0]],
    ]
)
EXPECTED_VALID_3H = np.array(
    ["2026-09-22T00:00:00", "2026-09-22T01:00:00", "2026-09-22T02:00:00"],
    dtype="datetime64[ns]",
)

# Projected (UTM 33N) multi-lead baseline: descending y, accumulation statistic.
Y_UTM = np.array([5.3e6, 5.2e6])
X_UTM = np.array([350000.0, 360000.0, 370000.0])
PROJ_DATA = np.array(
    [
        [[0.5, 1.5, np.nan], [2.5, 3.5, 4.5]],
        [[5.5, 6.5, 7.5], [8.5, 9.5, 10.5]],
        [[11.5, 12.5, 13.5], [14.5, 15.5, 16.5]],
    ]
)
ACC_START = EXPECTED_VALID_3H - np.timedelta64(3600, "s")
ACC_END = EXPECTED_VALID_3H.copy()


def make_latlon_field(
    *,
    lead_time=LEADS_3H,
    data=LATLON_DATA,
    name="2t",
    units="K",
    crs="EPSG:4326",
    init_time=INIT,
    valid_time=None,
    temporal_statistic="instantaneous",
    interval_start=None,
    interval_end=None,
    provenance=None,
    values=None,
):
    """Build a VisualizationField around an in-file lat/lon DataArray.

    ``values`` overrides the whole DataArray (used for structural mutations);
    ``units=None`` builds the array without a units attr at all.
    """
    if values is None:
        attrs = {"units": units} if units is not None else {}
        values = xr.DataArray(
            data,
            dims=("lead_time", "y", "x"),
            coords={"lead_time": lead_time, "y": Y_LAT, "x": X_LON},
            name=name,
            attrs=attrs,
        )
    return VisualizationField(
        values=values,
        crs=crs,
        init_time=init_time,
        valid_time=valid_time,
        temporal_statistic=temporal_statistic,
        interval_start=interval_start,
        interval_end=interval_end,
        provenance=provenance,
    )


def make_projected_field(
    *,
    valid_time=None,
    temporal_statistic="accumulation",
    interval_start=ACC_START,
    interval_end=ACC_END,
    init_time=INIT,
    lead_time=LEADS_3H,
):
    values = xr.DataArray(
        PROJ_DATA,
        dims=("lead_time", "y", "x"),
        coords={"lead_time": lead_time, "y": Y_UTM, "x": X_UTM},
        name="tp",
        attrs={"units": "kg m-2"},
    )
    return VisualizationField(
        values=values,
        crs="EPSG:32633",
        init_time=init_time,
        valid_time=valid_time,
        temporal_statistic=temporal_statistic,
        interval_start=interval_start,
        interval_end=interval_end,
    )


# --------------------------------------------------------------------------
# F1 - valid representative inputs
# --------------------------------------------------------------------------


def test_f1_latlon_singleton_lead_valid_and_derives_valid_time():
    field = make_latlon_field(
        lead_time=np.array([0], dtype="timedelta64[s]"),
        data=np.array([[[0.0, 1.0, 2.0], [3.0, np.nan, 5.0]]]),
    )
    validated = validate_field(field)

    # valid_time derived deterministically as init + lead (explicit oracle)
    np.testing.assert_array_equal(
        validated.valid_time, np.array(["2026-09-22T00:00:00"], dtype="datetime64[ns]")
    )
    # singleton lead dimension is kept in the source array
    assert validated.values.dims == ("lead_time", "y", "x")
    assert validated.values.sizes["lead_time"] == 1
    # name/units come from the DataArray, valid 0 and partial NaN preserved
    assert validated.name == "2t"
    assert validated.units == "K"
    assert validated.values.values[0, 0, 0] == 0.0
    assert np.isnan(validated.values.values[0, 1, 1])
    assert validated.provenance is None
    assert isinstance(validated.crs, CRS)
    assert validated.crs.to_epsg() == 4326
    # the caller's field is not rewritten: its valid_time stays absent
    assert field.valid_time is None


def test_f1_projected_multi_lead_with_explicit_valid_time():
    field = make_projected_field(valid_time=EXPECTED_VALID_3H.copy())
    validated = validate_field(field)

    np.testing.assert_array_equal(validated.valid_time, EXPECTED_VALID_3H)
    assert validated.temporal_statistic == "accumulation"
    np.testing.assert_array_equal(validated.interval_start, ACC_START)
    np.testing.assert_array_equal(validated.interval_end, ACC_END)
    # descending y and projected CRS are both acceptable
    np.testing.assert_array_equal(validated.values.coords["y"].values, Y_UTM)
    assert isinstance(validated.crs, CRS)
    assert validated.crs.to_epsg() == 32633
    assert validated.units == "kg m-2"


def test_f1_frame_at_selects_lead_values_and_time_semantics():
    field = make_latlon_field()
    validated = validate_field(field)

    frame = frame_at(validated, 1)

    assert frame.dims == ("y", "x")
    assert frame.shape == (2, 3)
    np.testing.assert_array_equal(frame.values, LATLON_DATA[1])
    assert frame.name == "2t"
    assert frame.attrs["units"] == "K"
    np.testing.assert_array_equal(frame.coords["y"].values, Y_LAT)
    np.testing.assert_array_equal(frame.coords["x"].values, X_LON)
    assert frame.coords["lead_time"].values == np.timedelta64(3600, "s")
    assert frame.coords["valid_time"].values == np.datetime64("2026-09-22T01:00:00", "ns")
    # the frame is a view of the source sequence, not a copied lead stack
    assert np.shares_memory(frame.values, validated.values.values)


def test_f1_source_unchanged_after_validation_and_frame_access():
    field = make_latlon_field(valid_time=EXPECTED_VALID_3H.copy())
    values_snapshot = copy.deepcopy(field.values)
    valid_time_snapshot = field.valid_time.copy()

    validated = validate_field(field)
    frame_at(validated, 2)

    xr.testing.assert_identical(field.values, values_snapshot)
    np.testing.assert_array_equal(field.valid_time, valid_time_snapshot)
    assert field.values.dims == ("lead_time", "y", "x")
    assert field.values.shape == (3, 2, 3)


# --------------------------------------------------------------------------
# F2 - shape / grid / CRS / units failures (one mutation per case)
# --------------------------------------------------------------------------


def _latlon_values(coords, data=LATLON_DATA, name="2t", attrs=None):
    return xr.DataArray(
        data,
        dims=("lead_time", "y", "x"),
        coords=coords,
        name=name,
        attrs={"units": "K"} if attrs is None else attrs,
    )


def _case_2d_values():
    values = xr.DataArray(
        LATLON_DATA[0],
        dims=("y", "x"),
        coords={"y": Y_LAT, "x": X_LON},
        name="2t",
        attrs={"units": "K"},
    )
    return make_latlon_field(values=values)


def _case_dim_order_wrong():
    values = xr.DataArray(
        LATLON_DATA,
        dims=("x", "y", "lead_time"),
        coords={"lead_time": LEADS_3H, "y": Y_LAT, "x": X_LON},
        name="2t",
        attrs={"units": "K"},
    )
    return make_latlon_field(values=values)


def _case_dim_name_wrong():
    values = xr.DataArray(
        LATLON_DATA,
        dims=("time", "y", "x"),
        coords={"y": Y_LAT, "x": X_LON},
        name="2t",
        attrs={"units": "K"},
    )
    return make_latlon_field(values=values)


def _case_y_single_cell():
    values = _latlon_values(
        {"lead_time": LEADS_3H, "y": np.array([47.0]), "x": X_LON},
        data=np.zeros((3, 1, 3)),
    )
    return make_latlon_field(values=values)


F2_CASES = [
    pytest.param("INVALID_SHAPE", "values", _case_2d_values, id="wrong_rank_2d"),
    pytest.param("INVALID_SHAPE", "values", _case_dim_order_wrong, id="dim_order_wrong"),
    pytest.param("INVALID_SHAPE", "values", _case_dim_name_wrong, id="dim_name_wrong"),
    pytest.param("INVALID_SHAPE", "name", lambda: make_latlon_field(name=None), id="name_missing"),
    pytest.param("INVALID_SHAPE", "name", lambda: make_latlon_field(name=""), id="name_empty"),
    pytest.param(
        "INVALID_SHAPE", "name", lambda: make_latlon_field(name="  "), id="name_whitespace"
    ),
    pytest.param(
        "INVALID_SHAPE", "values", lambda: make_latlon_field(data=LATLON_DATA.astype("U8")),
        id="non_numeric_dtype",
    ),
    pytest.param("INVALID_SHAPE", "values", _case_y_single_cell, id="y_axis_single_cell"),
    pytest.param(
        "INVALID_GRID",
        "x",
        lambda: make_latlon_field(
            values=_latlon_values(
                {"lead_time": LEADS_3H, "y": Y_LAT, "x": np.array([5.0, 5.5, 7.0])}
            )
        ),
        id="x_non_regular",
    ),
    pytest.param(
        "INVALID_GRID",
        "x",
        lambda: make_latlon_field(
            values=_latlon_values(
                {"lead_time": LEADS_3H, "y": Y_LAT, "x": np.array([5.0, 5.0, 7.0])}
            )
        ),
        id="x_duplicate",
    ),
    pytest.param(
        "INVALID_GRID",
        "x",
        lambda: make_latlon_field(
            values=_latlon_values(
                {"lead_time": LEADS_3H, "y": Y_LAT, "x": np.array([5.0, np.nan, 7.0])}
            )
        ),
        id="x_non_finite",
    ),
    pytest.param(
        "INVALID_GRID",
        "x",
        lambda: make_latlon_field(
            values=_latlon_values(
                {
                    "lead_time": LEADS_3H,
                    "y": Y_LAT,
                    "x": (("y", "x"), np.tile(X_LON, (2, 1))),
                }
            )
        ),
        id="x_curvilinear_2d",
    ),
    pytest.param(
        "INVALID_GRID",
        "y",
        lambda: make_latlon_field(values=_latlon_values({"lead_time": LEADS_3H, "x": X_LON})),
        id="y_coord_missing",
    ),
    pytest.param("INVALID_CRS", "crs", lambda: make_latlon_field(crs=None), id="crs_missing"),
    pytest.param(
        "INVALID_CRS", "crs", lambda: make_latlon_field(crs="NOT_A_CRS"), id="crs_unparseable"
    ),
    pytest.param(
        "MISSING_UNITS", "units", lambda: make_latlon_field(units=None), id="units_attr_missing"
    ),
    pytest.param("MISSING_UNITS", "units", lambda: make_latlon_field(units=""), id="units_empty"),
]


@pytest.mark.parametrize(("expected_code", "expected_field", "build_field"), F2_CASES)
def test_f2_rejects_invalid_inputs(expected_code, expected_field, build_field):
    with pytest.raises(FieldValidationError) as exc_info:
        validate_field(build_field())
    err = exc_info.value
    assert err.code == expected_code
    assert err.field == expected_field
    assert err.message


# --------------------------------------------------------------------------
# F3 - time and interval semantics
# --------------------------------------------------------------------------


def test_f3_accumulation_window_passes_on_projected_field():
    validated = validate_field(make_projected_field())
    np.testing.assert_array_equal(validated.valid_time, EXPECTED_VALID_3H)
    np.testing.assert_array_equal(validated.interval_end, validated.valid_time)


def test_f3_mean_window_passes_on_latlon_field():
    field = make_latlon_field(
        temporal_statistic="mean",
        interval_start=ACC_START,
        interval_end=ACC_END,
        name="t2m",
        units="K",
    )
    validated = validate_field(field)
    assert validated.temporal_statistic == "mean"
    np.testing.assert_array_equal(validated.interval_start, ACC_START)


F3_TIME_CASES = [
    pytest.param(
        "INVALID_TIME",
        "lead_time",
        lambda: make_latlon_field(
            lead_time=np.array([-3600, 0], dtype="timedelta64[s]"),
            data=np.zeros((2, 2, 3)),
        ),
        id="lead_negative",
    ),
    pytest.param(
        "INVALID_TIME",
        "lead_time",
        lambda: make_latlon_field(
            lead_time=np.array([7200, 3600, 0], dtype="timedelta64[s]"),
        ),
        id="lead_unordered",
    ),
    pytest.param(
        "INVALID_TIME",
        "lead_time",
        lambda: make_latlon_field(
            lead_time=np.array([0, 3600, 3600], dtype="timedelta64[s]"),
        ),
        id="lead_duplicate",
    ),
    pytest.param(
        "INVALID_TIME",
        "lead_time",
        lambda: make_latlon_field(
            lead_time=np.array(
                ["2026-09-22T00", "2026-09-22T01", "2026-09-22T02"], dtype="datetime64[s]"
            ),
        ),
        id="lead_wrong_dtype",
    ),
    pytest.param(
        "INVALID_TIME",
        "lead_time",
        lambda: make_latlon_field(
            values=xr.DataArray(
                LATLON_DATA,
                dims=("lead_time", "y", "x"),
                coords={"y": Y_LAT, "x": X_LON},
                name="2t",
                attrs={"units": "K"},
            )
        ),
        id="lead_coord_missing",
    ),
    pytest.param(
        "INVALID_TIME",
        "init_time",
        lambda: make_latlon_field(init_time="2026-09-22T00:00:00"),
        id="init_not_datetime64",
    ),
    pytest.param(
        "INVALID_TIME",
        "init_time",
        lambda: make_latlon_field(init_time=np.datetime64("NaT", "ns")),
        id="init_nat",
    ),
    pytest.param(
        "INVALID_TIME",
        "valid_time",
        lambda: make_latlon_field(valid_time=EXPECTED_VALID_3H + np.timedelta64(3600, "s")),
        id="valid_time_contradicts",
    ),
    pytest.param(
        "INVALID_TIME",
        "valid_time",
        lambda: make_latlon_field(valid_time=EXPECTED_VALID_3H[:2]),
        id="valid_time_wrong_length",
    ),
    pytest.param(
        "INVALID_TIME",
        "valid_time",
        lambda: make_latlon_field(valid_time=EXPECTED_VALID_3H.reshape(3, 1)),
        id="valid_time_not_1d",
    ),
    pytest.param(
        "INVALID_TIME",
        "valid_time",
        lambda: make_latlon_field(valid_time=np.array([0, 1, 2])),
        id="valid_time_wrong_dtype",
    ),
]


@pytest.mark.parametrize(("expected_code", "expected_field", "build_field"), F3_TIME_CASES)
def test_f3_rejects_invalid_time_semantics(expected_code, expected_field, build_field):
    with pytest.raises(FieldValidationError) as exc_info:
        validate_field(build_field())
    err = exc_info.value
    assert err.code == expected_code
    assert err.field == expected_field
    assert err.message


F3_INTERVAL_CASES = [
    pytest.param(
        "INVALID_INTERVAL",
        "interval_start",
        lambda: make_projected_field(interval_start=None),
        id="interval_start_missing",
    ),
    pytest.param(
        "INVALID_INTERVAL",
        "interval_end",
        lambda: make_projected_field(interval_end=None),
        id="interval_end_missing",
    ),
    pytest.param(
        "INVALID_INTERVAL",
        "interval_start",
        lambda: make_projected_field(interval_start=ACC_START[:2]),
        id="interval_length_mismatch",
    ),
    pytest.param(
        "INVALID_INTERVAL",
        "interval_start",
        lambda: make_projected_field(
            interval_start=ACC_END.copy(), interval_end=ACC_END.copy()
        ),
        id="interval_start_not_before_end",
    ),
    pytest.param(
        "INVALID_INTERVAL",
        "interval_end",
        lambda: make_projected_field(interval_end=ACC_END + np.timedelta64(60, "s")),
        id="interval_end_not_valid_time",
    ),
    pytest.param(
        "INVALID_INTERVAL",
        "temporal_statistic",
        lambda: make_projected_field(temporal_statistic="sum"),
        id="statistic_unknown",
    ),
]


@pytest.mark.parametrize(("expected_code", "expected_field", "build_field"), F3_INTERVAL_CASES)
def test_f3_rejects_invalid_interval_semantics(expected_code, expected_field, build_field):
    with pytest.raises(FieldValidationError) as exc_info:
        validate_field(build_field())
    err = exc_info.value
    assert err.code == expected_code
    assert err.field == expected_field
    assert err.message


# --------------------------------------------------------------------------
# F4 - missing data semantics
# --------------------------------------------------------------------------


def test_f4_partial_nan_passes_and_valid_zero_is_preserved():
    validated = validate_field(make_latlon_field())
    # the NaN at (lead 0, y 1, x 1) stays NaN; the valid 0 at (0, 0, 0) stays 0
    assert np.isnan(validated.values.values[0, 1, 1])
    assert validated.values.values[0, 0, 0] == 0.0
    np.testing.assert_array_equal(np.isnan(validated.values.values), np.isnan(LATLON_DATA))


def test_f4_all_nan_lead_frame_is_rejected():
    data = LATLON_DATA.copy()
    data[1, :, :] = np.nan
    field = make_latlon_field(data=data)
    with pytest.raises(FieldValidationError) as exc_info:
        validate_field(field)
    err = exc_info.value
    assert err.code == "EMPTY_FRAME"
    assert err.field == "values[lead_index=1]"


def test_f4_all_nan_singleton_field_is_rejected():
    field = make_latlon_field(
        lead_time=np.array([0], dtype="timedelta64[s]"),
        data=np.full((1, 2, 3), np.nan),
    )
    with pytest.raises(FieldValidationError) as exc_info:
        validate_field(field)
    assert exc_info.value.code == "EMPTY_FRAME"
    assert exc_info.value.field == "values[lead_index=0]"


# --------------------------------------------------------------------------
# Independent-review fix regressions
# (doc_log/tech_log/20260922_vis002_independent_review.md F-001/F-002)
# - F-001: frame_at index boundary, valid-index semantics unchanged
# - F-002: representative float32 regular decimal coordinates
# --------------------------------------------------------------------------


def test_review_f001_frame_at_rejects_negative_and_overflow_lead_index():
    field = validate_field(make_latlon_field())

    # -1 must not silently select the last lead; n_leads is equally out of range
    with pytest.raises(IndexError):
        frame_at(field, -1)
    with pytest.raises(IndexError):
        frame_at(field, 3)


def test_review_f001_boundary_indices_keep_view_semantics_and_source_unchanged():
    field = validate_field(make_latlon_field())
    snapshot = copy.deepcopy(field.values)

    first = frame_at(field, 0)
    last = frame_at(field, 2)

    np.testing.assert_array_equal(first.values, LATLON_DATA[0])
    np.testing.assert_array_equal(last.values, LATLON_DATA[2])
    assert first.coords["lead_time"].values == np.timedelta64(0, "s")
    assert last.coords["valid_time"].values == np.datetime64("2026-09-22T02:00:00", "ns")
    assert np.shares_memory(last.values, field.values.values)
    # a failed access must not modify the source any more than a successful one
    with pytest.raises(IndexError):
        frame_at(field, -1)
    xr.testing.assert_identical(field.values, snapshot)


def test_review_f001_contract_errors_take_priority_over_index_errors():
    invalid = make_latlon_field(crs=None)

    # validation runs before index handling; a broken field reports its
    # contract error instead of being masked by an IndexError
    with pytest.raises(FieldValidationError):
        frame_at(invalid, -1)


def test_review_f002_float32_ascending_regular_coordinates_are_accepted():
    x_float32 = np.array([47.0, 47.1, 47.2], dtype=np.float32)
    values = _latlon_values({"lead_time": LEADS_3H, "y": Y_LAT, "x": x_float32})

    validated = validate_field(make_latlon_field(values=values))

    # the stored coordinate is the oracle: not rounded, re-sorted or recast
    np.testing.assert_array_equal(validated.values.coords["x"].values, x_float32)
    assert validated.values.coords["x"].dtype == np.float32


def test_review_f002_float32_descending_regular_coordinates_are_accepted():
    x_desc_float32 = np.array([47.2, 47.1, 47.0], dtype=np.float32)
    values = _latlon_values({"lead_time": LEADS_3H, "y": Y_LAT, "x": x_desc_float32})

    validated = validate_field(make_latlon_field(values=values))

    # element order is pinned: a silently sorted coordinate would not match
    np.testing.assert_array_equal(validated.values.coords["x"].values, x_desc_float32)
    assert validated.values.coords["x"].dtype == np.float32


def test_review_f002_float32_clearly_irregular_coordinates_are_still_rejected():
    x_irregular = np.array([47.0, 47.1, 47.25], dtype=np.float32)
    values = _latlon_values({"lead_time": LEADS_3H, "y": Y_LAT, "x": x_irregular})

    with pytest.raises(FieldValidationError) as exc_info:
        validate_field(make_latlon_field(values=values))
    err = exc_info.value
    assert err.code == "INVALID_GRID"
    assert err.field == "x"
    assert err.message
