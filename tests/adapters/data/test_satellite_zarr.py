"""Satellite Zarr dataset construction tests. No observation files."""

from __future__ import annotations

import unittest

import numpy as np
import xarray as xr

from adapters.satellite_zarr import build_satellite_dataset


class _FakeCRS:
    def to_wkt(self) -> str:
        return 'PROJCRS["fake-geos"]'


class _FakeArea:
    area_id = "demo_area"
    description = "demo projected area"
    proj_id = "geos"
    area_extent = (-2.0, -1.0, 2.0, 1.0)
    pixel_size_x = 1.0
    pixel_size_y = 1.0
    crs = _FakeCRS()


def _bundle() -> dict:
    c13_t0 = xr.DataArray(
        np.arange(12, dtype=np.float32).reshape(3, 4),
        dims=("y", "x"),
        coords={
            "y": xr.DataArray([1.0, 0.0, -1.0], dims=("y",), attrs={"units": "meter"}),
            "x": xr.DataArray([-2.0, -1.0, 0.0, 1.0], dims=("x",), attrs={"units": "meter"}),
            "crs": xr.DataArray(_FakeCRS()),
        },
        attrs={
            "platform_name": "FY-4B",
            "sensor": "agri",
            "name": "C13",
            "resolution": 4000,
            "units": "K",
            "standard_name": "toa_brightness_temperature",
            "area": _FakeArea(),
        },
    )
    c13_t1 = c13_t0.copy(deep=True)

    # GK2A-like field: native pixel dimensions with no spatial coordinates.
    sw038_t0 = xr.DataArray(
        np.arange(6, dtype=np.uint16).reshape(2, 3),
        dims=("dim_image_y", "dim_image_x"),
        attrs={
            "satellite_name": "GK-2A",
            "instrument_name": "AMI",
            "channel_name": "sw038",
            "channel_spatial_resolution": 2.0,
        },
    )
    sw038_t1 = sw038_t0.copy(deep=True)

    return {
        "source": "fy4b",
        "request": {
            "region": {"bbox": [100.0, 10.0, 145.0, 55.0]},
        },
        "data": {
            "2024-09-16T00:00:00Z": {"C13": c13_t0, "sw038": sw038_t0},
            "2024-09-16T00:15:00Z": {"C13": c13_t1, "sw038": sw038_t1},
        },
        "source_files": {
            "2024-09-16T00:00:00Z": {
                "C13": ["a.hdf"],
                "sw038": ["a.nc"],
            },
            "2024-09-16T00:15:00Z": {
                "C13": ["b.hdf"],
                "sw038": ["b.nc"],
            },
        },
        "metadata": {"version": "1.0.0"},
    }


class SatelliteZarrDatasetTests(unittest.TestCase):
    def test_multiple_native_grids_share_one_dataset(self) -> None:
        ds = build_satellite_dataset(_bundle())

        self.assertEqual(ds.sizes["time"], 2)
        self.assertEqual(ds["C13"].dims, ("time", "C13_y", "C13_x"))
        self.assertEqual(ds["C13"].shape, (2, 3, 4))
        self.assertEqual(ds["sw038"].dims, ("time", "sw038_y", "sw038_x"))
        self.assertEqual(ds["sw038"].shape, (2, 2, 3))

        self.assertIn("C13_y", ds.coords)
        self.assertIn("C13_x", ds.coords)
        self.assertNotIn("sw038_y", ds.coords)
        self.assertNotIn("sw038_x", ds.coords)

        self.assertEqual(ds["C13"].attrs["units"], "K")
        self.assertIn("crs_wkt", ds["C13"].attrs)
        self.assertIn("native pixel dimensions", ds["sw038"].attrs["spatial_metadata_status"])
        self.assertEqual(ds.attrs["source_file_count"], 4)

    def test_grid_change_is_rejected(self) -> None:
        bundle = _bundle()
        bundle["data"]["2024-09-16T00:15:00Z"]["C13"] = xr.DataArray(
            np.zeros((4, 4), dtype=np.float32),
            dims=("y", "x"),
        )
        with self.assertRaises(ValueError):
            build_satellite_dataset(bundle)


if __name__ == "__main__":
    unittest.main()
