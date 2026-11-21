"""CMA runtime contract: complete windows, honest masks and a metric grid."""

import datetime as dt
import json

import numpy as np
import pytest
import xarray as xr

from adapters.cma_radar import source as cma
from adapters.cma_radar.nowcastnet.adapter import ADAPTER
from interfaces.errors import ConstraintError
from tests.field_fixtures import forecast as physical_forecast
from tests.field_fixtures import mock_onnx, observations
from utils.stored_fields import StoredFields, create_run, sha256


def test_index_latest_generation_and_nested_files(tmp_path):
    nested = tmp_path / "20260701" / "CREF"
    nested.mkdir(parents=True)
    for generation, date in [
        ("20260701000600", "20260701"),
        ("20260701001000", "20260701"),
        ("20260801000600", "20260801"),
    ]:
        (
            nested / f"Z_RADA_C_BABJ_{generation}_P_DOR_ACHN_CREF_{date}_000000.bin"
        ).touch()
    index = cma._window_file_index(tmp_path)
    assert len(index) == 2
    assert index[dt.datetime(2026, 7, 1)].generation_time.minute == 10


def test_samples_expose_days_and_filter_by_day(monkeypatch):
    starts = [
        dt.datetime(2026, 7, 31, 23, 30),
        dt.datetime(2026, 8, 2),
        dt.datetime(2026, 8, 3),
    ]
    index = {}
    for start in starts:
        for time in cma._window_times(start):
            index[time] = object()
    monkeypatch.setattr(cma, "_window_file_index", lambda _path: index)

    all_days = cma.ADAPTER.list_samples("data", limit=1)
    assert all_days["days"] == ["20260731", "20260802", "20260803"]
    assert cma.ADAPTER.check_window("data", "20260731", "2026-07-31T23:30:00Z")["ready"]
    assert all_days["total"] == 3

    second_day = cma.ADAPTER.list_samples("data", day="20260802", limit=10)
    assert second_day["day"] == "20260802"
    assert second_day["total"] == 1
    assert second_day["samples"][0]["event_id"] == "20260802"


def test_grid_is_128_square_at_three_km():
    grid = cma._regional_grid(118, 32)
    assert len(grid["x"]) == len(grid["y"]) == 128
    assert np.all(np.diff(grid["x"]) == 3000)
    assert grid["axis_units"] == "m"
    assert grid["x"][-1] - grid["x"][0] + 3000 == 384000
    with pytest.raises(ValueError):
        cma._regional_grid(200, 32)


def test_missing_window_is_not_padded():
    start = "2026-07-31T23:30:00Z"
    times = cma._window_times(cma._parse_utc(start))
    index = {t.replace(tzinfo=None): object() for t in times if t.month == 7}
    with pytest.raises(FileNotFoundError, match="missing frames"):
        cma._check_window_times(index, "20260731", start)


def test_no_echo_and_no_coverage_are_distinct(monkeypatch):
    class Identity:
        def transform(self, x, y):
            return x, y

    from pyproj import Transformer

    monkeypatch.setattr(Transformer, "from_crs", lambda *a, **kw: Identity())
    values = np.array([[0, -128], [-3276.8, -5]], dtype=np.float32)
    data = xr.DataArray(
        values,
        dims=("latitude", "longitude"),
        coords={"longitude": [0, 1], "latitude": [0, 1]},
    )
    out = cma._regrid(data, {"x": [0, 1], "y": [0, 1], "crs": "unused"})
    assert out[0, 1] == 0
    assert np.isnan(out[1, 0])
    assert out[1, 1] == 0


def test_mapping_roundtrip_and_real_cadence(monkeypatch, tmp_path):
    obs = observations(
        source="cma_radar", variable="CREF", units="dBZ", cadence=6, value=37.5
    )
    obs.values[0, 0, 0] = 100
    batch, physical = ADAPTER.prepare(obs)
    assert batch.shape == (1, 5, 128, 128)
    assert batch.dtype == np.float32
    assert batch[0, 0, 0, 0] == 1
    assert batch[0, 0, 0, 1] == 0.5
    assert obs.values[0, 0, 0] == 100
    output = np.full((1, 20, 128, 128), 0.5, np.float32)
    output[0, 0, 0, :2] = [-1, 2]
    weights, _, session = mock_onnx(monkeypatch, tmp_path, ADAPTER, output)
    result = ADAPTER.predict((batch, physical), weights_path=str(weights))
    assert session.run.call_args.args[1]["input_sequence"] is batch
    assert result.values.shape == (20, 128, 128)
    assert result.values.dtype == np.float32
    np.testing.assert_array_equal(result.values[0, 0, :3], [0, 75, 37.5])
    assert result.lead_minutes == tuple(range(6, 121, 6))
    assert result.init_time == obs.times[-1]
    assert result.times[0] == "2026-07-01T00:30:00Z"
    assert result.units == "dBZ" and result.variable_id == "CREF"


