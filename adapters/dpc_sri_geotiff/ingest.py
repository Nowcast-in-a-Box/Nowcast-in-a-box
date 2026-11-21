"""Create a NiB run from local Italy DPC SRI GeoTIFF scans.

The original IRENE container consumes complete 1200 x 1400 DPC scans.  This
adapter keeps those exact files in ``inputs/dpc`` for IRENE, while also writing
the IRENE configured 256 x 256 domain as NiB's standard ``context.npz``.  The
two representations belong to the same run and preserve the source timestamps.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from utils.stored_fields import create_run

CADENCE_MINUTES = 5
CONTEXT_STEPS = 6
# The default IRENE configuration crops west=-200000, north=256000 from the
# DPC native grid (west=-600000, north=650000, 1 km cells).
IRENE_WINDOW = Window(col_off=400, row_off=394, width=256, height=256)
NAME = re.compile(r"^dpc_sri\.(?P<stamp>\d{12})\.(?:tif|tiff)$", re.IGNORECASE)


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("DPC start time must be explicit UTC")
    return parsed


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def domain_window(size: int = 256) -> Window:
    """Return a square DPC window centred on IRENE's default 256 px domain.

    ConvGRU pads internally to a multiple of 32; keeping that constraint in
    this adapter makes an input-size experiment explicit and reproducible.
    """
    if size < 32 or size % 32:
        raise ValueError("IRENE domain size must be a positive multiple of 32")
    center_col = IRENE_WINDOW.col_off + IRENE_WINDOW.width / 2
    center_row = IRENE_WINDOW.row_off + IRENE_WINDOW.height / 2
    return Window(
        col_off=int(center_col - size / 2),
        row_off=int(center_row - size / 2),
        width=size,
        height=size,
    )


def validate_window(window: Window) -> Window:
    """Validate one DPC crop that the ConvGRU may use directly."""
    values = (window.col_off, window.row_off, window.width, window.height)
    if any(value != int(value) for value in values):
        raise ValueError("IRENE crop offsets and dimensions must be integer pixels")
    if window.col_off < 0 or window.row_off < 0:
        raise ValueError("IRENE crop offsets must be non-negative")
    if (
        window.width < 32
        or window.height < 32
        or window.width % 32
        or window.height % 32
    ):
        raise ValueError("IRENE crop width and height must be positive multiples of 32")
    return window


def window_from_grid(grid: dict) -> Window:
    """Recover the native DPC window used to make an increasing-y NiB grid."""
    x, y = np.asarray(grid["x"], dtype=float), np.asarray(grid["y"], dtype=float)
    if len(x) < 2 or len(y) < 2:
        raise ValueError("cannot recover DPC crop from fewer than two grid points")
    dx, dy = float(np.median(np.diff(x))), float(np.median(np.diff(y)))
    if not (np.isclose(dx, 1000.0) and np.isclose(dy, 1000.0)):
        raise ValueError("DPC crop must retain 1 km regular coordinates")
    # DPC native outer grid: west=-600 km, north=650 km, north-first rows.
    col = round((x[0] - 500.0 + 600000.0) / 1000.0)
    row = round((650000.0 - (y[-1] + 500.0)) / 1000.0)
    return validate_window(
        Window(col_off=col, row_off=row, width=len(x), height=len(y))
    )


def irene_config(grid: dict) -> dict:
    """Create the upstream IRENE config that exactly matches the NiB crop."""
    crop = window_from_grid(grid)
    west = -600000.0 + crop.col_off * 1000.0
    north = 650000.0 - crop.row_off * 1000.0
    return {
        "artifacts": "/app/artifacts/v1",
        "domain": {
            "west": west,
            "south": north - crop.height * 1000.0,
            "east": west + crop.width * 1000.0,
            "north": north,
        },
        "model_latitude_order": "north_to_south",
        "product": "dpc_sri",
        "remote_directory": "SRI",
        "missing_policy": "zero_rain",
        "max_missing_fraction": 0.95,
        "ftp": {
            "timeout_seconds": 60,
            "attempts": 3,
            "retry_delay_seconds": 30,
            "max_retry_delay_seconds": 120,
        },
        "ensemble_members": 4,
        "seed": 20260101,
    }


def domain_metadata(grid: dict) -> dict:
    """Describe the selected DPC window in pixels, metres and WGS84 degrees."""
    crop = window_from_grid(grid)
    config = irene_config(grid)
    projected = dict(config["domain"])
    west, south, east, north = transform_bounds(
        grid["crs"],
        "EPSG:4326",
        projected["west"],
        projected["south"],
        projected["east"],
        projected["north"],
        densify_pts=21,
    )
    return {
        "pixel_window": {
            "col_off": int(crop.col_off),
            "row_off": int(crop.row_off),
            "width": int(crop.width),
            "height": int(crop.height),
        },
        "projected_bounds_m": projected,
        "bbox_wgs84": {
            "west": round(float(west), 6),
            "south": round(float(south), 6),
            "east": round(float(east), 6),
            "north": round(float(north), 6),
        },
    }


def scans(data: Path) -> dict[datetime, Path]:
    result: dict[datetime, Path] = {}
    for path in data.rglob("*"):
        if not path.is_file():
            continue
        match = NAME.match(path.name)
        if match is None:
            continue
        time = datetime.strptime(match["stamp"], "%Y%m%d%H%M").replace(
            tzinfo=timezone.utc
        )
        if time in result:
            raise ValueError(f"duplicate DPC scan time {stamp(time)}")
        result[time] = path
    if not result:
        raise FileNotFoundError("no dpc_sri.YYYYMMDDHHMM.tif files found")
    return result


def available_starts(data: Path) -> list[datetime]:
    index = scans(data)
    return [
        start
        for start in sorted(index)
        if all(
            start + timedelta(minutes=CADENCE_MINUTES * i) in index
            for i in range(CONTEXT_STEPS)
        )
    ]


def list_samples(data: Path, offset: int, limit: int, day: str | None) -> dict:
    starts = available_starts(data)
    if day:
        starts = [item for item in starts if item.strftime("%Y%m%d") == day]
    selected = starts[offset : offset + limit]
    return {
        "data_id": "dpc_sri_geotiff",
        "total": len(starts),
        "offset": offset,
        "limit": limit,
        "day": day,
        "samples": [
            {
                "sample_id": f"dpc-sri@{stamp(start)}",
                "event_id": start.strftime("%Y%m%d"),
                "row": offset + index,
                "context_start_time": stamp(start),
                "init_time": stamp(start + timedelta(minutes=25)),
                "lead_minutes": list(range(5, 61, 5)),
                "ready": True,
                "grid": {"shape": [256, 256], "resolution_m": 1000},
            }
            for index, start in enumerate(selected)
        ],
    }


def _read_crop(path: Path, window: Window = IRENE_WINDOW) -> tuple[np.ndarray, dict]:
    with rasterio.open(path) as source:
        if source.count != 1 or source.crs is None:
            raise ValueError(f"{path.name} must be a one-band GeoTIFF with a CRS")
        if (
            source.width < window.col_off + window.width
            or source.height < window.row_off + window.height
        ):
            raise ValueError(f"{path.name} does not cover the requested IRENE domain")
        transform = source.window_transform(window)
        values = source.read(1, window=window).astype(np.float32, copy=False)
        if source.nodata is not None:
            values = np.where(values == source.nodata, np.nan, values)
    x = transform.c + (np.arange(values.shape[1], dtype=np.float64) + 0.5) * transform.a
    y = transform.f + (np.arange(values.shape[0], dtype=np.float64) + 0.5) * transform.e
    return np.flipud(values).copy(), {
        "crs": source.crs.to_wkt(),
        "x": x,
        "y": y[::-1].copy(),
    }


def create(
    data: Path,
    runs: Path,
    run_id: str,
    start: datetime,
    atlas_revision: str,
    *,
    domain_size: int = 256,
    crop_window: Window | None = None,
) -> Path:
    index = scans(data)
    times = [
        start + timedelta(minutes=CADENCE_MINUTES * step)
        for step in range(CONTEXT_STEPS)
    ]
    missing = [stamp(item) for item in times if item not in index]
    if missing:
        raise FileNotFoundError("missing consecutive DPC scans: " + ", ".join(missing))
    crop_window = validate_window(crop_window or domain_window(domain_size))
    fields, grid = [], None
    for time in times:
        field, next_grid = _read_crop(index[time], crop_window)
        if grid is None:
            grid = next_grid
        elif (
            grid["crs"] != next_grid["crs"]
            or not np.array_equal(grid["x"], next_grid["x"])
            or not np.array_equal(grid["y"], next_grid["y"])
        ):
            raise ValueError("DPC scans disagree on the default IRENE crop grid")
        fields.append(field)
    assert grid is not None
    init_time = times[-1]
    domain = domain_metadata(grid)
    run_window = {
        "data_id": "dpc_sri_geotiff",
        "sample_id": f"dpc-sri@{stamp(start)}",
        "event_id": start.strftime("%Y%m%d"),
        "row": 0,
        "context_start_time": stamp(start),
        "init_time": stamp(init_time),
        "context": np.stack(fields).astype(np.float32),
        "context_times": [stamp(item) for item in times],
        "cadence_minutes": CADENCE_MINUTES,
        "variable": "RR",
        "unit": "mm/h",
        "provenance": {
            "adapter": "dpc_sri_geotiff",
            "native_input": "six complete DPC SRI GeoTIFF scans",
            "model_domain": domain,
            "source_files": [
                str(index[item].relative_to(data)).replace("\\", "/") for item in times
            ],
        },
    }
    directory = create_run(
        runs, run_id, run_window, grid, atlas_revision=atlas_revision
    )
    raw = directory / "inputs" / "dpc"
    raw.mkdir(parents=True)
    for time in times:
        shutil.copy2(index[time], raw / index[time].name)
    config = directory / "config"
    config.mkdir()
    (config / "irene.json").write_text(json.dumps(irene_config(grid), indent=2) + "\n")
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--atlas-revision", required=True)
    parser.add_argument(
        "--domain-size",
        type=int,
        default=256,
        help="Square centred IRENE domain; multiple of 32, default 256",
    )
    parser.add_argument("--window-col", type=int, help="DPC native crop column offset")
    parser.add_argument("--window-row", type=int, help="DPC native crop row offset")
    parser.add_argument("--window-width", type=int, help="DPC native crop width")
    parser.add_argument("--window-height", type=int, help="DPC native crop height")
    parser.add_argument("--event")
    parser.add_argument("--list-samples", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--day")
    args = parser.parse_args()
    if args.list_samples:
        print(json.dumps(list_samples(args.data, args.offset, args.limit, args.day)))
        return
    start = utc(args.start)
    if args.event and args.event != start.strftime("%Y%m%d"):
        parser.error("event must match the first DPC scan date")
    requested = (
        args.window_col,
        args.window_row,
        args.window_width,
        args.window_height,
    )
    if any(value is not None for value in requested) and any(
        value is None for value in requested
    ):
        parser.error("specify all four --window-col/row/width/height values together")
    crop_window = None
    if all(value is not None for value in requested):
        crop_window = Window(
            col_off=args.window_col,
            row_off=args.window_row,
            width=args.window_width,
            height=args.window_height,
        )
    directory = create(
        args.data,
        args.runs,
        args.id,
        start,
        args.atlas_revision,
        domain_size=args.domain_size,
        crop_window=crop_window,
    )
    print(
        json.dumps(
            {
                "run_id": args.id,
                "directory": str(directory),
                "sample_id": f"dpc-sri@{stamp(start)}",
            }
        )
    )


if __name__ == "__main__":
    main()
