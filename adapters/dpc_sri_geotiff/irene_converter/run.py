"""Convert one completed IRENE GeoTIFF ensemble into a NiB ForecastField.

The upstream IRENE image publishes one TIFF per (lead, ensemble member), plus
a manifest recording the exact files, grid and valid times.  This converter is
intentionally a separate product: it does not modify IRENE or infer metadata
from filenames when a source manifest is available.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio

from interfaces.fields import Forecast
from utils.stored_fields import add_forecast, save_manifest


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("IRENE manifest timestamps must be explicit UTC")
    return parsed


def _source_manifest(root: Path) -> tuple[Path, dict]:
    candidates: list[tuple[Path, dict]] = []
    for path in root.rglob("manifest.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("model") == "IRENE" and payload.get("status") == "complete":
            candidates.append((path, payload))
    if len(candidates) != 1:
        raise ValueError(
            "converter requires exactly one complete IRENE manifest below --input-dir"
        )
    return candidates[0]


def _grid(dataset: rasterio.io.DatasetReader) -> dict:
    if dataset.count != 1 or dataset.crs is None:
        raise ValueError("IRENE output must be a one-band GeoTIFF with a CRS")
    transform = dataset.transform
    if transform.b != 0 or transform.d != 0 or transform.a <= 0 or transform.e >= 0:
        raise ValueError("IRENE output must use a north-up, regular GeoTIFF grid")
    x = transform.c + (np.arange(dataset.width, dtype=np.float64) + 0.5) * transform.a
    # GeoTIFF rows descend north-to-south.  StoredFields requires increasing y,
    # hence the corresponding flip performed in _read_field.
    y = transform.f + (np.arange(dataset.height, dtype=np.float64) + 0.5) * transform.e
    return {
        "crs": dataset.crs.to_wkt(),
        "axis_units": dataset.crs.axis_info[0].unit_name
        if dataset.crs.axis_info
        else "unknown",
        "x": x,
        "y": y[::-1].copy(),
    }


def _read_field(path: Path, expected_grid: dict | None) -> tuple[np.ndarray, dict]:
    with rasterio.open(path) as dataset:
        grid = _grid(dataset)
        values = dataset.read(1).astype(np.float32, copy=False)
        if dataset.nodata is not None:
            values = np.where(values == dataset.nodata, np.nan, values)
    if expected_grid is not None:
        for key in ("crs", "x", "y"):
            if key == "crs":
                matches = grid[key] == expected_grid[key]
            else:
                matches = np.array_equal(grid[key], expected_grid[key])
            if not matches:
                raise ValueError("IRENE GeoTIFF outputs disagree on their grid")
    return np.flipud(values).copy(), grid


def convert(run: Path, input_dir: Path, model_id: str = "irene") -> dict:
    source_path, source = _source_manifest(input_dir)
    run_time = _utc(source["run_time"])
    outputs = source.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("IRENE manifest has no output files")

    members = sorted({int(item["member"]) for item in outputs})
    valid_times = sorted({_utc(item["valid_time"]) for item in outputs})
    if members != list(range(len(members))):
        raise ValueError("IRENE ensemble members must be contiguous from zero")
    if not all(time > run_time for time in valid_times):
        raise ValueError("IRENE valid times must follow run_time")
    leads = [int((time - run_time).total_seconds() // 60) for time in valid_times]

    by_key = {(int(item["member"]), _utc(item["valid_time"])): item for item in outputs}
    expected_keys = {(member, time) for member in members for time in valid_times}
    if set(by_key) != expected_keys:
        raise ValueError(
            "IRENE manifest does not contain one file for every member/lead"
        )

    cube = None
    grid = None
    for member in members:
        for lead, valid_time in enumerate(valid_times):
            item = by_key[(member, valid_time)]
            field, field_grid = _read_field(source_path.parent / item["name"], grid)
            if grid is None:
                grid = field_grid
                cube = np.empty(
                    (len(members), len(valid_times), *field.shape), dtype=np.float32
                )
            cube[member, lead] = field

    assert cube is not None and grid is not None
    weight = source.get("artifacts", {}).get("weights", {}).get("sha256")
    if not isinstance(weight, str) or len(weight) != 64:
        raise ValueError("IRENE manifest does not declare a SHA-256 for its weights")
    forecast = Forecast(
        "RR",
        "mm/h",
        cube,
        tuple(time.isoformat().replace("+00:00", "Z") for time in valid_times),
        grid,
        ~np.isfinite(cube),
        run_time.isoformat().replace("+00:00", "Z"),
        tuple(leads),
    )
    add_forecast(
        run,
        model_id,
        str(source.get("model_version", "unknown")),
        weight,
        forecast,
    )
    access_manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    access_manifest["forecasts"][model_id]["converter"] = {
        "id": "irene_geotiff_to_forecast",
        "source_manifest": str(source_path.relative_to(run)).replace("\\", "/"),
        "source_format": "irene-geotiff-ensemble.v1",
    }
    save_manifest(run, access_manifest)
    return access_manifest["forecasts"][model_id]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--model-id", default="irene")
    args = parser.parse_args()
    result = convert(args.run.resolve(), args.input_dir.resolve(), args.model_id)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
