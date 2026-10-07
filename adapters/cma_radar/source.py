# Author: joking233
# Last modified: 2026-10-07
# Modified by: sonderlau
"""cma_radar data source."""

from __future__ import annotations

import bz2
import datetime as dt
import re
from pathlib import Path

import numpy as np
import xarray as xr

_FILENAME_RE = re.compile(
    r"^Z_RADA_C_[^_]+_(?P<generation>\d{14})_P_DOR_[^_]+_CREF_"
    r"(?P<data_date>\d{8})_(?P<data_time>\d{6})\.bin$",
    re.IGNORECASE,
)


from interfaces.data import (
    ChannelSpec,
    DataBundle,
    DataCapabilities,
    DataRequest,
    DeclaredDataAdapter,
    SpatialSpec,
    VariableSpec,
    bundle_from_parts,
    loader_config,
)

# Dataset. Fixed before any request runs.
ID = "cma_radar"
VERSION = "1.0.0"
TITLE = "CMA Radar"
INSTRUMENT = "CREF mosaic"
VARIABLE_ID = "CREF"
VARIABLE_LONG_NAME = "composite_reflectivity"
VARIABLE_UNITS = "dBZ"
CHANNELS = "closed"
NATIVE_CADENCE_MINUTES = 6
CROP = True
FORMATS = ("npz", "bin")


