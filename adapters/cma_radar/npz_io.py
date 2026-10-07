"""NPZ mosaic reader. NumPy only; the .bin decoder stays in source.py."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np

NPZ_KEYS = (
    "reflectivity",
    "latitude",
    "longitude",
    "bbox",
    "resolution_deg",
    "data_time",
    "generation_time",
    "unit",
    "source",
    "product",
    "source_file",
)


def _parse_utc(value: str) -> dt.datetime:
    out = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _iso_utc(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value) -> str:
    if hasattr(value, "item"):
        value = value.item()
    return str(value)


def _requested_times(initial_time: str, history_steps: int, interval_minutes: int):
    end = _parse_utc(initial_time)
    return [
        end - dt.timedelta(minutes=interval_minutes * i)
        for i in range(history_steps - 1, -1, -1)
    ]


def _validate_bbox(bbox) -> list[float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("region.bbox must be [west, south, east, north]")
    west, south, east, north = (float(v) for v in bbox)
    if west >= east or south >= north:
        raise ValueError("Invalid region.bbox: require west < east and south < north")
    return [west, south, east, north]


def _crop(values, latitude, longitude, requested, native_bbox, resolution):
    west, south, east, north = requested
    native_west, native_south, native_east, native_north = native_bbox
    tolerance = float(resolution) / 2.0
    if (
        west < native_west - tolerance
        or south < native_south - tolerance
        or east > native_east + tolerance
        or north > native_north + tolerance
    ):
        raise ValueError(
            "Requested radar region is outside the available domain: "
            f"available={native_bbox} requested={requested}"
        )
    lon_idx = np.where((longitude >= west) & (longitude <= east))[0]
    lat_idx = np.where((latitude >= south) & (latitude <= north))[0]
    if lon_idx.size == 0 or lat_idx.size == 0:
        raise ValueError("Requested radar region contains no grid cells")
    return values[
        int(lat_idx[0]) : int(lat_idx[-1]) + 1,
        int(lon_idx[0]) : int(lon_idx[-1]) + 1,
    ]


def load_cma_npz(config: dict, variable_id: str) -> dict:
    """Load predeclared NPZ mosaics. Extra archive keys are ignored."""
    data_dir = Path(config["data_dir"])
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory does not exist: {data_dir}")

    bbox = _validate_bbox(config["region"]["bbox"])
    paths = sorted(data_dir.glob("*.npz"))
    if not paths:
        raise FileNotFoundError(f"No CMA radar .npz files found in {data_dir}")

    index = []
    for path in paths:
        with np.load(path) as archive:
            if (
                "data_time" not in archive.files
                or "generation_time" not in archive.files
            ):
                raise ValueError(f"NPZ file {path.name} is missing time keys")
            index.append(
                (
                    path,
                    _parse_utc(_text(archive["data_time"])),
                    _parse_utc(_text(archive["generation_time"])),
                )
            )

    results = {}
    for requested_time in _requested_times(
        config["initial_time"],
        int(config["history_steps"]),
        int(config["interval_minutes"]),
    ):
        matched = [item for item in index if item[1] == requested_time]
        if not matched:
            available = sorted({item[1] for item in index})
            nearest = sorted(available, key=lambda t: abs(t - requested_time))[:4]
            nearest_text = (
                ", ".join(_iso_utc(t) for t in nearest) if nearest else "none"
            )
            raise FileNotFoundError(
                "\nCMA radar NPZ file not found\n"
                f"requested_data_time = {_iso_utc(requested_time)}\n"
                f"nearest_available   = {nearest_text}"
            )
        path = max(matched, key=lambda item: item[2])[0]
        with np.load(path) as archive:
            missing = [key for key in NPZ_KEYS if key not in archive.files]
            if missing:
                raise ValueError(f"NPZ file {path.name} is missing keys: {missing}")
            product = _text(archive["product"])
            if product != variable_id:
                raise ValueError(
                    f"NPZ product {product!r} does not match declared variable {variable_id!r}"
                )
            values = np.array(archive["reflectivity"], dtype=np.float32, copy=True)
            latitude = np.array(archive["latitude"], dtype=np.float64, copy=True)
            longitude = np.array(archive["longitude"], dtype=np.float64, copy=True)
            native_bbox = [float(v) for v in np.array(archive["bbox"]).reshape(-1)]
            resolution = float(np.array(archive["resolution_deg"]).reshape(-1)[0])
            data_time = _parse_utc(_text(archive["data_time"]))
            generation_time = _parse_utc(_text(archive["generation_time"]))
            source_file = _text(archive["source_file"])
        if values.shape != (latitude.size, longitude.size):
            raise ValueError(
                f"NPZ reflectivity shape {values.shape} does not match coordinates"
            )
        cropped = _crop(values, latitude, longitude, bbox, native_bbox, resolution)
        key = _iso_utc(requested_time)
        results[key] = {
            "files": [str(path)],
            "channels": {variable_id: cropped},
            "metadata": {
                "data_time": _iso_utc(data_time),
                "generation_time": _iso_utc(generation_time),
                "available_region": native_bbox,
                "requested_region": bbox,
                "native_resolution_deg": resolution,
                "source_file": source_file,
            },
        }
    return results
