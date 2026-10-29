"""Batch-prepare six additional MLCast radar datasets for the existing NiB Data Adapter.

Run from the NiB repository root (Windows PowerShell, Python 3.12 / Zarr 3):

    python -m scripts.mlcast_download_six --output-root ./demo_data

Each dataset gets its own directory under demo_data/mlcast/<dataset_id>/ and
an example_success.yaml under tests/adapters/requests/mlcast_<dataset_id>/.

This is a *preparation/test* script.  The NiB Data Adapter itself remains
strictly local and continues using the unchanged adapters.__main__.run().
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.download_mlcast_sample import get_source_catalog, main as download_one
from scripts.mlcast_make_request import make_request

# Existing Italian product is already accepted; do not overwrite it by default.
OTHER_SIX = (
    "radklim_hourly",
    "radklim_5_minutes",
    "dmi_10_minutes",
    "uk_metoffice_5min",
    "be_rmi_radclim_mfb_5min",
    "fr_mf_prate_5min",
)


def prepare_six(output_root="./demo_data", dataset_ids=OTHER_SIX, size=256,
                download_steps=12, history_steps=5, crop_fraction=0.8,
                overwrite=False, verify=True):
    """Download, write standard YAML and verify using the ORIGINAL NiB dispatcher.

    One failure is logged and processing continues for the remaining datasets.
    Existing complete samples are reused (unless --overwrite was supplied).
    Returns a dict of structured per-dataset outcomes.
    """
    catalog = get_source_catalog()
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not 1 <= history_steps < download_steps:
        raise ValueError("Require download_steps > history_steps >= 1 for an independent YAML selection test")
    report = {}
    for num, dataset_id in enumerate(dataset_ids, 1):
        print("\n" + "=" * 65, flush=True)
        print("[{}/{}] MLCast: {}".format(num, len(dataset_ids), dataset_id), flush=True)
        print("=" * 65, flush=True)
        info = catalog[dataset_id]
        data_folder = root / "mlcast" / dataset_id
        store = data_folder / "sample.zarr"
        req = data_folder / "request.yaml"
        record = {"dataset": dataset_id, "source": info["source"],
                  "variable": info["variable"], "cadence_minutes": info["cadence_minutes"],
                  "zarr": str(store), "status": "pending"}
        report[dataset_id] = record
        try:
            if store.is_dir() and req.is_file() and not overwrite:
                print("Using existing local Zarr and request.yaml (no download): {}".format(store))
                record["download"] = "reused"
            else:
                if store.exists() and not overwrite:
                    raise RuntimeError("Found sample.zarr without request.yaml: {}. "
                                       "Check the incomplete sample, then retry with --overwrite.".format(store))
                download_one([
                    "--dataset", dataset_id,
                    "--output-root", str(root),
                    "--size", str(size),
                    "--download-steps", str(download_steps),
                    "--history-steps", str(history_steps),
                    "--auto-time",
                ] + (["--overwrite"] if overwrite else []))
                record["download"] = "downloaded"

            example = make_request(
                str(root), dataset_id, history_steps=history_steps,
                shift_steps=1, crop_fraction=crop_fraction, sync_example=True,
            )
            repo_root = Path(__file__).resolve().parents[1]
            config = repo_root / "tests" / "adapters" / "requests" / info["source"] / "example_success.yaml"
            record["config"] = str(config)
            record["custom_config"] = str(example)
            if verify:
                # Crucially, the same existing NiB dispatcher is called, not
                # a parallel ad-hoc Zarr reader.
                from adapters.__main__ import run
                from scripts.mlcast_inspect import print_mlcast_qa
                bundle = run(str(config), str(root))
                if len(bundle["data"]) != history_steps:
                    raise AssertionError("Unexpected loaded time count")
                first_time = sorted(bundle["data"])[0]
                if list(bundle["data"][first_time]) != [info["variable"]]:
                    raise AssertionError("Incorrect variable in NiB DataBundle")
                print_mlcast_qa(bundle)
                record["frame_shape"] = list(bundle["metadata"].get("frame_shape") or [])
                record["grid_spacing"] = bundle["metadata"].get("grid_spacing")
                record["spatial_resolution_m"] = bundle["metadata"].get("spatial_resolution_m")
                record["loaded_times"] = len(bundle["data"])
            record["status"] = "passed" if verify else "prepared"
            print("[OK] {}: {}".format(dataset_id, record["status"]))
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = "{}: {}".format(type(exc).__name__, exc)
            print("[FAILED] {}: {}".format(dataset_id, record["error"]))
            print("Continuing with other datasets; see the JSON report at the end.")

    summary_path = root / "mlcast" / "download_report.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n=== MLCast six-dataset summary ===")
    for dataset_id, record in report.items():
        print("{:<30} {:<9} {}".format(dataset_id, record["status"], record.get("error", "")))
    print("Report:", summary_path)
    print("Data folders:", root / "mlcast")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-root", default="./demo_data", help="Local NiB data root")
    parser.add_argument("--size", type=int, default=256, help="Square local sample width/height, pixels")
    parser.add_argument("--download-steps", type=int, default=12, help="Contiguous frames saved per dataset")
    parser.add_argument("--history-steps", type=int, default=5, help="Frames in standard NiB YAML request")
    parser.add_argument("--crop-fraction", type=float, default=0.8, help="Central bbox fraction for YAML verification")
    parser.add_argument("--only", nargs="+", choices=OTHER_SIX, help="Retry only the named dataset(s)")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing sample.zarr and YAML")
    parser.add_argument("--no-verify", action="store_true", help="Download and generate YAML, skip NiB loading")
    args = parser.parse_args(argv)
    if args.size < 2 or args.download_steps < 2 or args.history_steps < 1 or not 0 < args.crop_fraction <= 1:
        parser.error("Require size>=2, download_steps>=2, history_steps>=1, 0<crop_fraction<=1")
    report = prepare_six(
        args.output_root, tuple(args.only or OTHER_SIX), size=args.size,
        download_steps=args.download_steps, history_steps=args.history_steps,
        crop_fraction=args.crop_fraction, overwrite=args.overwrite,
        verify=not args.no_verify,
    )
    if any(item["status"] == "failed" for item in report.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
