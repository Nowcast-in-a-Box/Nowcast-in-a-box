"""YAML selection stays separate from download; no network required."""
import numpy as np
import yaml
import xarray as xr
from unittest.mock import patch

from scripts.mlcast_make_request import make_request


def test_generates_editable_request_for_subset_of_existing_sample(tmp_path):
    sample_folder = tmp_path / "mlcast" / "it_dpc_sri_5min"
    sample_folder.mkdir(parents=True)
    (sample_folder / "sample.zarr").mkdir()
    times = np.datetime64("2023-07-01T12:00:00") - np.arange(4, -1, -1) * np.timedelta64(5, "m")
    ds = xr.Dataset(
        {"RR": (("time", "y", "x"), np.ones((5, 8, 10)), {"units": "mm/h"})},
        coords={"time": times, "y": np.arange(8), "x": np.arange(10),
                "lat": ("y", np.linspace(38, 45, 8)),
                "lon": ("x", np.linspace(8, 17, 10))},
    )
    with patch("scripts.mlcast_make_request.xr.open_zarr", return_value=ds):
        config_path = make_request(tmp_path, "it_dpc_sri_5min", 3, 1, 0.6)
    req = yaml.safe_load(config_path.read_text())
    assert req["source"] == "mlcast_it_dpc_sri_5min"
    assert req["initial_time"] == "2023-07-01T11:55:00Z"
    assert req["history_steps"] == 3
    assert req["interval_minutes"] == 5
    assert req["channels"] == ["RR"]
    assert req["data_dir"] == "mlcast/it_dpc_sri_5min"
    assert req["region"]["bbox"][0] > 8
    assert req["region"]["bbox"][2] < 17


def test_cli_can_sync_standard_nib_example_without_changing_local_sample(tmp_path, monkeypatch):
    """CLI should place config alongside the other source request YAML examples."""
    import sys
    from pathlib import Path
    from scripts import mlcast_make_request
    dataset = "it_dpc_sri_5min"
    sample_folder = tmp_path / "mlcast" / dataset
    sample_folder.mkdir(parents=True)
    (sample_folder / "sample.zarr").mkdir()
    timevals = np.datetime64("2023-07-01T12:00:00") - np.arange(4, -1, -1) * np.timedelta64(5, "m")
    ds = xr.Dataset({"RR": (("time", "y", "x"), np.ones((5, 8, 10)))},
                    coords={"time": timevals, "y": np.arange(8), "x": np.arange(10),
                            "lat": ("y", np.linspace(38, 45, 8)),
                            "lon": ("x", np.linspace(8, 17, 10))})
    # Do not actually alter repository fixtures in unit tests.
    repo_root = tmp_path / "fake_repo"
    (repo_root / "adapters").mkdir(parents=True)
    real_root = Path(mlcast_make_request.__file__).resolve().parents[1]
    (repo_root / "adapters" / "mlcast_sources.json").write_text(
        (real_root / "adapters" / "mlcast_sources.json").read_text())
    class FakeFile:
        def __init__(self, p):
            self.p = p
        def resolve(self):
            return repo_root / "scripts" / "mlcast_make_request.py"
    monkeypatch.setattr(mlcast_make_request, "__file__", str(repo_root / "scripts" / "mlcast_make_request.py"))
    with patch("scripts.mlcast_make_request.xr.open_zarr", return_value=ds):
        config_path = mlcast_make_request.make_request(tmp_path, dataset, 3, 1, 0.6, sync_example=True)
    example = repo_root / "tests/adapters/requests/mlcast_it_dpc_sri_5min/example_success.yaml"
    assert example.exists()
    assert yaml.safe_load(example.read_text()) == yaml.safe_load(config_path.read_text())
