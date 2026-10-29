"""Regression test: CF time units must not collide with xarray Zarr encoding."""
import json
import numpy as np
import xarray as xr
from xarray.conventions import encode_cf_variable

from scripts.download_mlcast_sample import _prepare_temporal_attrs_for_zarr_v2


def test_preserve_rainfall_units_and_avoid_cf_datetime_collision():
    times = np.array(["2022-07-01T11:05:00", "2022-07-01T11:10:00"], dtype="datetime64[ns]")
    ds = xr.Dataset(
        {"prate": (("time", "y", "x"), np.array([[[1.0]], [[2.0]]], dtype="float32"),
                   {"units": "kg m-2 h-1"})},
        coords={"time": ("time", times, {"units": "minutes since 1970-01-01", "calendar": "proleptic_gregorian"}),
                "x": [1000.0], "y": [2000.0]},
    )
    # Unfixed input reproduces the actual French error.
    try:
        encode_cf_variable(ds.variables["time"])
        assert False, "Expected the CF datetime units collision"
    except ValueError as exc:
        assert "Key 'units' already exists" in str(exc)

    _prepare_temporal_attrs_for_zarr_v2(ds)
    encoded = encode_cf_variable(ds.variables["time"])
    assert "units" in encoded.attrs
    assert ds["prate"].attrs["units"] == "kg m-2 h-1"
    provenance = json.loads(ds.attrs["nib_source_temporal_attrs"])
    assert provenance["time"]["units"] == "minutes since 1970-01-01"
    assert provenance["time"]["calendar"] == "proleptic_gregorian"
    assert ds["prate"].dtype == np.float32
    assert ds["prate"].shape == (2, 1, 1)
