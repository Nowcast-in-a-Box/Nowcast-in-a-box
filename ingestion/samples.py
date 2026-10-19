"""Read sample declarations only; never read meteorological arrays or weights."""

import csv
from datetime import timedelta
from pathlib import Path

import h5py

from ingestion.sevir import grid_from_row, read_grid, stamp, text, utc


def check_window(path, catalog_path, event_id, start_time):
    """Confirm one window from metadata only. Do not read meteorological arrays."""
    wanted = utc(start_time).strftime("%Y-%m-%d %H:%M:%S")
    with h5py.File(path, "r") as handle:
        events = [text(value) for value in handle["event_id"][:]]
        starts = [text(value) for value in handle["start_time_utc"][:]]
    rows = [
        index
        for index, (event, start) in enumerate(zip(events, starts))
        if event == event_id and start == wanted
    ]
    if len(rows) != 1:
        raise FileNotFoundError(f"SEVIR window not found: {event_id} {wanted}")
    read_grid(catalog_path, event_id)
    return {
        "event_id": event_id,
        "context_start_time": stamp(utc(start_time)),
        "ready": True,
    }


def list_samples(path, catalog_path, *, offset=0, limit=100):
    if offset < 0 or limit < 1 or limit > 500:
        raise ValueError("sample pagination requires offset>=0 and 1<=limit<=500")
    with Path(catalog_path).open(newline="", encoding="utf-8-sig") as stream:
        events = {}
        for row in csv.DictReader(stream):
            if row["img_type"] == "vil":
                if row["id"] in events:
                    raise ValueError("duplicate VIL event in catalog")
                events[row["id"]] = row
    with h5py.File(path, "r") as handle:
        total = len(handle["event_id"])
        ids = handle["event_id"][offset : offset + limit]
        starts = handle["start_time_utc"][offset : offset + limit]
        input_count = int(handle.attrs["input_frames"])
        output_count = int(handle.attrs["output_frames"])
    if (input_count, output_count) != (6, 20):
        raise ValueError("unsupported processed SEVIR frame counts")
    result = []
    grids = {}
    for index, (raw_id, raw_start) in enumerate(zip(ids, starts), start=offset):
        event_id = text(raw_id)
        start = utc(text(raw_start))
        item = {
            "sample_id": f"{event_id}@{stamp(start)}",
            "event_id": event_id,
            "row": index,
            "context_start_time": stamp(start),
            "init_time": stamp(start + timedelta(minutes=25)),
            "lead_minutes": list(range(5, 101, 5)),
            "ready": False,
        }
        try:
            if event_id not in grids:
                if event_id not in events:
                    raise ValueError("event missing from catalog")
                grid = grid_from_row(events[event_id])
                grids[event_id] = {
                    "crs": grid["crs"],
                    "axis_units": grid["axis_units"],
                    "shape": [128, 128],
                    "extent": [
                        float(grid["x"][0]),
                        float(grid["y"][0]),
                        float(grid["x"][-1]),
                        float(grid["y"][-1]),
                    ],
                    "resolution": float(grid["x"][1] - grid["x"][0]),
                    "source": grid["source"],
                }
            item.update(grid=grids[event_id], ready=True)
        except ValueError as exc:
            item["reason"] = str(exc)
        result.append(item)
    return {
        "data_id": "sevir",
        "total": total,
        "offset": offset,
        "limit": limit,
        "samples": result,
    }