def _parse_utc(value: str) -> dt.datetime:
    out = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _iso_utc(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class CREFFile:
    """Filename metadata for one CMA national CREF mosaic file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        m = _FILENAME_RE.match(self.path.name)
        if not m:
            raise ValueError(f"Unsupported CREF filename: {self.path.name}")

        self.generation_time = dt.datetime.strptime(
            m.group("generation"), "%Y%m%d%H%M%S"
        )
        self.data_time = dt.datetime.strptime(
            m.group("data_date") + m.group("data_time"),
            "%Y%m%d%H%M%S",
        )


def _requested_times(
    initial_time: str,
    history_steps: int,
    interval_minutes: int,
):
    end = _parse_utc(initial_time)
    return [
        end - dt.timedelta(minutes=interval_minutes * i)
        for i in range(history_steps - 1, -1, -1)
    ]


def _discover_cref_files(data_dir: Path) -> list[CREFFile]:
    files = []
    for path in data_dir.glob("*.bin"):
        try:
            files.append(CREFFile(path))
        except ValueError:
            continue
    return files


def _select_latest_generation(
    files: list[CREFFile],
    data_time: dt.datetime,
) -> CREFFile:
    matched = [f for f in files if f.data_time == data_time]

    if not matched:
        available = sorted({f.data_time for f in files})
        nearest = sorted(
            available,
            key=lambda t: abs(t - data_time),
        )[:4]

        nearest_text = ", ".join(_iso_utc(t) for t in nearest) if nearest else "none"

        raise FileNotFoundError(
            "\nCMA CREF file not found\n"
            f"requested_data_time = {_iso_utc(data_time)}\n"
            f"nearest_available   = {nearest_text}"
        )

    # Several files can represent the same actual radar time.
    # Use the most recently generated version.
    return max(matched, key=lambda f: f.generation_time)


def _validate_bbox(bbox) -> list[float]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("region.bbox must be [west, south, east, north]")

    if not all(isinstance(v, (int, float)) for v in bbox):
        raise ValueError("region.bbox values must be numeric")

    west, south, east, north = map(float, bbox)

    if west >= east or south >= north:
        raise ValueError("Invalid region.bbox: require west < east and south < north")

    return [west, south, east, north]


def _bbox_from_coords(da: xr.DataArray) -> list[float]:
    return [
        float(da.longitude.min()),
        float(da.latitude.min()),
        float(da.longitude.max()),
        float(da.latitude.max()),
    ]


def _crop_bbox(
    da: xr.DataArray,
    bbox: list[float],
) -> xr.DataArray:
    """
    Crop to [west, south, east, north].

    The native CREF latitude coordinate is north -> south, so index-based
    selection is used to avoid assumptions about coordinate direction.
    """
    west, south, east, north = bbox

    native_bbox = da.attrs["native_bounds"]
    native_west, native_south, native_east, native_north = native_bbox

    # A half-grid tolerance allows requests that use the nominal header
    # boundary (for example 135.0E) while the last cell center is 134.99E.
    tolerance = float(da.attrs["native_resolution_deg"]) / 2.0

    if (
        west < native_west - tolerance
        or south < native_south - tolerance
        or east > native_east + tolerance
        or north > native_north + tolerance
    ):
        raise ValueError(
            "\nRequested radar region is outside the available domain\n"
            f"available_bbox = {native_bbox}\n"
            f"requested_bbox = {bbox}\n"
            "bbox order      = [west, south, east, north]"
        )

    lon = da.longitude.values
    lat = da.latitude.values

    lon_idx = np.where((lon >= west) & (lon <= east))[0]
    lat_idx = np.where((lat >= south) & (lat <= north))[0]

    if lon_idx.size == 0 or lat_idx.size == 0:
        raise ValueError(
            "\nRequested radar region contains no grid cells\n"
            f"available_bbox = {native_bbox}\n"
            f"requested_bbox = {bbox}"
        )

    cropped = da.isel(
        longitude=slice(int(lon_idx[0]), int(lon_idx[-1]) + 1),
        latitude=slice(int(lat_idx[0]), int(lat_idx[-1]) + 1),
    ).copy()

    output_bbox = _bbox_from_coords(cropped)

    cropped.attrs["requested_region"] = bbox
    cropped.attrs["output_bounds"] = output_bbox
    cropped.attrs["crop_applied"] = True

    return cropped


def read_cref(path: str | Path) -> xr.DataArray:
    """
    Decode one CMA CREF binary file.

    The binary layout and scaling follow the supplied internal reader.
    """
    path = Path(path)
    meta = CREFFile(path)
    buf = path.read_bytes()

    if len(buf) < 256:
        raise ValueError(f"CREF file is too small: {path}")

    compress_flag = int(np.frombuffer(buf[166:168], dtype="<i2")[0])
    cols = int(np.frombuffer(buf[148:152], dtype="<i4")[0])
    rows = int(np.frombuffer(buf[152:156], dtype="<i4")[0])

    lat_start = float(np.frombuffer(buf[124:128], dtype="<i4")[0]) / 1000.0
    lon_start = float(np.frombuffer(buf[128:132], dtype="<i4")[0]) / 1000.0
    lat_end = float(np.frombuffer(buf[132:136], dtype="<i4")[0]) / 1000.0
    lon_end = float(np.frombuffer(buf[136:140], dtype="<i4")[0]) / 1000.0

    resolution = 0.01

    payload = buf[256:]
    raw_bytes = bz2.decompress(payload) if compress_flag == 1 else payload

    expected_bytes = rows * cols * 2
    if len(raw_bytes) != expected_bytes:
        raise ValueError(
            "Unexpected CREF payload size: "
            f"got {len(raw_bytes)}, expected {expected_bytes}"
        )

    raw = np.frombuffer(
        raw_bytes,
        dtype="<i2",
    ).reshape(rows, cols)

    data = raw.astype(np.float32) / 10.0

    # Follow the orientation used in the supplied CREF reader:
    # latitude north -> south, longitude west -> east.
    latitude = lat_end - np.arange(rows, dtype=np.float64) * resolution
    longitude = lon_start + np.arange(cols, dtype=np.float64) * resolution

    native_bounds = [
        min(lon_start, lon_end),
        min(lat_start, lat_end),
        max(lon_start, lon_end),
        max(lat_start, lat_end),
    ]

    da = xr.DataArray(
        data,
        dims=("latitude", "longitude"),
        coords={
            "latitude": latitude,
            "longitude": longitude,
        },
        name="CREF",
        attrs={
            "source": "cma_radar",
            "product": "CREF",
            "long_name": ("CMA weather radar mosaic composite reflectivity"),
            "units": "dBZ",
            "data_time": _iso_utc(meta.data_time),
            "generation_time": _iso_utc(meta.generation_time),
            "native_resolution_deg": resolution,
            "compress_flag": compress_flag,
            # [west, south, east, north]
            "native_bounds": native_bounds,
            "decoder_note": (
                "int16 values scaled by 0.1 following " "the supplied CREF reader"
            ),
        },
    )

    return da


def load_cma_radar(config: dict) -> dict:
    """
    NiB source loader for CMA CREF mosaics.

    Input contract:
      data_dir
      initial_time
      history_steps
      interval_minutes
      channels
      region.bbox

    The loader matches files by actual radar data time and selects the
    newest generation when multiple files represent the same data time.
    The requested bbox is then cropped from the native CREF domain.
    """
    required = [
        "data_dir",
        "initial_time",
        "history_steps",
        "interval_minutes",
        "channels",
        "region",
    ]

    missing = [k for k in required if k not in config]
    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    data_dir = Path(config["data_dir"])
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory does not exist: {data_dir}")

    history_steps = int(config["history_steps"])
    interval_minutes = int(config["interval_minutes"])

    if history_steps < 1 or interval_minutes < 1:
        raise ValueError(
            "history_steps and interval_minutes " "must be positive integers"
        )

    channels = [str(c).upper() for c in config["channels"]]

    if channels != ["CREF"]:
        raise ValueError("CMA radar Demo v1 currently supports channels: [CREF]")

    region = config["region"]
    bbox = region.get("bbox") if isinstance(region, dict) else None
    bbox = _validate_bbox(bbox)

    all_files = _discover_cref_files(data_dir)
    if not all_files:
        raise FileNotFoundError(f"No CMA CREF .bin files found in {data_dir}")

    times = _requested_times(
        config["initial_time"],
        history_steps,
        interval_minutes,
    )

    print("=== CMA Radar Loader Request ===")
    print("data_dir         :", data_dir)
    print("initial_time     :", config["initial_time"])
    print("history_steps    :", history_steps)
    print("interval_minutes :", interval_minutes)
    print("channels         :", channels)
    print("requested region :", bbox)
    print("bbox order       : [west, south, east, north]")

    results = {}

    for requested_time in times:
        selected = _select_latest_generation(
            all_files,
            requested_time,
        )

        full_da = read_cref(selected.path)
        native_bbox = full_da.attrs["native_bounds"]

        cropped_da = _crop_bbox(
            full_da,
            bbox,
        )

        output_bbox = cropped_da.attrs["output_bounds"]

        key = _iso_utc(requested_time)

        results[key] = {
            "files": [str(selected.path)],
            "channels": {
                "CREF": cropped_da,
            },
            "metadata": {
                "data_time": _iso_utc(selected.data_time),
                "generation_time": _iso_utc(selected.generation_time),
                "available_region": native_bbox,
                "requested_region": bbox,
                "output_region": output_bbox,
                "native_resolution_deg": full_da.attrs["native_resolution_deg"],
            },
        }

        print(f"\nRequested time  : {key}")
        print("Selected file   :", selected.path.name)
        print(
            "Generation time :",
            _iso_utc(selected.generation_time),
        )
        print(
            "Data time       :",
            _iso_utc(selected.data_time),
        )
        print(
            "Available region:",
            native_bbox,
            "[west, south, east, north]",
        )
        print(
            "Requested region:",
            bbox,
            "[west, south, east, north]",
        )
        print(
            "Output region   :",
            output_bbox,
            "[west, south, east, north]",
        )
        print(
            "Native shape    :",
            tuple(full_da.shape),
        )
        print(
            "Output shape    :",
            tuple(cropped_da.shape),
        )
        print(
            "Resolution      :",
            full_da.attrs["native_resolution_deg"],
            "degree",
        )

    print("\n=== CMA Radar Loader Success ===")
    print("Loaded times:", len(results))

    return results


class Source(DeclaredDataAdapter):
    CAPABILITIES = DataCapabilities(
        id=ID,
        version=VERSION,
        title=TITLE,
        instrument=INSTRUMENT,
        channels=ChannelSpec(
            mode=CHANNELS,
            variables=(VariableSpec(VARIABLE_ID, VARIABLE_LONG_NAME, VARIABLE_UNITS),),
        ),
        spatial=SpatialSpec(crop=CROP),
        native_cadence_minutes=NATIVE_CADENCE_MINUTES,
        formats=FORMATS,
    )

    def load(self, request: DataRequest) -> DataBundle:
        self.validate(request)
        config = loader_config(request)
        data_dir = Path(config["data_dir"])
        if any(data_dir.glob("*.npz")):
            from adapters.cma_radar.npz_io import load_cma_npz

            raw = load_cma_npz(config, VARIABLE_ID)
        elif any(data_dir.glob("*.bin")):
            raw = load_cma_radar(config)
        else:
            raise FileNotFoundError(
                f"No {' or '.join(FORMATS)} files found in {data_dir}"
            )
        data: dict = {}
        source_files: dict = {}
        for time_key, time_item in raw.items():
            data[time_key] = {}
            source_files[time_key] = {}
            files = list(time_item["files"])
            for channel, values in time_item["channels"].items():
                data[time_key][channel] = values
                source_files[time_key][channel] = files
        return _bundle(self.capabilities(), request, data, source_files)


def _bundle(caps, request, data, source_files) -> DataBundle:
    return bundle_from_parts(
        caps.id,
        request,
        data,
        source_files,
        {
            "loader_module": "adapters.cma_radar.source",
            "version": caps.version,
            "n_times": len(data),
            "n_channel_fields": sum(len(item) for item in data.values()),
        },
    )


ADAPTER = Source()
