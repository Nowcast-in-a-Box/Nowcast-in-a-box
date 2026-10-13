"""ETOPO NetCDF adapter for the generic raster-basemap boundary.

Only this adapter knows the source format and elevation palette. It reads a
viewport once during scene preparation, using nearest source cells for the
finite display grid. No source file or scientific field is modified.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import netCDF4
import numpy as np
from pyproj import CRS

from nib_visualization.basemap import RasterBasemapView, _validate_extent

ETOPO_COLORS = (
    (-10000, "#173955"), (-6000, "#32678a"), (-3000, "#75a5bc"),
    (-1, "#d8eaf0"), (0, "#cad9b5"), (500, "#d5dbb5"),
    (1500, "#e4d3ae"), (3000, "#c2a580"), (5000, "#8d8070"),
    (6500, "#c5c0b6"), (8500, "#faf8f1"),
)


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _covering_slice(axis, low, high):
    step = float(axis[1] - axis[0])
    edge = float(axis[0] - step / 2)
    start = max(0, math.floor((low - edge) / step + 1e-8))
    stop = min(axis.size, math.ceil((high - edge) / step - 1e-8))
    if start >= stop:
        raise ValueError("terrain does not intersect the requested viewport")
    return start, stop


@dataclass
class EtopoTerrain:
    path: Path
    latitude: np.ndarray
    longitude: np.ndarray
    attribution: str
    disclaimer: str
    metadata: dict
    max_width: int
    alpha: float

    def crop(self, extent_wgs84) -> RasterBasemapView:
        west, east, south, north = _validate_extent(extent_wgs84)
        y0, y1 = _covering_slice(self.latitude, south, north)
        x0, x1 = _covering_slice(self.longitude, west, east)
        width = min(self.max_width, x1 - x0)
        height = max(1, round((y1 - y0) * width / (x1 - x0)))
        rows = y0 + np.floor((np.arange(height) + 0.5) * (y1 - y0) / height).astype(int)
        cols = x0 + np.floor((np.arange(width) + 0.5) * (x1 - x0) / width).astype(int)
        with netCDF4.Dataset(self.path) as dataset:
            # netCDF4 uses orthogonal indexing: the outer product of rows/cols.
            values = np.ma.filled(dataset["z"][rows, cols], np.nan).astype("f4")
        dx = float(self.longitude[1] - self.longitude[0])
        dy = float(self.latitude[1] - self.latitude[0])
        extent = (
            float(self.longitude[x0] - dx / 2), float(self.longitude[x1 - 1] + dx / 2),
            float(self.latitude[y0] - dy / 2), float(self.latitude[y1 - 1] + dy / 2),
        )
        return RasterBasemapView(values, extent, ETOPO_COLORS, self.alpha)


def load_etopo(
    path: Path | str, source_record: Path | str, *, expected_sha256: str,
    max_width: int = 1200, alpha: float = 0.56,
) -> EtopoTerrain:
    """Verify a local ETOPO global file or native crop and open its coordinate metadata."""
    path, source_record = Path(path), Path(source_record)
    if max_width < 2 or not 0 <= alpha <= 1:
        raise ValueError("terrain max_width must be >=2 and alpha must be between 0 and 1")
    actual = _sha256(path)
    if actual != expected_sha256:
        raise ValueError(f"terrain checksum mismatch: {path}")
    record = json.loads(source_record.read_text())
    if not CRS.from_user_input(record["horizontal_crs"]).equals(CRS.from_epsg(4326)):
        raise ValueError("ETOPO adapter requires EPSG:4326")
    with netCDF4.Dataset(path) as ds:
        lat, lon = np.asarray(ds["lat"][:]), np.asarray(ds["lon"][:])
        for axis in (lat, lon):
            if axis.ndim != 1 or len(axis) < 2 or not np.isfinite(axis).all():
                raise ValueError("terrain requires finite 1D coordinate axes")
            step = axis[1] - axis[0]
            if step <= 0 or not np.allclose(np.diff(axis), step, rtol=0, atol=1e-9):
                raise ValueError("terrain axes must be regular and ascending")
        if ds["z"].dimensions != ("lat", "lon") or ds["z"].shape != (len(lat), len(lon)):
            raise ValueError("terrain values must align with lat/lon")
        if ds["lat"].units != "degrees_north" or ds["lon"].units != "degrees_east":
            raise ValueError("terrain coordinate units must be geographic degrees")
        if ds["z"].units not in ("meters", "m"):
            raise ValueError("terrain elevation units must be meters")
        grid_crs = CRS.from_user_input(ds[ds["z"].grid_mapping].spatial_ref)
        if not grid_crs.equals(CRS.from_epsg(4326)):
            raise ValueError("terrain file CRS does not match its WGS84 source record")
    return EtopoTerrain(
        path, lat, lon, record["citation"], record["disclaimer"],
        {
            "source_id": record["source_id"], "path": str(path.resolve()), "sha256": actual,
            "source_record": {
                "path": str(source_record.resolve()), "sha256": _sha256(source_record),
            },
            "horizontal_crs": "EPSG:4326", "vertical_crs": record["vertical_crs"],
            "units": "m", "native_shape": [len(lat), len(lon)],
            "max_display_width": max_width, "alpha": alpha,
            "sampling": "nearest native cell at each regular display-pixel centre; display only",
            "attribution": record["citation"], "disclaimer": record["disclaimer"],
        },
        max_width, alpha,
    )
