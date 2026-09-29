from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
import re
import yaml

from satpy import Scene


FY4B_RE = re.compile(
    r"^FY4B-_AGRI--_N_(DISK|REGC|AREA)_([0-9A-Z]+)_L1-_"
    r"(FDI|GEO)-_MULT_NOM_(\d{14})_(\d{14})_"
    r"(\d{4}M)_V\d{4}\.HDF$",
    re.IGNORECASE,
)


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
    text = str(channel).upper().strip()

    if text.startswith("C"):
        number = text[1:]
    else:
        number = text

    if not number.isdigit():
        raise ValueError(
            "FY-4B AGRI channel must look like C13 or 13"
        )

    number = int(number)

    if number < 1 or number > 15:
        raise ValueError(
            "FY-4B AGRI channel must be between C01 and C15"
        )

    return "C{:02d}".format(number)


def parse_fy4b_file(path):
    m = FY4B_RE.match(path.name)
    if not m:
        return None

    return {
        "path": path,
        "area_type": m.group(1).upper(),
        "subsatellite": m.group(2).upper(),
        "product": m.group(3).upper(),
        "start_tag": m.group(4),
        "end_tag": m.group(5),
        "resolution": m.group(6).upper(),
    }


def scan_local_files(data_dir):
    records = []

    for path in data_dir.rglob("*.HDF"):
        if not path.is_file():
            continue

        record = parse_fy4b_file(path)
        if record is not None:
            records.append(record)

    # Also accept lower-case extension on case-sensitive systems.
    for path in data_dir.rglob("*.hdf"):
        if not path.is_file():
            continue

        record = parse_fy4b_file(path)
        if record is not None:
            records.append(record)

    # De-duplicate if filesystem search overlaps.
    unique = {}
    for r in records:
        unique[str(r["path"].resolve())] = r

    return list(unique.values())


def find_fy4b_files(records, requested_time):
    """
    Locate FY-4B AGRI L1 files by exact observation start time.

    At minimum, one FDI file must exist.
    A matching GEO file is included automatically when available.
    """
    time_tag = requested_time.strftime("%Y%m%d%H%M%S")

    time_records = [
        r for r in records
        if r["start_tag"] == time_tag
    ]

    if not time_records:
        available_times = sorted(set(
            r["start_tag"] for r in records
            if r["product"] == "FDI"
        ))

        raise FileNotFoundError(
            "\nFY-4B requested time not found\n"
            "requested_time = {}\n"
            "available_times = {}".format(
                requested_time.isoformat(),
                available_times[:20],
            )
        )

    fdi_records = [
        r for r in time_records
        if r["product"] == "FDI"
    ]

    if not fdi_records:
        raise FileNotFoundError(
            "\nFY-4B FDI file not found at requested time\n"
            "requested_time = {}".format(
                requested_time.isoformat()
            )
        )

    # Minimal rule:
    # use all matching L1 files at this exact start time.
    # This allows a paired GEO file to be passed to Satpy automatically.
    files = sorted(
        [r["path"] for r in time_records],
        key=lambda p: p.name,
    )

    return files, fdi_records


def load_one_time(files, channels):
    """
    Decode one FY-4B time using Satpy.
    """
    scene = Scene(
        filenames=[str(p) for p in files],
        reader="agri_fy4b_l1",
    )

    available = set(scene.available_dataset_names())

    missing_channels = [
        ch for ch in channels
        if ch not in available
    ]

    if missing_channels:
        available_channels = sorted(
            name for name in available
            if re.fullmatch(r"C\d{2}", str(name))
        )

        raise FileNotFoundError(
            "\nFY-4B requested channel not found\n"
            "requested_channels = {}\n"
            "available_channels = {}".format(
                missing_channels,
                available_channels,
            )
        )

    scene.load(channels)

    # Materialize data now so the result is independent of file handlers.
    result = {}

    for channel in channels:
        data = scene[channel].load().copy(deep=True)

        result[channel] = data

        print("\nLoaded channel  :", channel)
        print(
            "Satellite       :",
            data.attrs.get("platform_name"),
        )
        print(
            "Instrument      :",
            data.attrs.get("sensor"),
        )
        print(
            "Resolution(m)   :",
            data.attrs.get("resolution"),
        )
        print(
            "Units           :",
            data.attrs.get("units"),
        )
        print(
            "Standard name   :",
            data.attrs.get("standard_name"),
        )
        print(
            "Calibration     :",
            data.attrs.get("calibration"),
        )
        print(
            "Image shape     :",
            tuple(data.shape),
        )

    return result


