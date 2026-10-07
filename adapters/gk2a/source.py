# Author: joking233
# Last modified: 2026-10-07
# Modified by: sonderlau
"""gk2a data source."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import re
import xarray as xr


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
ID = "gk2a"
VERSION = "2.0.0"
TITLE = "GK2A AMI L1B"
INSTRUMENT = "AMI"
CHANNELS = "open"
NATIVE_CADENCE_MINUTES = 10
CROP = False
FORMATS = ("ami-l1b",)


def parse_time(time_str: str) -> datetime:
    dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_requested_times(initial_time: str, history_steps: int, interval_minutes: int):
    end_time = parse_time(initial_time)

    if history_steps < 1:
        raise ValueError("'history_steps' must be >= 1")
    if interval_minutes < 1:
        raise ValueError("'interval_minutes' must be >= 1")

    return [
        end_time - timedelta(minutes=interval_minutes * i)
        for i in range(history_steps - 1, -1, -1)
    ]


def validate_bbox(config: dict):
    region = config.get("region", {})
    bbox = region.get("bbox")

    if bbox is None:
        return None

    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("'region.bbox' must be [west, south, east, north]")

    west, south, east, north = map(float, bbox)

    if west >= east:
        raise ValueError("region.bbox requires west < east")
    if south >= north:
        raise ValueError("region.bbox requires south < north")

    return [west, south, east, north]


def _extract_channel(filename: str):
    m = re.match(r"gk2a_ami_le1b_([^_]+)_", filename.lower())
    return m.group(1) if m else None


def _extract_time_tag(filename: str):
    m = re.search(r"_(\d{12})\.nc$", filename.lower())
    return m.group(1) if m else None


def find_gk2a_file(data_dir: Path, requested_time: datetime, channel: str) -> Path:
    """
    Match one GK2A AMI L1B file by UTC time and channel.

    Distinguishes:
    1) requested time not found
    2) requested channel not found at that time
    """
    time_tag = requested_time.strftime("%Y%m%d%H%M")
    channel = channel.lower()

    all_files = sorted(data_dir.glob("gk2a_ami_le1b_*.nc"))

    if not all_files:
        raise FileNotFoundError(
            "\nGK2A data files not found\n" f"data_dir = {data_dir}"
        )

    # First check whether the requested time exists at all.
    time_matches = [p for p in all_files if _extract_time_tag(p.name) == time_tag]

    if not time_matches:
        available_times = sorted(
            {
                _extract_time_tag(p.name)
                for p in all_files
                if _extract_time_tag(p.name) is not None
            }
        )

        raise FileNotFoundError(
            "\nGK2A requested time not found\n"
            f"requested_time = {requested_time.isoformat()}\n"
            f"data_dir       = {data_dir}\n"
            f"available_times = {available_times[:20]}"
        )

    # The time exists; now check whether the requested channel exists at that time.
    channel_matches = [p for p in time_matches if _extract_channel(p.name) == channel]

    if not channel_matches:
        available_channels = sorted(
            {
                _extract_channel(p.name)
                for p in time_matches
                if _extract_channel(p.name) is not None
            }
        )

        raise FileNotFoundError(
            "\nGK2A requested channel not found\n"
            f"requested_time   = {requested_time.isoformat()}\n"
            f"requested_channel = {channel}\n"
            f"available_channels_at_time = {available_channels}\n"
            f"data_dir         = {data_dir}"
        )

    if len(channel_matches) > 1:
        raise RuntimeError(
            "\nMultiple GK2A files matched one request\n"
            f"requested_time = {requested_time.isoformat()}\n"
            f"channel        = {channel}\n"
            f"matches        = {[p.name for p in channel_matches]}"
        )

    return channel_matches[0]


def load_gk2a(config: dict):
    required = ["data_dir", "initial_time", "channels"]
    missing = [key for key in required if key not in config]

    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    data_dir = Path(config["data_dir"])

    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory does not exist: {data_dir}")

    channels = config["channels"]

    if not isinstance(channels, list) or len(channels) == 0:
        raise ValueError("'channels' must be a non-empty list")

    history_steps = int(config.get("history_steps", 1))
    interval_minutes = int(config.get("interval_minutes", 10))
    bbox = validate_bbox(config)

    requested_times = build_requested_times(
        initial_time=config["initial_time"],
        history_steps=history_steps,
        interval_minutes=interval_minutes,
    )

    print("=== GK2A Loader Request ===")
    print("data_dir         :", data_dir)
    print("initial_time     :", config["initial_time"])
    print("history_steps    :", history_steps)
    print("interval_minutes :", interval_minutes)
    print("channels         :", channels)

    if bbox is None:
        print("region           : full disk")
    else:
        west, south, east, north = bbox
        print(
            "region           : "
            f"[{west}, {south}, {east}, {north}] "
            "(west, south, east, north)"
        )

    print("requested_times  :")
    for t in requested_times:
        print("  -", t.isoformat())

    results = {}

    for requested_time in requested_times:
        time_key = requested_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        results[time_key] = {}

        print(f"\n--- Time: {time_key} ---")

        for channel in channels:
            channel = channel.lower()

            file_path = find_gk2a_file(
                data_dir=data_dir,
                requested_time=requested_time,
                channel=channel,
            )

            print(f"\nLoading channel : {channel}")
            print("Matched file    :", file_path.name)

            ds = xr.open_dataset(file_path)

            if "image_pixel_values" not in ds:
                ds.close()
                raise ValueError(
                    "\nExpected variable 'image_pixel_values' not found\n"
                    f"file = {file_path}"
                )

            actual_channel = str(
                ds["image_pixel_values"].attrs.get("channel_name", "")
            ).lower()

            if actual_channel and actual_channel != channel:
                ds.close()
                raise ValueError(
                    "\nGK2A channel mismatch\n"
                    f"requested_channel = {channel}\n"
                    f"file_channel      = {actual_channel}\n"
                    f"file              = {file_path}"
                )

            print("Satellite       :", ds.attrs.get("satellite_name"))
            print("Instrument      :", ds.attrs.get("instrument_name"))
            print(
                "Resolution(km)  :",
                ds.attrs.get("channel_spatial_resolution"),
            )
            print(
                "Wavelength(um)  :",
                ds.attrs.get("channel_center_wavelength"),
            )
            print(
                "Image shape     :",
                tuple(ds["image_pixel_values"].shape),
            )

            results[time_key][channel] = {
                "file": str(file_path),
                "dataset": ds,
            }

    print("\n=== GK2A Loader Success ===")
    print("Loaded times    :", len(requested_times))
    print("Loaded channels :", channels)
    print(
        "Loaded files    :",
        len(requested_times) * len(channels),
    )

    if bbox is None:
        print("Requested region: full disk")
    else:
        print(
            "Requested region:",
            f"{bbox} [west, south, east, north]",
        )
        print(
            "Note            : region is recorded only; "
            "actual spatial crop is not performed in v2."
        )

    return results


def close_results(results: dict):
    for time_item in results.values():
        for channel_item in time_item.values():
            channel_item["dataset"].close()


class Source(DeclaredDataAdapter):
    CAPABILITIES = DataCapabilities(
        id=ID,
        version=VERSION,
        title=TITLE,
        instrument=INSTRUMENT,
        channels=ChannelSpec(mode=CHANNELS),
        spatial=SpatialSpec(crop=CROP),
        native_cadence_minutes=NATIVE_CADENCE_MINUTES,
        formats=FORMATS,
    )

    def load(self, request: DataRequest) -> DataBundle:
        self.validate(request)
        config = loader_config(request)
        raw = load_gk2a(config)
        data: dict = {}
        source_files: dict = {}
        for time_key, time_item in raw.items():
            data[time_key] = {}
            source_files[time_key] = {}
            for channel, item in time_item.items():
                dataset = item["dataset"]
                values = dataset["image_pixel_values"].load().copy(deep=True)
                for key in (
                    "satellite_name",
                    "instrument_name",
                    "channel_spatial_resolution",
                    "channel_center_wavelength",
                ):
                    if key in dataset.attrs and key not in values.attrs:
                        values.attrs[key] = dataset.attrs[key]
                data[time_key][channel] = values
                source_files[time_key][channel] = [item["file"]]
                dataset.close()
        return _bundle(self.capabilities(), request, data, source_files)


def _bundle(caps, request, data, source_files) -> DataBundle:
    return bundle_from_parts(
        caps.id,
        request,
        data,
        source_files,
        {
            "loader_module": "adapters.gk2a.source",
            "version": caps.version,
            "n_times": len(data),
            "n_channel_fields": sum(len(item) for item in data.values()),
        },
    )


ADAPTER = Source()
