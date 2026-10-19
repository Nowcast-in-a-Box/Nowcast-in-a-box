"""SEVIR ingestion: one explicit event/window, one HDF5 open."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import h5py
import numpy as np
from pyproj import CRS, Transformer

UTC = timezone.utc


def utc(value):
    when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(UTC)


def stamp(when):
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")


def text(value):
    return value.decode() if isinstance(value, bytes) else str(value)


def read_window(path, start_time, event_id=None):
    wanted = utc(start_time).strftime("%Y-%m-%d %H:%M:%S")
    with h5py.File(path, "r") as handle:
        times = [text(v) for v in handle["start_time_utc"][:]]
        events = [text(v) for v in handle["event_id"][:]]
        rows = [
            i
            for i, (time, event) in enumerate(zip(times, events))
            if time == wanted and (event_id is None or event == event_id)
        ]
        if not rows:
            raise FileNotFoundError(f"SEVIR window not found: {event_id} {wanted}")
        if len(rows) != 1:
            raise ValueError("ambiguous SEVIR window; select event_id and start_time")
        row = rows[0]
        context = np.asarray(handle["inputs"][row], dtype=np.float32) * 255
        target = np.asarray(handle["targets"][row], dtype=np.float32) * 255
        event = events[row]
        event_time = utc(text(handle["time_utc"][row]))
        offsets = np.asarray(handle["frame_minutes"][row], dtype=int)
        frame_indices = np.asarray(handle["frame_idx"][row], dtype=int)
        event_type = text(handle["event_type"][row])
    if context.shape != (6, 128, 128) or target.shape != (20, 128, 128):
        raise ValueError("SEVIR requires six context and twenty target 128x128 frames")
    if offsets.shape != (26,) or frame_indices.shape != (26,):
        raise ValueError("incomplete SEVIR frame metadata")
    valid_times = [event_time + timedelta(minutes=int(v)) for v in offsets]
    if stamp(valid_times[0]) != stamp(utc(start_time)) or any(
        b - a != timedelta(minutes=5) for a, b in zip(valid_times, valid_times[1:])
    ):
        raise ValueError(
            "SEVIR frame metadata does not match the selected time/cadence"
        )
    if not np.isfinite(context).all() or not np.isfinite(target).all():
        raise ValueError("SEVIR input has undeclared non-finite values")
    return {
        "context": context,
        "target": target,
        "context_times": [stamp(v) for v in valid_times[:6]],
        "target_times": [stamp(v) for v in valid_times[6:]],
        "context_start_time": stamp(valid_times[0]),
        "init_time": stamp(valid_times[5]),
        "lead_minutes": list(range(5, 101, 5)),
        "event_id": event,
        "sample_id": f"{event}@{stamp(valid_times[0])}",
        "row": row,
        "event_type": event_type,
    }


def read_grid(catalog_path, event_id, shape=(128, 128)):
    with Path(catalog_path).open(newline="", encoding="utf-8-sig") as stream:
        matches = [
            r
            for r in csv.DictReader(stream)
            if r["id"] == event_id and r["img_type"] == "vil"
        ]
    if len(matches) != 1:
        raise ValueError("CATALOG must identify exactly one VIL event")
    return grid_from_row(matches[0], shape)


def grid_from_row(row, shape=(128, 128)):
    # Processed bilinear samples cannot reconstruct a sentinel-contaminated mask.
    if float(row["pct_missing"]) != 0:
        raise ValueError(
            "processed SEVIR event has missing pixels; mask-aware preprocessing required"
        )
    crs = CRS.from_user_input(row["proj"])
    if not crs.is_projected:
        raise ValueError("SEVIR requires an explicit projected CRS")
    transform = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x0, y0 = transform.transform(float(row["llcrnrlon"]), float(row["llcrnrlat"]))
    x1, y1 = transform.transform(float(row["urcrnrlon"]), float(row["urcrnrlat"]))
    width, height = float(row["width_m"]), float(row["height_m"])
    if not np.allclose([x1 - x0, y1 - y0], [width, height], atol=1, rtol=0):
        raise ValueError("CATALOG corners and projected dimensions disagree")
    native_x, native_y = int(row["size_x"]), int(row["size_y"])
    if (native_y, native_x) != (384, 384):
        raise ValueError("processed SEVIR grid requires a 384x384 source")
    ny, nx = shape
    # Upstream pixel zero is the LL reference; align_corners=False samples (i+.5)*scale-.5.
    x = x0 + ((np.arange(nx) + 0.5) * native_x / nx - 0.5) * width / native_x
    y = y0 + ((np.arange(ny) + 0.5) * native_y / ny - 0.5) * height / native_y
    return {
        "crs": crs.to_wkt(),
        "x": x,
        "y": y,
        "axis_units": "m",
        "source": "CATALOG.csv + align_corners=False pixel mapping",
        "native_shape": [native_y, native_x],
        "missing_policy": "reject_pct_missing_nonzero",
    }
