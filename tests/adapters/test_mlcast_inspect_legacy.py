"""Regression test for the older metadata shape found in actual Windows QA."""
import numpy as np
import xarray as xr

from scripts.mlcast_inspect import print_mlcast_qa


def test_qa_recomputes_from_bundle_even_without_optional_metadata(capsys):
    coords = {"latitude": (("y", "x"), [[43.1, 43.2], [43.0, 43.1]]),
              "longitude": (("y", "x"), [[12.0, 12.1], [12.0, 12.1]])}
    data = {
        "2023-07-01T11:45:00Z": {"RR": xr.DataArray([[0.0, 1.0], [np.nan, 4.0]],
                                                       dims=("y", "x"), coords=coords,
                                                       attrs={"units": "kg m-2 h-1"})},
        "2023-07-01T11:50:00Z": {"RR": xr.DataArray([[2., 3.], [4., 5.]],
                                                       dims=("y", "x"), attrs={"units": "kg m-2 h-1"})},
    }
    bundle = {
        "source": "mlcast_it_dpc_sri_5min",
        "request": {"data_dir": "mlcast/it_dpc_sri_5min",
                    "region": {"bbox": [12, 43, 12.1, 43.2]}},
        "data": data,
        "source_files": {k: {"RR": ["not_existing_sample.zarr"]} for k in data},
        "metadata": {"n_times": 2, "n_channel_fields": 2},
    }
    print_mlcast_qa(bundle)
    report = capsys.readouterr().out
    assert "frame shape     : (2, 2)" in report
    assert "requested bbox  : [12, 43, 12.1, 43.2]" in report
    assert "variable / units: RR / kg m-2 h-1" in report
    assert "2023-07-01T11:45:00Z  RR  75.0%  0  4" in report
    assert "2023-07-01T11:50:00Z  RR  100.0%  2  5" in report
    assert "frame shape     : None" not in report
