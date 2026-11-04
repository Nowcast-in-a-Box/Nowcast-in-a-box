"""Offline unit tests of the seven MLCast source adapters (no remote access)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import xarray as xr
from pyproj import CRS, Transformer

from adapters.__main__ import adapter_for
from adapters.mlcast_common import _latlon
from interfaces.data import DataRequest
from interfaces.errors import ConstraintError

SOURCE_INFO = [
    ("radklim_hourly", "rainfall_amount", 60),
    ("radklim_5_minutes", "rainfall_amount", 5),
    ("dmi_10_minutes", "dbz", 10),
    ("it_dpc_sri_5min", "RR", 5),
    ("uk_metoffice_5min", "RR", 5),
    ("be_rmi_radclim_mfb_5min", "rain_rate", 5),
    ("fr_mf_prate_5min", "prate", 5),
]


def fake_dataset(var, minutes):
    end = np.datetime64("2023-07-01T12:00:00", "ns")
    times = end - np.arange(4, -1, -1) * np.timedelta64(minutes, "m")
    data = np.arange(5 * 7 * 9, dtype="float32").reshape(5, 7, 9)
    ds = xr.Dataset(
        {var: (("time", "y", "x"), data,
               {"units": "dBZ" if var == "dbz" else "mm/h", "grid_mapping": "crs"})},
        coords={
            "time": times,
            "y": np.arange(7) * 1000.0,
            "x": np.arange(9) * 1000.0,
            "lat": (("y", "x"), np.linspace(49, 51, 7)[:, None] * np.ones((1, 9))),
            "lon": (("y", "x"), np.ones((7, 1)) * np.linspace(7, 9, 9)[None, :]),
        },
    )
    ds["crs"] = xr.DataArray(0, attrs={"crs_wkt": CRS.from_epsg(3035).to_wkt()})
    return ds


@pytest.mark.parametrize("dataset_id,variable,cadence", SOURCE_INFO)
def test_registered_loader_uses_local_path_and_keeps_native_variable(tmp_path, dataset_id, variable, cadence):
    folder = tmp_path / dataset_id
    folder.mkdir()
    (folder / "sample.zarr").mkdir()
    request = DataRequest(
        source=f"mlcast_{dataset_id}", data_dir=str(folder), data_root=None,
        initial_time="2023-07-01T12:00:00Z", history_steps=3,
        interval_minutes=cadence, channels=(variable,), bbox=(7.3, 49.4, 8.8, 50.8),
    )
    ds = fake_dataset(variable, cadence)
    with patch("adapters.mlcast_common.xr.open_zarr", return_value=ds) as opening:
        bundle = adapter_for(request.source).load(request)
    assert opening.call_args.args[0] == folder / "sample.zarr"
    assert len(bundle["data"]) == 3
    assert bundle["metadata"]["model_ready"] is False
    assert bundle["metadata"]["n_channel_fields"] == 3
    assert bundle["metadata"]["dataset_id"] == dataset_id
    for row in bundle["data"].values():
        f = row[variable]
        assert f.ndim == 2 and 0 < f.shape[0] < 7 and 0 < f.shape[1] < 9
        assert f.attrs["units"] == ("dBZ" if variable == "dbz" else "mm/h")
        assert "latitude" in f.coords and "longitude" in f.coords


def test_missing_frame_is_an_error(tmp_path):
    (tmp_path / "sample.zarr").mkdir()
    request = DataRequest(
        source="mlcast_it_dpc_sri_5min", data_dir=str(tmp_path), data_root=None,
        initial_time="2023-07-01T12:03:00Z", history_steps=2,
        interval_minutes=5, channels=("RR",), bbox=(7.3, 49.4, 8.8, 50.8),
    )
    with patch("adapters.mlcast_common.xr.open_zarr", return_value=fake_dataset("RR", 5)):
        with pytest.raises(FileNotFoundError, match="Missing requested historical frames"):
            adapter_for(request.source).load(request)


def test_rejects_wrong_cadence_and_channels(tmp_path):
    folder = tmp_path / "sample.zarr"
    folder.mkdir()
    req = DataRequest("mlcast_radklim_hourly", str(folder), None, "2023-07-01T12:00:00Z", 2, 5,
                      ("rainfall_amount",), (7, 49, 9, 51))
    with pytest.raises(ConstraintError, match="native cadence"):
        adapter_for(req.source).validate(req)


def test_projected_grid_can_generate_geolocation():
    crs = CRS.from_epsg(3035)
    dx, dy = np.meshgrid([4000000.0, 4001000.0], [2900000.0, 2901000.0])
    ds = xr.Dataset({"RR": (("y", "x"), np.ones((2, 2)), {"grid_mapping": "crs"})},
                    coords={"y": [2900000.0, 2901000.0], "x": [4000000.0, 4001000.0]})
    ds["crs"] = xr.DataArray(0, attrs={"crs_wkt": crs.to_wkt()})
    lat, lon = _latlon(ds["RR"], ds)
    expected_lon, expected_lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(dx, dy)
    np.testing.assert_allclose(lat, expected_lat)
    np.testing.assert_allclose(lon, expected_lon)


def test_no_network_or_unverified_grid(tmp_path):
    folder = tmp_path / "sample.zarr"
    folder.mkdir()
    req = DataRequest("mlcast_it_dpc_sri_5min", str(folder), None,
                      "2023-07-01T12:00:00Z", 1, 5, ("RR",), (7, 49, 9, 51))
    ds = fake_dataset("RR", 5).drop_vars(["lat", "lon", "crs"])
    with patch("adapters.mlcast_common.xr.open_zarr", return_value=ds):
        with pytest.raises(ConstraintError, match="no usable latitude/longitude"):
            adapter_for(req.source).load(req)


def test_real_local_zarr_roundtrip(tmp_path):
    """End-to-end local Zarr read/write, skipped only when zarr is not installed."""
    pytest.importorskip("zarr")
    folder = tmp_path / "it_dpc_sri_5min"
    folder.mkdir()
    fake_dataset("RR", 5).to_zarr(str(folder / "sample.zarr"), mode="w", consolidated=True)
    request = DataRequest(
        source="mlcast_it_dpc_sri_5min", data_dir=str(folder), data_root=None,
        initial_time="2023-07-01T12:00:00Z", history_steps=3,
        interval_minutes=5, channels=("RR",), bbox=(7.3, 49.4, 8.8, 50.8),
    )
    bundle = adapter_for(request.source).load(request)
    assert bundle["metadata"]["n_times"] == 3
    assert bundle["metadata"]["local_store"].endswith("sample.zarr")
    for channels in bundle["data"].values():
        assert channels["RR"].ndim == 2
        assert channels["RR"].attrs["units"] == "mm/h"
        assert np.isfinite(channels["RR"].values).any()


def test_partial_outside_bbox_must_fail(tmp_path):
    """Prior behavior silently intersected; current contract rejects it."""
    (tmp_path / "sample.zarr").mkdir()
    req = DataRequest(
        source="mlcast_it_dpc_sri_5min", data_dir=str(tmp_path), data_root=None,
        initial_time="2023-07-01T12:00:00Z", history_steps=2,
        interval_minutes=5, channels=("RR",), bbox=(6.9, 49.5, 8.0, 50.5),
    )
    with patch("adapters.mlcast_common.xr.open_zarr", return_value=fake_dataset("RR", 5)):
        with pytest.raises(ConstraintError, match="exceeds the local sample"):
            adapter_for(req.source).load(req)


def test_edit_yaml_controls_time_area_and_qa(tmp_path):
    (tmp_path / "sample.zarr").mkdir()
    req = DataRequest(
        source="mlcast_it_dpc_sri_5min", data_dir=str(tmp_path), data_root=None,
        initial_time="2023-07-01T11:55:00Z", history_steps=2,
        interval_minutes=5, channels=("RR",), bbox=(7.75, 49.7, 8.25, 50.3),
    )
    with patch("adapters.mlcast_common.xr.open_zarr", return_value=fake_dataset("RR", 5)):
        bundle = adapter_for(req.source).load(req)
    assert list(bundle["data"]) == ["2023-07-01T11:50:00Z", "2023-07-01T11:55:00Z"]
    assert bundle["metadata"]["frame_shape"] == (1, 3)
    assert len(bundle["metadata"]["frame_stats"]) == 2
    for stat in bundle["metadata"]["frame_stats"]:
        assert stat["valid_fraction"] == 1.0
        assert stat["max"] >= stat["min"]
    assert bundle["metadata"]["units"] == "mm/h"


def test_rejects_all_nan_frame(tmp_path):
    (tmp_path / "sample.zarr").mkdir()
    req = DataRequest(
        source="mlcast_it_dpc_sri_5min", data_dir=str(tmp_path), data_root=None,
        initial_time="2023-07-01T12:00:00Z", history_steps=1,
        interval_minutes=5, channels=("RR",), bbox=(7.3, 49.4, 8.8, 50.8),
    )
    ds = fake_dataset("RR", 5)
    ds["RR"][-1, :, :] = np.nan
    with patch("adapters.mlcast_common.xr.open_zarr", return_value=ds):
        with pytest.raises(ConstraintError, match="no finite pixels"):
            adapter_for(req.source).load(req)


def test_nib_yaml_cli_keeps_original_dispatcher_and_optional_qa(tmp_path, capsys):
    """Same public run() path as `python -m adapters --config ...`."""
    import yaml
    from adapters.__main__ import run
    data_root = tmp_path / "demo_data"
    folder = data_root / "mlcast" / "it_dpc_sri_5min"
    folder.mkdir(parents=True)
    (folder / "sample.zarr").mkdir()
    config = {
        "source": "mlcast_it_dpc_sri_5min",
        "data_dir": "mlcast/it_dpc_sri_5min",
        "initial_time": "2023-07-01T11:55:00Z",
        "history_steps": 3,
        "interval_minutes": 5,
        "channels": ["RR"],
        "region": {"bbox": [7.75, 49.7, 8.25, 50.3]},
    }
    config_file = tmp_path / "request_custom.yaml"
    config_file.write_text(yaml.safe_dump(config))
    with patch("adapters.mlcast_common.xr.open_zarr", return_value=fake_dataset("RR", 5)):
        bundle = run(str(config_file), str(data_root))
    stdout = capsys.readouterr().out
    assert "loaded times    : 3" in stdout
    # Original common NiB dispatcher prints only the generic summary.
    assert "frame QA (UTC, valid %, min, max):" not in stdout
    assert "zarr output     : disabled" in stdout
    assert bundle["metadata"]["frame_shape"] == (1, 3)
    from scripts.mlcast_inspect import print_mlcast_qa
    print_mlcast_qa(bundle)
    report = capsys.readouterr().out
    assert "variable / units: RR / mm/h" in report
    assert "frame QA (UTC, valid %, min, max):" in report
    assert "2023-07-01T11:45:00Z" in report
