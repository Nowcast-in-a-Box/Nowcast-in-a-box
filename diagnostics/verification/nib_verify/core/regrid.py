"""Spatial alignment.

The provider hands us a field on *its* grid.  The config declares the grid the
metrics are computed on.  This module is the only place that bridges the two.

Two rules, both deliberate:

* Missing points are set to NaN **before** interpolation, so a missing source
  point can never be read as a value.  The interpolation then propagates NaN,
  and any target point that touches a missing source point stays missing.
  Conservative, and it never invents a number.
* Nothing here touches time.  Temporal alignment is exact equality on the
  cadence, enforced upstream, because the cadence is part of the ruler.
"""

from __future__ import annotations

import numpy as np

from ..access.base import Coords, Field


def target_coords(
    extent: tuple[float, float, float, float], resolution: float
) -> Coords:
    """Regular grid over ``(lon0, lat0, lon1, lat1)`` at ``resolution``."""
    lon0, lat0, lon1, lat1 = extent
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    if lon1 < lon0 or lat1 < lat0:
        raise ValueError("extent must be (lon0, lat0, lon1, lat1) with lon1 >= lon0")
    lon = _axis(lon0, lon1, resolution)
    lat = _axis(lat0, lat1, resolution)
    return Coords(lon=lon, lat=lat)


def _axis(a: float, b: float, step: float) -> np.ndarray:
    n = int(round((b - a) / step)) + 1
    if n < 2:
        n = 2
    return a + step * np.arange(n)


def grid_shape(
    extent: tuple[float, float, float, float], resolution: float
) -> tuple[int, int]:
    """``(ny, nx)`` a config's extent and resolution produce."""
    return target_coords(extent, resolution).shape


def regrid(field: Field, target: Coords) -> Field:
    """Resample ``field`` onto ``target``, bilinearly, conservative on missing."""
    src_lon = field.coords.lon
    src_lat = field.coords.lat

    # identical grid: copy straight through, no interpolation artefacts
    if src_lon.shape == target.lon.shape and src_lat.shape == target.lat.shape:
        if np.array_equal(src_lon, target.lon) and np.array_equal(src_lat, target.lat):
            return Field(
                data=field.data.copy(),
                coords=target,
                valid_time=field.valid_time,
                unit=field.unit,
                variable=field.variable,
                name=field.name,
                mask=None if field.mask is None else field.mask.copy(),
            )

    values = field.values()  # missing -> NaN
    tmp = _interp_1d(src_lon, target.lon, values, axis=1)
    out = _interp_1d(src_lat, target.lat, tmp, axis=0)

    _, lon_ok = nearest_index(src_lon, target.lon)
    _, lat_ok = nearest_index(src_lat, target.lat)
    outside = ~(lat_ok[:, None] & lon_ok[None, :])

    mask = outside | np.isnan(out)
    out = np.where(mask, np.nan, out)

    return Field(
        data=out,
        coords=target,
        valid_time=field.valid_time,
        unit=field.unit,
        variable=field.variable,
        name=field.name,
        mask=mask,
    )


def _interp_1d(
    src: np.ndarray, dst: np.ndarray, arr: np.ndarray, axis: int
) -> np.ndarray:
    """Linear interpolation along one axis.  Clamps outside the source span."""
    out_shape = list(arr.shape)
    out_shape[axis] = dst.size
    out = np.empty(out_shape, dtype=float)
    if axis == 1:
        for i in range(arr.shape[0]):
            out[i] = np.interp(dst, src, arr[i])
    elif axis == 0:
        for j in range(arr.shape[1]):
            out[:, j] = np.interp(dst, src, arr[:, j])
    else:
        raise ValueError("axis must be 0 or 1")
    return out


def nearest_index(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Nearest source indices for ``dst``, plus whether each is in range."""
    idx = np.searchsorted(src, dst).clip(1, src.size - 1)
    left = src[idx - 1]
    right = src[idx]
    take_left = np.abs(dst - left) <= np.abs(right - dst)
    idx = np.where(take_left, idx - 1, idx)
    in_range = (dst >= src[0]) & (dst <= src[-1])
    return idx, in_range


def align_pair(
    truth: Field, prediction: Field, target: Coords
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Regrid both fields and return ``(truth, prediction, valid)``.

    ``valid`` is the intersection of the two valid masks, so a point is used
    only when both sides have a real value there.
    """
    if truth.valid_time != prediction.valid_time:
        raise ValueError(
            "truth and prediction are at different valid times: "
            f"{truth.valid_time.isoformat()} vs {prediction.valid_time.isoformat()}"
        )
    if truth.unit != prediction.unit:
        raise ValueError(
            f"unit mismatch: truth is {truth.unit!r}, prediction is "
            f"{prediction.unit!r}; thresholds would be meaningless"
        )

    t = regrid(truth, target)
    p = regrid(prediction, target)
    valid = t.valid & p.valid
    return t.values(), p.values(), valid
