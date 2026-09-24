from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
import os
import re
import yaml

# IMPORTANT:
# MTG FCI L1c uses the EUMETSAT FCIDECOMP HDF5 filter (ID 32018).
# netCDF4 may use a different HDF5 runtime from h5py, so simply
# `import hdf5plugin` is not always enough on Windows.
# We explicitly expose hdf5plugin's plugin directory via HDF5_PLUGIN_PATH
# BEFORE Satpy/netCDF4 is imported.
try:
    import hdf5plugin
except ImportError as e:
    raise ImportError(
        "\nMTG FCI Loader requires 'hdf5plugin'.\n"
        "Install it in the SAME Python environment with:\n"
        "    python -m pip install hdf5plugin\n"
    ) from e

# Make FCIDECOMP visible to netCDF4/HDF5-based readers.
os.environ["HDF5_PLUGIN_PATH"] = str(hdf5plugin.PLUGIN_PATH)

# Also explicitly register FCIDECOMP for h5py-based access.
try:
    hdf5plugin.register(filters=("fcidecomp",), force=True)
except Exception:
    # HDF5_PLUGIN_PATH is the critical part for netCDF4;
    # registration here is an extra safeguard.
    pass

from satpy import Scene


RC_RE = re.compile(r"_(\d{4})_(\d{4})\.nc$", re.IGNORECASE)
OPE_RE = re.compile(r"OPE_(\d{14})_", re.IGNORECASE)


def parse_time(time_str):
    dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_requested_times(initial_time, history_steps, interval_minutes):
    end_time = parse_time(initial_time)
    if history_steps < 1:
        raise ValueError("'history_steps' must be >= 1")
    if interval_minutes < 1:
        raise ValueError("'interval_minutes' must be >= 1")
    return [
        end_time - timedelta(minutes=interval_minutes * i)
        for i in range(history_steps - 1, -1, -1)
    ]


def validate_bbox(config):
    region = config.get("region", {})
    bbox = region.get("bbox")

    if bbox is None:
        return None

    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(
            "'region.bbox' must be [west, south, east, north]"
        )

    west, south, east, north = map(float, bbox)

    if west >= east:
        raise ValueError("region.bbox requires west < east")
    if south >= north:
        raise ValueError("region.bbox requires south < north")

    return [west, south, east, north]


def normalize_channel(channel):
    text = str(channel).lower().strip()
    if not text:
        raise ValueError("MTG FCI channel must not be empty")
    return text


def parse_mtg_file(path):
    if path.suffix.lower() != ".nc":
        return None

    rc_match = RC_RE.search(path.name)
    ope_match = OPE_RE.search(path.name)

    if not rc_match or not ope_match:
        return None

    repeat_cycle = int(rc_match.group(1))
    chunk_number = int(rc_match.group(2))

    observation_start = datetime.strptime(
        ope_match.group(1),
        "%Y%m%d%H%M%S",
    ).replace(tzinfo=timezone.utc)

    day_start = observation_start.replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    nominal_start = day_start + timedelta(
        minutes=(repeat_cycle - 1) * 10
    )

    upper_name = path.name.upper()

    if "FDHSI" in upper_name:
        product_type = "FDHSI"
    elif "HRFI" in upper_name:
        product_type = "HRFI"
    else:
        product_type = "UNKNOWN"

    # EUMETSAT test set includes one trailer file per product.
    # Satpy's image reader does not need the trailer for channel loading.
    file_role = "TRAIL" if "TRAIL" in upper_name else "BODY"

    return {
        "path": path,
        "repeat_cycle": repeat_cycle,
        "chunk_number": chunk_number,
        "observation_start": observation_start,
        "nominal_start": nominal_start,
        "product_type": product_type,
        "file_role": file_role,
    }


def scan_local_files(data_dir):
    records = []

    for path in data_dir.rglob("*.nc"):
        if not path.is_file():
            continue

        record = parse_mtg_file(path)
        if record is not None:
            records.append(record)

    return records


def find_mtg_cycle_files(records, requested_time):
    target = requested_time.replace(second=0, microsecond=0)

    cycle_records = [
        r for r in records
        if r["nominal_start"] == target
    ]

    if not cycle_records:
        available_times = sorted(set(
            r["nominal_start"].strftime("%Y-%m-%dT%H:%M:%SZ")
            for r in records
        ))

        raise FileNotFoundError(
            "\nMTG FCI requested time not found\n"
            f"requested_time = {target.isoformat()}\n"
            f"available_times = {available_times[:30]}"
        )

    # Only BODY chunks are passed to Satpy.
    body_records = [
        r for r in cycle_records
        if r["file_role"] == "BODY"
    ]

    if not body_records:
        raise FileNotFoundError(
            "\nMTG FCI BODY chunks not found\n"
            f"requested_time = {target.isoformat()}"
        )

    files = sorted(
        [r["path"] for r in body_records],
        key=lambda p: p.name,
    )

    return files, cycle_records, body_records