def load_fy4b(config):
    """
    Minimal NiB FY-4B AGRI L1 loader.

    Required:
      data_dir
      initial_time
      channels

    Optional:
      history_steps      default 1
      interval_minutes   default 15
      region.bbox

    Supported raw input:
      FY-4B AGRI L1 HDF/HDF5 files with official naming convention.

    Notes:
      - local raw files only
      - no downloader
      - FDI is required
      - matching GEO is automatically included when present
      - region.bbox is validated/reported only in v1
    """
    required = ["data_dir", "initial_time", "channels"]
    missing = [k for k in required if k not in config]

    if missing:
        raise ValueError(
            "Missing required config fields: {}".format(missing)
        )

    data_dir = Path(config["data_dir"])

    if not data_dir.exists():
        raise FileNotFoundError(
            "Data directory does not exist: {}".format(data_dir)
        )

    channels = config["channels"]
    if not isinstance(channels, list) or not channels:
        raise ValueError(
            "'channels' must be a non-empty list"
        )

    channels = [normalize_channel(c) for c in channels]

    history_steps = int(config.get("history_steps", 1))
    interval_minutes = int(config.get("interval_minutes", 15))
    bbox = validate_bbox(config)

    requested_times = build_requested_times(
        config["initial_time"],
        history_steps,
        interval_minutes,
    )

    records = scan_local_files(data_dir)

    if not records:
        raise FileNotFoundError(
            "\nNo FY-4B AGRI L1 files found\n"
            "data_dir = {}\n"
            "Expected filename like:\n"
            "FY4B-_AGRI--_N_DISK_1050E_L1-_FDI-_MULT_NOM_"
            "20240916000000_20240916001459_4000M_V0001.HDF".format(
                data_dir
            )
        )

    print("=== FY-4B AGRI Loader Request ===")
    print("data_dir         :", data_dir)
    print("initial_time     :", config["initial_time"])
    print("history_steps    :", history_steps)
    print("interval_minutes :", interval_minutes)
    print("channels         :", channels)

    if bbox is None:
        print("region           : full disk")
    else:
        print(
            "region           : {} "
            "(west, south, east, north)".format(bbox)
        )

    print("requested_times  :")
    for t in requested_times:
        print("  -", t.isoformat())

    results = {}

    for requested_time in requested_times:
        time_key = requested_time.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        print("\n--- Time: {} ---".format(time_key))

        files, fdi_records = find_fy4b_files(
            records,
            requested_time,
        )

        print("Matched files    :", len(files))
        for p in files:
            print("  ", p.name)

        resolutions = sorted(set(
            r["resolution"] for r in fdi_records
        ))
        print("FDI resolutions  :", resolutions)

        channel_data = load_one_time(
            files,
            channels,
        )

        results[time_key] = {
            "files": [str(p) for p in files],
            "channels": channel_data,
        }

    print("\n=== FY-4B AGRI Loader Success ===")
    print("Loaded times    :", len(requested_times))
    print("Loaded channels :", channels)
    print(
        "Requested region:",
        "full disk" if bbox is None else bbox,
    )

    if bbox is not None:
        print(
            "Note            : region is recorded only; "
            "actual spatial crop is not performed in v1."
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

    load_fy4b(config)


if __name__ == "__main__":
    main()
