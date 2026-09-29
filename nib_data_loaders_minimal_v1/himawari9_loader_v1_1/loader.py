from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
import bz2
import gc
import re
import shutil
import tempfile
import yaml

from satpy import Scene


HSD_RE = re.compile(
    r"^HS_(H08|H09)_(\d{8})_(\d{4})_B(\d{2})_"
    r"([A-Z0-9]+)_R(\d{2})_S(\d{2})(\d{2})\.DAT(?:\.bz2)?$",
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

    if text.startswith("B"):
        number = text[1:]
    else:
        number = text

    if not number.isdigit():
        raise ValueError(
            "Himawari channel must look like B13 or 13"
        )

    number = int(number)

    if number < 1 or number > 16:
        raise ValueError(
            "Himawari AHI channel must be between B01 and B16"
        )

    return "B{:02d}".format(number)


def parse_hsd_file(path):
    m = HSD_RE.match(path.name)
    if not m:
        return None

    return {
        "path": path,
        "satellite": m.group(1).upper(),
        "datetime_tag": m.group(2) + m.group(3),
        "channel": "B" + m.group(4),
        "area": m.group(5).upper(),
        "resolution_code": m.group(6),
        "segment_no": int(m.group(7)),
        "segment_total": int(m.group(8)),
    }


def scan_local_files(data_dir):
    records = []

    for path in data_dir.rglob("*"):
        if not path.is_file():
            continue

        record = parse_hsd_file(path)
        if record is not None:
            records.append(record)

    return records


def find_himawari_files(records, requested_time, channel):
    """
    Find all HSD segments for one Himawari-9 time/channel request.

    Distinguishes:
      1. requested time not found
      2. requested channel not found at that time
      3. segment set incomplete
    """
    channel = normalize_channel(channel)
    datetime_tag = requested_time.strftime("%Y%m%d%H%M")

    h09_records = [
        r for r in records
        if r["satellite"] == "H09"
    ]

    time_records = [
        r for r in h09_records
        if r["datetime_tag"] == datetime_tag
    ]

    if not time_records:
        available_times = sorted(set(
            r["datetime_tag"] for r in h09_records
        ))

        raise FileNotFoundError(
            "\nHimawari-9 requested time not found\n"
            "requested_time = {}\n"
            "available_times = {}".format(
                requested_time.isoformat(),
                available_times[:20],
            )
        )

    channel_records = [
        r for r in time_records
        if r["channel"] == channel
    ]

    if not channel_records:
        available_channels = sorted(set(
            r["channel"] for r in time_records
        ))

        raise FileNotFoundError(
            "\nHimawari-9 requested channel not found\n"
            "requested_time = {}\n"
            "requested_channel = {}\n"
            "available_channels_at_time = {}".format(
                requested_time.isoformat(),
                channel,
                available_channels,
            )
        )

    full_disk = [
        r for r in channel_records
        if r["area"] == "FLDK"
    ]
    if full_disk:
        channel_records = full_disk

    totals = sorted(set(
        r["segment_total"] for r in channel_records
    ))

    if len(totals) != 1:
        raise RuntimeError(
            "\nInconsistent Himawari segment metadata\n"
            "segment_totals = {}".format(totals)
        )

    expected_total = totals[0]
    present_segments = sorted(set(
        r["segment_no"] for r in channel_records
    ))

    expected_segments = list(range(1, expected_total + 1))
    missing_segments = [
        i for i in expected_segments
        if i not in present_segments
    ]

    if missing_segments:
        raise FileNotFoundError(
            "\nHimawari-9 segment files incomplete\n"
            "requested_time = {}\n"
            "channel = {}\n"
            "expected_segments = {}\n"
            "present_segments = {}\n"
            "missing_segments = {}".format(
                requested_time.isoformat(),
                channel,
                expected_total,
                present_segments,
                missing_segments,
            )
        )

    channel_records = sorted(
        channel_records,
        key=lambda r: r["segment_no"]
    )

    return [r["path"] for r in channel_records]


def prepare_satpy_files(raw_files, temp_dir):
    """
    Prepare raw Himawari files for Satpy.

    - .DAT      -> use directly
    - .DAT.bz2  -> automatically decompress to temp_dir

    The original files are never modified.
    """
    prepared = []

    for src in raw_files:
        name_lower = src.name.lower()

        if name_lower.endswith(".dat.bz2"):
            dst = Path(temp_dir) / src.name[:-4]

            print("Decompressing     :", src.name)

            with bz2.open(src, "rb") as f_in:
                with open(dst, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

            prepared.append(dst)

        elif name_lower.endswith(".dat"):
            prepared.append(src)

        else:
            raise ValueError(
                "Unsupported Himawari file type: {}".format(src)
            )

    return prepared


def load_one_channel(raw_files, channel):
    """
    Decode one channel from raw .DAT or .DAT.bz2 files.

    For .bz2 input, decompression is fully automatic and temporary.
    The returned DataArray is loaded into memory before temp cleanup,
    avoiding the Satpy Windows temporary-file cleanup issue.
    """
    with tempfile.TemporaryDirectory(
        prefix="nib_himawari_"
    ) as temp_dir:

        prepared_files = prepare_satpy_files(
            raw_files,
            temp_dir,
        )

        scene = Scene(
            filenames=[str(p) for p in prepared_files],
            reader="ahi_hsd",
        )

        scene.load([channel])

        # Important on Windows:
        # materialize the dask-backed array before temporary files vanish.
        data = scene[channel].load()

        # Detach from Scene/file handlers.
        data = data.copy(deep=True)

        # Explicitly release Satpy handlers before temp directory cleanup.
        del scene
        gc.collect()

    return data


def load_himawari(config):
    """
    Minimal NiB Himawari-9 AHI HSD loader.

    Required:
      data_dir
      initial_time
      channels

    Optional:
      history_steps      default 1
      interval_minutes   default 10
      region.bbox

    Supported local raw inputs:
      - *.DAT
      - *.DAT.bz2

    Notes:
      - local files only
      - no downloader
      - .bz2 decompression is automatic
      - original raw files are never modified
      - region.bbox is validated/reported only in v1.1
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
            "\nNo Himawari HSD files found\n"
            "data_dir = {}\n"
            "Supported raw inputs: *.DAT and *.DAT.bz2\n"
            "Expected names like:\n"
            "HS_H09_20180205_0250_B13_FLDK_R20_S0110.DAT.bz2".format(
                data_dir
            )
        )

    print("=== Himawari-9 Loader Request ===")
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
        results[time_key] = {}

        print("\n--- Time: {} ---".format(time_key))

        for channel in channels:
            raw_files = find_himawari_files(
                records,
                requested_time,
                channel,
            )

            print("\nLoading channel :", channel)
            print("Matched segments:", len(raw_files))

            compressed_count = sum(
                1 for p in raw_files
                if p.name.lower().endswith(".bz2")
            )

            print(
                "Compressed input:",
                "{} / {}".format(
                    compressed_count,
                    len(raw_files),
                ),
            )

            data = load_one_channel(
                raw_files,
                channel,
            )

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
                "Image shape     :",
                tuple(data.shape),
            )

            results[time_key][channel] = {
                "raw_files": [str(p) for p in raw_files],
                "data": data,
            }

    print("\n=== Himawari-9 Loader Success ===")
    print("Loaded times    :", len(requested_times))
    print("Loaded channels :", channels)
    print(
        "Requested region:",
        "full disk" if bbox is None else bbox,
    )
    print(
        "Raw input       : .DAT / .DAT.bz2 "
        "(automatic decompression)"
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

    load_himawari(config)


if __name__ == "__main__":
    main()
