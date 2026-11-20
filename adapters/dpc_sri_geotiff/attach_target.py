"""Attach retrospective DPC GeoTIFF truth to a completed IRENE NiB run.

This module runs inside the DPC ingestion image, which already contains the
same GDAL/rasterio stack used to ingest the input scans.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from adapters.dpc_sri_geotiff.ingest import (
    CADENCE_MINUTES,
    _read_crop,
    scans,
    stamp,
    window_from_grid,
)
from utils.stored_fields import StoredFields, save_manifest, write_cube


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--data", required=True, type=Path, help="DPC GeoTIFF directory")
    parser.add_argument("--steps", type=int, default=12)
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be positive")

    access = StoredFields(args.run)
    if "target" in access.manifest["observations"]:
        raise FileExistsError("run already contains inputs/target.npz; do not overwrite truth")
    index = scans(args.data)
    initial = datetime.fromisoformat(
        access.manifest["sample"]["init_time"].replace("Z", "+00:00")
    )
    times = [initial + timedelta(minutes=CADENCE_MINUTES * step) for step in range(1, args.steps + 1)]
    missing = [stamp(moment) for moment in times if moment not in index]
    if missing:
        raise FileNotFoundError("missing DPC truth scans: " + ", ".join(missing))

    context = access.cube(access.manifest["observations"]["context"])
    grid = {
        **access.manifest["observations"]["context"]["grid"],
        "x": context["x"],
        "y": context["y"],
    }
    window = window_from_grid(grid)
    fields: list[np.ndarray] = []
    for moment in times:
        field, next_grid = _read_crop(index[moment], window)
        if (
            next_grid["crs"] != grid["crs"]
            or not np.array_equal(next_grid["x"], grid["x"])
            or not np.array_equal(next_grid["y"], grid["y"])
        ):
            raise ValueError(f"truth scan {index[moment].name} does not share the IRENE grid")
        fields.append(field)

    descriptor = write_cube(
        access.root,
        "inputs/target.npz",
        np.stack(fields).astype(np.float32),
        [stamp(moment) for moment in times],
        grid,
        variable="RR",
        unit="mm/h",
    )
    access.manifest["observations"]["target"] = descriptor
    provenance = access.manifest["sample"].setdefault("provenance", {})
    provenance["verifying_observations"] = {
        "native_input": "complete DPC SRI GeoTIFF scans",
        "source_files": [
            str(index[moment].relative_to(args.data)).replace("\\", "/") for moment in times
        ],
    }
    save_manifest(access.root, access.manifest)
    print(f"Attached {len(fields)} verifying DPC scans; target shape={descriptor['shape']}")


if __name__ == "__main__":
    main()
