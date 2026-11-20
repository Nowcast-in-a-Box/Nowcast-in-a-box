"""Create one NiB run directory from the CMA CREF pySTEPS test window.

This is a local bridge while the final Handler/Data Adapter integration is
being agreed.  It intentionally reuses ``scripts.test_pysteps_cma.load_window``
so the container receives exactly the same raw CMA window used by the
standalone optical-flow test.

Example (from the repository root)::

    .\\.venv312\\Scripts\\python.exe -m scripts.prepare_pysteps_cma_run \\
      --data-dir "C:\\dataloader\\CREF20240705\\05" \\
      --start "2024-07-05T00:00:00Z" --bbox 117 31 119 33 \\
      --runs "runs" --id pysteps-cma-20240705-0000
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from scripts.test_pysteps_cma import load_window
from utils.stored_fields import create_run


def _run_window(raw_window: dict, bbox: tuple[float, float, float, float]) -> tuple[dict, dict]:
    """Make a StoredFields window and an EPSG:4326 grid from one CMA window.

    CMA files enumerate latitude north-to-south.  StoredFields requires
    strictly increasing grid axes, so reverse the row axis in *both* context
    and target.  This changes only storage order; each value retains its
    original geographic latitude.
    """
    longitude = np.asarray(raw_window["longitude"], dtype=np.float64)
    latitude = np.asarray(raw_window["latitude"], dtype=np.float64)
    context = np.asarray(raw_window["context"], dtype=np.float32)
    target = np.asarray(raw_window["target"], dtype=np.float32)
    if longitude.ndim != 1 or latitude.ndim != 1:
        raise ValueError("CMA CREF coordinates must be one-dimensional")
    if context.shape[1:] != (latitude.size, longitude.size):
        raise ValueError("CMA CREF values and coordinates have inconsistent shapes")
    if np.all(np.diff(longitude) < 0):
        longitude = longitude[::-1]
        context = context[..., ::-1]
        target = target[..., ::-1]
    elif not np.all(np.diff(longitude) > 0):
        raise ValueError("CMA longitude axis must be strictly monotonic")
    if np.all(np.diff(latitude) < 0):
        latitude = latitude[::-1]
        context = context[..., ::-1, :]
        target = target[..., ::-1, :]
    elif not np.all(np.diff(latitude) > 0):
        raise ValueError("CMA latitude axis must be strictly monotonic")

    if not np.isfinite(context).all():
        raise ValueError(
            "Selected CMA history contains missing pixels; pySTEPS LK requires complete context"
        )
    run_window = {
        "data_id": "cma_radar",
        "variable": "CREF",
        "unit": "dBZ",
        "cadence_minutes": 6,
        "context": context,
        "target": target,
        "context_times": raw_window["context_times"],
        "target_times": raw_window["target_times"],
        "context_start_time": raw_window["context_times"][0],
        "init_time": raw_window["init_time"],
        "event_id": raw_window["context_times"][0][:10].replace("-", ""),
        "sample_id": f"cma-cref@{raw_window['context_times'][0]}",
        "row": 0,
        "provenance": {
            "adapter": "cma_radar",
            "preparation": "local bridge for pySTEPS LK model-container validation",
            "source_files": raw_window["source_files"],
            "requested_bbox_lonlat": list(bbox),
            "storage_row_order": "south_to_north (CMA source was north_to_south)",
        },
    }
    grid = {
        "crs": "EPSG:4326",
        "x": longitude,
        "y": latitude,
        "axis_units": "degrees",
        "source": "CMA CREF native latitude/longitude crop",
    }
    return run_window, grid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="CMA CREF directory")
    parser.add_argument("--start", required=True, help="First history time in UTC")
    parser.add_argument(
        "--bbox",
        required=True,
        nargs=4,
        type=float,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="CMA crop in longitude/latitude degrees",
    )
    parser.add_argument("--runs", required=True, help="Existing directory holding NiB runs")
    parser.add_argument("--id", required=True, help="New run ID: letters, digits, _ and - only")
    parser.add_argument(
        "--atlas-revision",
        default="local-pysteps-cma-bridge-v1",
        help="Provenance label until the real Data Adapter supplies one",
    )
    args = parser.parse_args()

    root = Path(args.runs).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"--runs directory must already exist: {root}")
    bbox = tuple(args.bbox)
    raw_window = load_window(args.data_dir, args.start, bbox)
    window, grid = _run_window(raw_window, bbox)
    directory = create_run(
        root, args.id, window, grid, atlas_revision=args.atlas_revision
    )
    print(f"Created NiB run: {directory}")
    print("  manifest.json")
    print("  inputs/context.npz   (6 CMA CREF history frames)")
    print("  inputs/target.npz    (20 verifying CMA CREF frames)")


if __name__ == "__main__":
    main()
