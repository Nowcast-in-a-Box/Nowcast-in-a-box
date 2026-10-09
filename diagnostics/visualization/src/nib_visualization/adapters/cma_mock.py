"""CMA CREF observations explicitly mapped to a mock Forecast for visualization.

No forecast is inferred. Valid times remain observation times; init is the
first observation and leads are their actual offsets. Source NPZ values stay
unchanged. Optional nearest-neighbour spatial sampling is for display only;
raw values, including zero and undecoded special codes, are retained at the
selected cells. A renderer display threshold may hide them without decoding.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr
from pyproj import CRS

from nib_visualization.field import VisualizationField, validate_field


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_cma_mock(
    directory: Path | str, *, crs: str, display_width: int = 1000,
) -> VisualizationField:
    """Read all input times, with a bounded regular display grid and no time subsampling.

    ``crs`` is an explicit integration mapping: the NPZ itself has no CRS field.
    This adapter supports the already-inspected WGS84 CMA lat/lon sample only.
    """
    if not CRS.from_user_input(crs).equals(CRS.from_epsg(4326)):
        raise ValueError("CMA mock mapping requires the explicit EPSG:4326 CRS")
    if display_width < 2:
        raise ValueError("display_width must be >=2")
    entries = []
    for path in Path(directory).glob("*.npz"):
        with np.load(path, allow_pickle=False) as source:
            meta = {key: source[key].item() for key in (
                "data_time", "generation_time", "source_file", "source", "product", "unit"
            )}
        if (meta["source"], meta["product"], meta["unit"]) != ("cma_radar", "CREF", "dBZ"):
            raise ValueError(f"unexpected CMA product metadata: {path}")
        observed = datetime.fromisoformat(meta["data_time"])
        if observed.tzinfo is None:
            raise ValueError("observation data_time must include its UTC timezone")
        timestamp = np.datetime64(observed.astimezone(UTC).replace(tzinfo=None), "s")
        entries.append((timestamp, path, meta))
    entries.sort(key=lambda entry: entry[0])
    if not entries:
        raise ValueError(f"no CMA observation NPZs in {directory}")
    times = np.array([entry[0] for entry in entries])
    if len(np.unique(times)) != len(times):
        raise ValueError("duplicate observation data_time")

    records, reference = [], None
    for index, (_time, path, meta) in enumerate(entries):
        before = _sha256(path)
        with np.load(path, allow_pickle=False) as source:
            raw = source["reflectivity"]
            lat, lon = source["latitude"], source["longitude"]
            resolution, bbox = float(source["resolution_deg"]), source["bbox"]
        if lat.ndim != 1 or lon.ndim != 1 or raw.shape != (lat.size, lon.size):
            raise ValueError(f"unaligned CMA grid: {path}")
        if min(raw.shape) < 2 or not np.isfinite(lat).all() or not np.isfinite(lon).all():
            raise ValueError(f"invalid CMA grid: {path}")
        if resolution <= 0 or not np.allclose(np.diff(lat), -resolution) or not np.allclose(
            np.diff(lon), resolution
        ):
            raise ValueError(f"CMA grid must be regular north-to-south / west-to-east: {path}")
        expected_bbox = [lon[0], lat[-1] - resolution, lon[-1] + resolution, lat[0]]
        if not np.allclose(bbox, expected_bbox, rtol=0, atol=1e-5):
            raise ValueError(f"CMA coordinate/bbox mismatch: {path}")
        if reference is None:
            reference = lat.copy(), lon.copy()
            ny, nx = raw.shape
            out_x = min(nx, display_width)
            out_y = min(ny, max(2, round(ny * out_x / nx)))
            rows = np.floor((np.arange(out_y) + 0.5) * ny / out_y).astype(int)
            cols = np.floor((np.arange(out_x) + 0.5) * nx / out_x).astype(int)
            dx, dy = lon[1] - lon[0], lat[1] - lat[0]
            x = lon[0] - dx / 2 + (np.arange(out_x) + 0.5) * dx * nx / out_x
            y = lat[0] - dy / 2 + (np.arange(out_y) + 0.5) * dy * ny / out_y
            values = np.empty((len(entries), out_y, out_x), dtype=raw.dtype)
        elif not np.array_equal(lat, reference[0]) or not np.array_equal(lon, reference[1]):
            raise ValueError(f"CMA grid changed between frames: {path}")
        if raw.dtype != values.dtype:
            raise ValueError(f"CMA value dtype changed between frames: {path}")
        values[index] = raw[np.ix_(rows, cols)]
        if _sha256(path) != before:
            raise ValueError(f"input changed during reading: {path}")
        records.append({"path": str(path.resolve()), "sha256": before, **meta})

    field = VisualizationField(
        values=xr.DataArray(
            values, dims=("lead_time", "y", "x"),
            coords={"lead_time": times - times[0], "y": y, "x": x},
            name="Reflectivity", attrs={"units": "dBZ"},
        ),
        crs=crs, init_time=times[0], valid_time=times, temporal_statistic="instantaneous",
        provenance={
            "mock": True, "synthetic": False, "is_real_forecast": False,
            "source_semantics": "CMA CREF observations replayed as pseudo forecast leads",
            "time_mapping": (
                "demo init = first observation; valid = observation time; lead = offset"
            ),
            "crs_mapping": f"{crs} explicitly supplied by the integration caller",
            "native_shape": [ny, nx], "display_shape": [out_y, out_x],
            "spatial_sampling": (
                "nearest source cell at regular display-grid centres, preserving native cell-edge "
                "footprint; no temporal sampling; display-only, not a scientific regridded product"
            ),
            "special_codes": "retained undecoded; display threshold is not quality control",
            "input_files": records, "input_files_unchanged": True,
        },
    )
    return validate_field(field)