def test_nonfinite_model_output_is_not_hidden_by_clipping(monkeypatch, tmp_path):
    obs = observations(
        source="cma_radar", variable="CREF", units="dBZ", cadence=6, value=0
    )
    output = np.full((1, 20, 128, 128), np.inf, np.float32)
    weights, _, _ = mock_onnx(monkeypatch, tmp_path, ADAPTER, output)
    with pytest.raises(ConstraintError, match="missing forecast"):
        ADAPTER.predict(ADAPTER.prepare(obs), weights_path=str(weights))


def test_stored_cma_roundtrip(tmp_path):
    times = [cma._iso_utc(t) for t in cma._window_times(dt.datetime(2026, 7, 1))]
    window = {
        "data_id": "cma_radar",
        "variable": "CREF",
        "unit": "dBZ",
        "cadence_minutes": 6,
        "context": np.zeros((6, 128, 128), np.float32),
        "target": np.zeros((20, 128, 128), np.float32),
        "context_times": times[:6],
        "target_times": times[6:],
        "sample_id": "demo",
        "event_id": "20260701",
        "row": 0,
        "context_start_time": times[0],
        "init_time": times[5],
    }
    window["provenance"] = {"notice": "Cross-domain demo; skill unvalidated"}
    directory = create_run(
        tmp_path, "cma", window, cma._regional_grid(118, 32), atlas_revision="dev"
    )
    source = StoredFields(directory)
    assert source.capabilities()["cadence_minutes"] == 6
    assert (
        source.get_observation(
            "cma_radar", dt.datetime.fromisoformat(times[6].replace("Z", "+00:00"))
        )["unit"]
        == "dBZ"
    )
    from utils.stored_fields import add_forecast

    forecast = np.arange(20 * 128 * 128, dtype=np.float32).reshape(20, 128, 128)
    forecast[0, 0, 0] = np.nan
    add_forecast(
        directory,
        "nowcastnet",
        "test",
        "a" * 64,
        physical_forecast(
            forecast,
            grid=cma._regional_grid(118, 32),
            init_time=window["init_time"],
            leads=tuple(range(6, 121, 6)),
            variable="CREF",
            units="dBZ",
        ),
    )
    pytest.importorskip("rasterio")
    from diagnostics.map_visualization import visualization

    basemap = tmp_path / "basemap"
    viewer = basemap / "viewer"
    (viewer / "wmo" / "0" / "0").mkdir(parents=True)
    (viewer / "terrain-land.tif").write_bytes(b"terrain")
    (viewer / "wmo" / "0" / "0" / "0.pbf").write_bytes(b"pbf")
    (viewer / "basemap.json").write_text(
        json.dumps(
            {
                "map_extent": [-180, -90, 180, 90],
                "terrain": {
                    "url": "terrain-land.tif",
                    "sha256": sha256(viewer / "terrain-land.tif"),
                    "colors": [[0, "#dce5d2"]],
                    "attribution": "ETOPO",
                    "disclaimer": "not for navigation",
                },
                "boundaries": {
                    "url": "wmo",
                    "tiles": {
                        "keys": ["0/0/0"],
                        "origin": [-180, 90],
                        "tile_size": 512,
                        "resolutions": [0.7],
                        "symbol_types": {"0": "coastline"},
                    },
                    "styles": {
                        "coastline": {"edgecolor": "#A1B6D3", "linewidth": 0.35}
                    },
                    "attribution": "WMO",
                    "disclaimer": "no opinion",
                },
            }
        )
    )
    stored = directory / "forecasts" / "nowcastnet" / "fields.npz"
    before = stored.read_bytes()
    attempt = visualization(directory, "nowcastnet", basemap)
    report = directory / attempt["entry"]
    bundle = json.loads(report.read_text())
    assert attempt["entry"].endswith("viewer.json")
    assert bundle["mock"] is False and bundle["is_real_forecast"] is True
    assert "skill unvalidated" in bundle["notice"]
    assert "observation replay" not in bundle["notice"].lower()
    assert len(bundle["frames"]) == 20
    assert bundle["weather_style"]["min"] == 10
    assert bundle["spatial_sampling"] == "nearest"
    assert (report.parent / "weather" / "frame-0000.tif").is_file()
    assert not (report.parent / "comparison.png").exists()
    assert not (report.parent / "animation.gif").exists()
    assert stored.read_bytes() == before
