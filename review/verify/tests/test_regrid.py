"""Spatial alignment: the one place two grids meet."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from nib_verify.access.base import Coords, Field
from nib_verify.core.regrid import align_pair, grid_shape, nearest_index, regrid, target_coords

UTC = timezone.utc
T = datetime(2026, 1, 1, tzinfo=UTC)


def _coords(lon0, lon1, nx, lat0, lat1, ny) -> Coords:
    return Coords(lon=np.linspace(lon0, lon1, nx), lat=np.linspace(lat0, lat1, ny))


def _field(data, coords, mask=None, *, unit="dBZ", name="x") -> Field:
    return Field(data=data, coords=coords, valid_time=T, unit=unit,
                 variable="reflectivity", name=name, mask=mask)


# ----------------------------------------------------------------------
# target grid
# ----------------------------------------------------------------------
def test_target_coords_is_inclusive_of_both_edges():
    c = target_coords((110.0, 30.0, 120.0, 42.0), 1.0)
    assert c.lon.size == 11
    assert c.lat.size == 13
    assert c.lon[0] == pytest.approx(110.0)
    assert c.lon[-1] == pytest.approx(120.0)
    assert c.lat[-1] == pytest.approx(42.0)


def test_grid_shape_matches_resolution():
    assert grid_shape((110.0, 30.0, 120.0, 42.0), 0.5) == (25, 21)


def test_target_coords_rejects_a_bad_resolution():
    with pytest.raises(ValueError):
        target_coords((110.0, 30.0, 120.0, 42.0), 0.0)


def test_nearest_index_picks_the_closer_point():
    src = np.array([0.0, 1.0, 2.0])
    idx, in_range = nearest_index(src, np.array([-0.1, 0.4, 0.6, 1.9, 2.1]))
    assert list(idx) == [0, 0, 1, 2, 2]
    assert list(in_range) == [False, True, True, True, False]


# ----------------------------------------------------------------------
# regridding
# ----------------------------------------------------------------------
def test_identical_grid_is_copied_not_interpolated():
    coords = _coords(0.0, 3.0, 4, 0.0, 2.0, 3)
    data = np.arange(12, dtype=float).reshape(3, 4)
    out = regrid(_field(data, coords), coords)
    assert np.array_equal(out.data, data)
    assert out.mask is None


def test_linear_field_survives_resampling_exactly():
    src = _coords(0.0, 10.0, 11, 0.0, 10.0, 11)
    data = np.tile(src.lon, (11, 1))  # value == lon, linear
    out = regrid(_field(data, src), _coords(0.0, 10.0, 5, 0.0, 10.0, 5))
    assert out.coords.shape == (5, 5)
    expected = np.tile(np.linspace(0.0, 10.0, 5), (5, 1))
    assert np.allclose(out.data, expected)


def test_missing_is_never_turned_into_zero():
    src = _coords(0.0, 5.0, 6, 0.0, 5.0, 6)
    data = np.full((6, 6), 40.0)
    mask = np.zeros((6, 6), dtype=bool)
    mask[2:4, 2:4] = True
    out = regrid(_field(data, src, mask), _coords(0.0, 5.0, 11, 0.0, 5.0, 11))

    assert out.mask is not None
    assert out.mask.any(), "the hole must survive resampling"
    # every value is either a real 40 or missing — no fabricated zeros
    vals = out.data[~out.mask]
    assert np.allclose(vals, 40.0)
    assert np.all(np.isnan(out.data[out.mask]))


def test_points_outside_the_source_extent_are_missing():
    src = _coords(0.0, 4.0, 5, 0.0, 4.0, 5)
    data = np.ones((5, 5))
    out = regrid(_field(data, src), _coords(-2.0, 6.0, 9, -2.0, 6.0, 9))
    assert out.mask is not None
    assert out.mask[0, 0]
    assert out.mask[-1, -1]
    assert not out.mask[4, 4]
    assert np.all(np.isnan(out.data[out.mask]))


def test_values_carries_missing_as_nan_not_zero():
    coords = _coords(0.0, 2.0, 3, 0.0, 2.0, 3)
    mask = np.zeros((3, 3), dtype=bool)
    mask[1, 1] = True
    f = _field(np.zeros((3, 3)), coords, mask)
    v = f.values()
    assert np.isnan(v[1, 1])
    assert not np.isnan(v[0, 0])


# ----------------------------------------------------------------------
# pairing
# ----------------------------------------------------------------------
def test_align_pair_uses_the_intersection_of_valid_points():
    coords = _coords(0.0, 3.0, 4, 0.0, 3.0, 4)
    t_mask = np.zeros((4, 4), dtype=bool)
    t_mask[0, 0] = True
    p_mask = np.zeros((4, 4), dtype=bool)
    p_mask[3, 3] = True
    truth = _field(np.ones((4, 4)), coords, t_mask)
    pred = _field(np.ones((4, 4)), coords, p_mask)

    _, _, valid = align_pair(truth, pred, coords)
    assert valid.sum() == 14
    assert not valid[0, 0]
    assert not valid[3, 3]


def test_align_pair_refuses_mismatched_units():
    coords = _coords(0.0, 3.0, 4, 0.0, 3.0, 4)
    truth = _field(np.ones((4, 4)), coords, unit="dBZ")
    pred = _field(np.ones((4, 4)), coords, unit="mm h-1")
    with pytest.raises(ValueError, match="unit mismatch"):
        align_pair(truth, pred, coords)


def test_align_pair_refuses_mismatched_valid_times():
    coords = _coords(0.0, 3.0, 4, 0.0, 3.0, 4)
    truth = _field(np.ones((4, 4)), coords)
    pred = Field(data=np.ones((4, 4)), coords=coords,
                 valid_time=datetime(2026, 1, 1, 1, tzinfo=UTC),
                 unit="dBZ", variable="reflectivity", name="y")
    with pytest.raises(ValueError, match="different valid times"):
        align_pair(truth, pred, coords)


def test_field_rejects_shape_mismatch():
    coords = _coords(0.0, 3.0, 4, 0.0, 3.0, 4)
    with pytest.raises(ValueError):
        _field(np.ones((3, 4)), coords)


def test_coords_rejects_unsorted_axes():
    with pytest.raises(ValueError):
        Coords(lon=np.array([0.0, 2.0, 1.0]), lat=np.array([0.0, 1.0]))