def load_one_time(files, channels):
    scene = Scene(
        filenames=[str(p) for p in files],
        reader="fci_l1c_nc",
    )

    available = set(scene.available_dataset_names())

    missing_channels = [
        ch for ch in channels
        if ch not in available
    ]

    if missing_channels:
        common_channels = sorted([
            str(name)
            for name in available
            if re.fullmatch(
                r"(vis|nir|ir|wv)_\d+",
                str(name).lower(),
            )
        ])

        raise FileNotFoundError(
            "\nMTG FCI requested channel not found\n"
            f"requested_channels = {missing_channels}\n"
            f"available_channels = {common_channels}"
        )

    scene.load(
        channels,
        upper_right_corner="NE",
    )

    result = {}

    for channel in channels:
        try:
            data = scene[channel].load().copy(deep=True)
        except RuntimeError as e:
            if "filter" in str(e).lower():
                raise RuntimeError(
                    "\nMTG FCI decompression filter failed.\n"
                    "The FCI NetCDF files are compressed and require hdf5plugin/FCIDECOMP.\n"
                    "Run in the same environment:\n"
                    "    python -m pip install --upgrade hdf5plugin h5py netCDF4\n"
                    "Then restart the terminal/Python process and run again."
                ) from e
            raise

        print("\nLoaded channel  :", channel)
        print("Satellite       :", data.attrs.get("platform_name"))
        print("Instrument      :", data.attrs.get("sensor"))
        print("Resolution(m)   :", data.attrs.get("resolution"))
        print("Units           :", data.attrs.get("units"))
        print("Standard name   :", data.attrs.get("standard_name"))
        print("Calibration     :", data.attrs.get("calibration"))
        print("Image shape     :", tuple(data.shape))

        result[channel] = data

    return result


def load_mtg_fci(config):
    required = ["data_dir", "initial_time", "channels"]
    missing = [k for k in required if k not in config]

    if missing:
        raise ValueError(
            f"Missing required config fields: {missing}"
        )

    data_dir = Path(config["data_dir"])

    if not data_dir.exists():
        raise FileNotFoundError(
            f"Data directory does not exist: {data_dir}"
        )

    channels = config["channels"]
    if not isinstance(channels, list) or not channels:
        raise ValueError(
            "'channels' must be a non-empty list"
        )

    channels = [normalize_channel(c) for c in channels]

    history_steps = int(config.get("history_steps", 1))
    interval_minutes = int(config.get("interval_minutes", 10))
    bbox = validate_bbox(config)

    requested_times = build_requested_times(
        config["initial_time"],
        history_steps,
        interval_minutes,
    )

    records = scan_local_files(data_dir)

    if not records:
        raise FileNotFoundError(
            "\nNo recognizable MTG FCI L1c NetCDF files found\n"
            f"data_dir = {data_dir}"
        )

    print("=== MTG FCI Loader Request ===")
    print("FCIDECOMP path   :", os.environ.get("HDF5_PLUGIN_PATH"))
    print("data_dir         :", data_dir)
    print("initial_time     :", config["initial_time"])
    print("history_steps    :", history_steps)
    print("interval_minutes :", interval_minutes)
    print("channels         :", channels)
    print(
        "region           :",
        "full disk" if bbox is None else bbox,
    )

    print("requested_times  :")
    for t in requested_times:
        print("  -", t.isoformat())

    results = {}

    for requested_time in requested_times:
        time_key = requested_time.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        print(f"\n--- Time: {time_key} ---")

        files, all_cycle_records, body_records = find_mtg_cycle_files(
            records,
            requested_time,
        )

        counts_all = {}
        counts_body = {}

        for r in all_cycle_records:
            counts_all[r["product_type"]] = (
                counts_all.get(r["product_type"], 0) + 1
            )
        for r in body_records:
            counts_body[r["product_type"]] = (
                counts_body.get(r["product_type"], 0) + 1
            )

        print("Cycle files      :", counts_all)
        print("BODY files used  :", counts_body)

        channel_data = load_one_time(
            files,
            channels,
        )

        results[time_key] = {
            "files": [str(p) for p in files],
            "channels": channel_data,
        }

    print("\n=== MTG FCI Loader Success ===")
    print("Loaded times    :", len(requested_times))
    print("Loaded channels :", channels)
    print(
        "Requested region:",
        "full disk" if bbox is None else bbox,
    )

    if bbox is not None:
        print(
            "Note            : region is recorded only; "
            "actual spatial crop is not performed in v1.1."
        )

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        required=True,
        help="Path to YAML config",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    load_mtg_fci(config)


if __name__ == "__main__":
    main()
