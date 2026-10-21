"""July CMA demo windows on a regional, metre-based 3 km grid."""

from datetime import timedelta
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer

from adapters.cma_radar.source import _discover_cref_files, read_cref
from ingestion.sevir import stamp, utc

CADENCE = 6
CONTEXT = 6
TARGET = 20
NOTICE = (
    "Cross-domain demo: SEVIR pretrained NowcastNet applied to CREF. "
    "0..75 dBZ mapped linearly to 0..255; 6 min runtime cadence replaces "
    "5 min training cadence. Forecast skill is not validated."
)


def grid_for(center_lon=118.0, center_lat=32.0):
    if not np.isfinite([center_lon, center_lat]).all() or not (
        76 <= center_lon <= 132 and 15 <= center_lat <= 51
    ):
        raise ValueError("Choose a regional center inside 76..132 E, 15..51 N")
    crs = CRS.from_proj4(
        f"+proj=laea +lat_0={center_lat} +lon_0={center_lon} +datum=WGS84 +units=m"
    )
    axis = (np.arange(128, dtype=np.float64) - 63.5) * 3000
    return {
        "crs": crs.to_wkt(),
        "x": axis.copy(),
        "y": axis.copy(),
        "axis_units": "m",
        "resolution_m": 3000,
        "center_lon": float(center_lon),
        "center_lat": float(center_lat),
        "source": "CMA CREF geographic grid -> regional LAEA; nearest source cell",
        "missing_policy": "raw -1280 no echo -> 0; raw <= -1000 otherwise masked; negative valid dBZ clipped to 0 for demo",
    }


def regrid(field, grid):
    xx, yy = np.meshgrid(grid["x"], grid["y"])
    lon, lat = Transformer.from_crs(grid["crs"], "EPSG:4326", always_xy=True).transform(
        xx, yy
    )
    longitude, latitude = field.longitude.values, field.latitude.values
    col = np.rint((lon - longitude[0]) / (longitude[1] - longitude[0])).astype(int)
    row = np.rint((lat - latitude[0]) / (latitude[1] - latitude[0])).astype(int)
    if (
        (row < 0).any()
        or (row >= len(latitude)).any()
        or (col < 0).any()
        or (col >= len(longitude)).any()
    ):
        raise ValueError("3 km regional grid extends outside this radar mosaic")
    values = np.asarray(field.values[row, col], dtype=np.float32)
    # CMARadar/mosaic_io.py: raw -1280 is no echo; <=-1000 are flags.
    no_echo = values == -128.0
    valid = (values > -100.0) | no_echo
    return np.where(valid, np.maximum(np.where(no_echo, 0, values), 0), np.nan).astype(
        np.float32
    )


def file_index(path):
    root = Path(path)
    if not root.is_dir():
        raise FileNotFoundError(
            "CMA data directory is unavailable; choose the cma-radar folder"
        )
    index = {}
    for item in _discover_cref_files(root):
        if item.data_time.strftime("%Y%m") != "202607":
            continue
        previous = index.get(item.data_time)
        if previous is None or item.generation_time > previous.generation_time:
            index[item.data_time] = item
    if not index:
        raise FileNotFoundError(
            "No July 2026 CREF files found under the selected directory"
        )
    return index


def window_times(start):
    first = utc(start)
    return [first + timedelta(minutes=CADENCE * i) for i in range(CONTEXT + TARGET)]


def _check(index, event_id, start):
    times = window_times(start)
    if event_id != times[0].strftime("%Y%m%d"):
        raise ValueError("CMA sample date must match the first context time")
    missing = [stamp(t) for t in times if t.replace(tzinfo=None) not in index]
    if missing:
        raise FileNotFoundError(
            "CMA window has missing July frames: " + ", ".join(missing)
        )
    return times


def check_window(path, event_id, start):
    times = _check(file_index(path), event_id, start)
    return {"event_id": event_id, "context_start_time": stamp(times[0]), "ready": True}


def list_samples(path, *, offset=0, limit=100, day=None):
    if offset < 0 or not 1 <= limit <= 500:
        raise ValueError("sample pagination requires offset>=0 and 1<=limit<=500")
    index = file_index(path)
    starts = [
        t
        for t in sorted(index)
        if all(
            t + timedelta(minutes=CADENCE * i) in index for i in range(CONTEXT + TARGET)
        )
    ]
    days = sorted({t.strftime("%Y%m%d") for t in starts})
    if day:
        starts = [t for t in starts if t.strftime("%Y%m%d") == day]
    samples = []
    for row, start in enumerate(starts[offset : offset + limit], start=offset):
        samples.append(
            {
                "sample_id": f"{start:%Y%m%d}@{stamp(utc(start))}",
                "event_id": f"{start:%Y%m%d}",
                "row": row,
                "context_start_time": stamp(utc(start)),
                "init_time": stamp(utc(start) + timedelta(minutes=30)),
                "lead_minutes": list(range(6, 121, 6)),
                "ready": True,
                "grid": {"shape": [128, 128], "resolution_m": 3000},
                "notice": NOTICE,
            }
        )
    return {
        "data_id": "cma_radar",
        "total": len(starts),
        "offset": offset,
        "limit": limit,
        "days": days,
        "day": day,
        "samples": samples,
    }


def read_window(path, start, event_id, *, center_lon=118.0, center_lat=32.0):
    index = file_index(path)
    times = _check(index, event_id, start)
    grid = grid_for(center_lon, center_lat)
    frames, files = [], []
    for time in times:
        source = index[time.replace(tzinfo=None)]
        frames.append(regrid(read_cref(source.path), grid))
        files.append(str(source.path.relative_to(Path(path))))
    values = np.stack(frames)
    if not np.isfinite(values[:CONTEXT]).all():
        raise ValueError(
            "Selected CMA region contains missing input pixels; choose another center or time window"
        )
    return {
        "data_id": "cma_radar",
        "variable": "CREF",
        "unit": "dBZ",
        "cadence_minutes": CADENCE,
        "context": values[:CONTEXT],
        "target": values[CONTEXT:],
        "context_times": [stamp(t) for t in times[:CONTEXT]],
        "target_times": [stamp(t) for t in times[CONTEXT:]],
        "context_start_time": stamp(times[0]),
        "init_time": stamp(times[CONTEXT - 1]),
        "lead_minutes": list(range(6, 121, 6)),
        "event_id": event_id,
        "sample_id": f"{event_id}@{stamp(times[0])}",
        "row": sorted(index).index(times[0].replace(tzinfo=None)),
        "provenance": {
            "notice": NOTICE,
            "source_files": files,
            "mapping_dbz": [0, 75],
            "training_data": "SEVIR VIL",
            "evaluation_levels": [10, 20, 30, 40, 50],
        },
    }, grid
