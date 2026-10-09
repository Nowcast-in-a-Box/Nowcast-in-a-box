"""Fully decode the SQ-selected ETOPO 2022 30s surface file and record its checksum.

Requires numpy and netCDF4 (an acquisition-only tool, not a renderer dependency).
Usage: python scripts/verify_etopo.py FILE --report REPORT.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import netCDF4
import numpy as np

EXPECTED_BYTES = 1_642_335_281


def verify(path: Path) -> dict:
    if path.stat().st_size != EXPECTED_BYTES:
        raise ValueError(f"Expected {EXPECTED_BYTES} bytes, got {path.stat().st_size}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()

    with netCDF4.Dataset(path) as dataset:
        z = dataset.variables["z"]
        if z.dimensions != ("lat", "lon") or z.shape != (21600, 43200):
            raise ValueError(f"Unexpected elevation grid: {z.dimensions}, {z.shape}")
        if z.units != "meters" or z.vert_crs_epsg != "EPSG:3855" or z.positive != "up":
            raise ValueError("Unexpected elevation units or vertical datum")
        crs = dataset.variables["crs"]
        if 'AUTHORITY["EPSG","4326"]' not in crs.spatial_ref:
            raise ValueError("Expected WGS84 / EPSG:4326")
        coordinates = {}
        axes = {}
        for name, size, edge, units in (
            ("lat", 21600, 90, "degrees_north"),
            ("lon", 43200, 180, "degrees_east"),
        ):
            axis = dataset.variables[name]
            values = np.asarray(axis[:])
            if axis.units != units or values.shape != (size,):
                raise ValueError(f"Unexpected {name} coordinate metadata")
            expected = -edge + (np.arange(size) + 0.5) / 120
            ascending = bool(values[-1] > values[0])
            np.testing.assert_allclose(
                values, expected if ascending else expected[::-1], rtol=0, atol=1e-9
            )
            axes[name] = values
            coordinates[name] = {
                "count": size,
                "first": float(values[0]),
                "last": float(values[-1]),
                "spacing_degrees": float(values[1] - values[0]),
                "units": units,
            }

        z.set_auto_maskandscale(False)
        chunks = z.chunking()
        tile_y, tile_x = chunks if isinstance(chunks, list) else (360, 720)
        count = missing = nonfinite = 0
        minimum, maximum = float("inf"), float("-inf")
        for y in range(0, z.shape[0], tile_y):
            for x in range(0, z.shape[1], tile_x):
                values = z[y : y + tile_y, x : x + tile_x]
                count += values.size
                missing += int(np.count_nonzero(values == z._FillValue))
                nonfinite += int(np.count_nonzero(~np.isfinite(values)))
                minimum = min(minimum, float(values.min()))
                maximum = max(maximum, float(values.max()))
        if missing or nonfinite:
            raise ValueError(f"Unexpected missing/nonfinite cells: {missing}/{nonfinite}")
        if count != 21600 * 43200:
            raise ValueError(f"Incomplete scan: {count} cells")
        samples = []
        for lat, lon in ((0, -140), (30, 90), (52, 13), (31, 114)):
            y = int(np.argmin(np.abs(axes["lat"] - lat)))
            x = int(np.argmin(np.abs(axes["lon"] - lon)))
            samples.append(
                {
                    "requested_lat_lon": [lat, lon],
                    "cell_lat_lon": [float(axes["lat"][y]), float(axes["lon"][x])],
                    "elevation_m": float(z[y, x]),
                }
            )
        return {
            "verified_at_utc": datetime.now(UTC).isoformat(),
            "size_bytes": path.stat().st_size,
            "sha256": digest,
            "checksum_basis": "Locally computed; no publisher checksum used",
            "format": dataset.data_model,
            "conventions": dataset.Conventions,
            "shape_lat_lon": list(z.shape),
            "dtype": str(z.dtype),
            "horizontal_crs": "EPSG:4326",
            "vertical_crs": z.vert_crs_epsg,
            "vertical_datum": z.vert_crs_name,
            "units": z.units,
            "geotransform": crs.GeoTransform,
            "coordinates": coordinates,
            "full_decode": {
                "cells": count,
                "missing": missing,
                "nonfinite": nonfinite,
                "minimum_m": minimum,
                "maximum_m": maximum,
            },
            "nearest_cell_samples": samples,
            "reader_versions": {
                "netCDF4": netCDF4.__version__,
                "numpy": np.__version__,
                "HDF5": netCDF4.__hdf5libversion__,
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.file)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
