"""
Concise inspection for a CMA Radar Zarr dataset.

Usage:
    python test_cma_zarr_simple.py --zarr cma_radar_test.zarr
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import xarray as xr


def fmt_time(value) -> str:
    """Format numpy/xarray datetime as readable UTC-like text."""
    try:
        return np.datetime_as_string(np.asarray(value).astype("datetime64[s]"), unit="s") + "Z"
    except Exception:
        return str(value)


def inspect(path: str | Path) -> None:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Zarr dataset does not exist: {path}")

    ds = xr.open_zarr(path, consolidated=True)

    da = ds["reflectivity"]

    print("=" * 58)
    print("CMA Radar Zarr - Quick Check")
    print("=" * 58)

    print(f"Path              : {path}")
    print(f"Variable          : reflectivity")
    print(f"Shape             : {tuple(da.shape)}")
    print(f"Dimensions        : {da.dims}")
    print(f"Data type         : {da.dtype}")
    print(f"Unit              : {da.attrs.get('units', '(not set)')}")
    print(f"Standard name     : {da.attrs.get('standard_name', '(not set)')}")

    print(f"Data time         : {fmt_time(ds['time'].values[0])}")
    if "generation_time" in ds.coords:
        print(f"Generation time   : {fmt_time(ds['generation_time'].values[0])}")

    lon = ds["longitude"]
    lat = ds["latitude"]
    print(
        f"Longitude range   : {float(lon.min().values):g} to "
        f"{float(lon.max().values):g}"
    )
    print(
        f"Latitude range    : {float(lat.min().values):g} to "
        f"{float(lat.max().values):g}"
    )

    print(f"Resolution        : {ds.attrs.get('native_resolution_deg', '(not set)')} degree")
    print(f"Source            : {ds.attrs.get('source', '(not set)')}")
    print(f"Product           : {ds.attrs.get('product', '(not set)')}")
    print(f"Requested bbox    : {ds.attrs.get('requested_bbox', '(not set)')}")
    print(f"Output bbox       : {ds.attrs.get('output_bbox', '(not set)')}")
    print(f"Zarr spec         : {ds.attrs.get('nib_zarr_spec_version', '(not set)')}")
    print(f"Spec status       : {ds.attrs.get('nib_zarr_spec_status', '(not set)')}")

    source_files = ds.attrs.get("source_file_names", [])
    print(f"Source file       : {source_files[0] if source_files else '(not set)'}")

    raw_min = float(da.min().values)
    raw_max = float(da.max().values)
    print(f"Raw value range   : {raw_min:g} to {raw_max:g} dBZ")

    if raw_min < -1000:
        print(
            "Data note         : very negative encoded values are present; "
            "their missing/QC meaning is not yet verified, so they are preserved."
        )

    required = {
        "time": "time" in ds.coords,
        "latitude": "latitude" in ds.coords,
        "longitude": "longitude" in ds.coords,
        "reflectivity": "reflectivity" in ds.data_vars,
    }
    ok = all(required.values())

    print("-" * 58)
    print("Basic structure   :", "PASS" if ok else "CHECK")
    for name, present in required.items():
        print(f"  {'[OK]' if present else '[MISSING]'} {name}")

    ds.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--zarr",
        default="cma_radar_test.zarr",
        help="Path to CMA radar Zarr dataset.",
    )
    args = parser.parse_args()
    inspect(args.zarr)


if __name__ == "__main__":
    main()
