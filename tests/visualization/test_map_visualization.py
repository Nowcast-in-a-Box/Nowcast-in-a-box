"""Stored forecasts become a viewer bundle without rewriting scientific files."""

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import CRS

from diagnostics.map_visualization import visualization
from utils.stored_fields import StoredFields, add_forecast, create_run, sha256

pytest.importorskip("rasterio")


def test_geographic_bundle_preserves_values_times_and_hash(tmp_path):
    directory, forecast = _run(tmp_path, projected=False)
    before = forecast.read_bytes()
    digest = sha256(forecast)
    basemap = _basemap(tmp_path / "cache")
    attempt = visualization(directory, "nowcastnet", basemap, attempt_id="ab" * 16)
    assert attempt["status"] == "succeeded"
    assert attempt["entry"].endswith("viewer.json")
    assert attempt["sample_id"] == "demo"
    bundle = json.loads((directory / attempt["entry"]).read_text())
    assert bundle["viewer_bundle_version"] == 1
    assert bundle["mock"] is False and bundle["is_real_forecast"] is True
    assert bundle["title"] == "NowcastNet · CREF"
    assert bundle["notice"] == "Cross-domain demo; skill unvalidated"
    assert "observation replay" not in json.dumps(bundle).lower()
    assert bundle["variable"] == "CREF" and bundle["units"] == "dBZ"
    assert bundle["native_shape"] == [3, 4]
    assert bundle["spatial_sampling"] == "native"
    assert bundle["weather_style"]["min"] == 10
    assert bundle["weather_style"]["max"] == 75
    assert bundle["weather_style"]["step"] == 0.5
    assert bundle["terrain"]["url"] == "/map-basemap/terrain-land.tif"
    assert bundle["boundaries"]["url"] == "/map-basemap/wmo"
    assert [frame["lead_seconds"] for frame in bundle["frames"]] == [360.0, 720.0]
    assert bundle["frames"][0]["valid_time"] == "2026-07-01T00:06:00Z"
    assert bundle["init_time"] == "2026-07-01T00:00:00Z"
    expected = np.arange(2 * 3 * 4, dtype="f4").reshape(2, 3, 4)
    expected[0, 0, 1] = np.nan
    for index, frame in enumerate(bundle["frames"]):
        with rasterio.open(directory / attempt["path"] / frame["url"]) as ds:
            assert ds.crs.to_epsg() == 4326
            assert np.isnan(ds.nodata)
            np.testing.assert_array_equal(ds.read(1), expected[index][::-1])
    assert forecast.read_bytes() == before
    assert sha256(forecast) == digest
    recorded = StoredFields(directory).manifest["diagnostics"][-1]
    assert recorded["id"] == attempt["id"] and recorded["status"] == "succeeded"


def test_projected_field_uses_display_grid_sampling(tmp_path):
    directory, _forecast = _run(tmp_path, projected=True)
    attempt = visualization(directory, "nowcastnet", _basemap(tmp_path / "cache"))
    bundle = json.loads((directory / attempt["entry"]).read_text())
    assert bundle["crs"] == "EPSG:4326"
    assert bundle["spatial_sampling"] == "nearest"
    assert bundle["native_shape"] == [4, 4]
    assert bundle["mock"] is False


def test_raw_field_uses_turbo_scale(tmp_path):
    directory, _forecast = _run(tmp_path, projected=False, unit="raw", variable="vil")
    attempt = visualization(directory, "nowcastnet", _basemap(tmp_path / "cache"))
    style = json.loads((directory / attempt["entry"]).read_text())["weather_style"]
    assert style["min"] == 0 and style["max"] == 255 and style["step"] == 1
    assert len(style["colors"]) == 256 and len(style["over"]) == 3


def test_conflict_external_url_and_failed_export_do_not_succeed(tmp_path, monkeypatch):
    directory, _forecast = _run(tmp_path, projected=False)
    basemap = _basemap(tmp_path / "cache")
    attempt_id = "cd" * 16
    first = visualization(directory, "nowcastnet", basemap, attempt_id=attempt_id)
    original = (directory / first["entry"]).read_bytes()
    with pytest.raises(FileExistsError, match="already exists"):
        visualization(directory, "nowcastnet", basemap, attempt_id=attempt_id)
    assert (directory / first["entry"]).read_bytes() == original
    assert [
        item["id"]
        for item in StoredFields(directory).manifest["diagnostics"]
        if item.get("kind") == "visualization"
    ] == [attempt_id]

    external = _basemap(tmp_path / "external")
    record = json.loads((external / "viewer" / "basemap.json").read_text())
    record["terrain"]["url"] = "https://example.com/terrain.tif"
    (external / "viewer" / "basemap.json").write_text(json.dumps(record))
    with pytest.raises(ValueError):
        visualization(directory, "nowcastnet", external, attempt_id="ef" * 16)
    assert not (directory / "diagnostics" / "visualization" / ("ef" * 16)).exists()

    def fail(*_args, **_kwargs):
        raise RuntimeError("cog failed")

    monkeypatch.setattr("diagnostics.map_visualization.export_field_cogs", fail)
    with pytest.raises(RuntimeError, match="cog failed"):
        visualization(directory, "nowcastnet", basemap, attempt_id="12" * 16)
    assert not any(
        item.get("id") == "12" * 16
        for item in StoredFields(directory).manifest["diagnostics"]
    )


def test_missing_basemap_and_bad_hash_do_not_record_success(tmp_path):
    directory, forecast = _run(tmp_path, projected=False)
    with pytest.raises(FileNotFoundError):
        visualization(directory, "nowcastnet", tmp_path / "missing")
    assert StoredFields(directory).manifest["diagnostics"] == []

    payload = bytearray(forecast.read_bytes())
    payload[-1] ^= 0xFF
    forecast.write_bytes(payload)
    with pytest.raises(ValueError, match="checksum"):
        visualization(directory, "nowcastnet", _basemap(tmp_path / "cache"))
    assert not list((directory / "diagnostics").glob("visualization/*"))


@pytest.mark.parametrize("corruption", ["bytes", "symlink"])
def test_prepared_terrain_corruption_does_not_record_success(tmp_path, corruption):
    directory, _forecast = _run(tmp_path, projected=False)
    basemap = _basemap(tmp_path / "cache")
    terrain = basemap / "viewer" / "terrain-land.tif"
    if corruption == "bytes":
        terrain.write_bytes(b"corrupt terrain")
    else:
        outside = basemap / "outside-viewer.tif"
        outside.write_bytes(terrain.read_bytes())
        terrain.unlink()
        terrain.symlink_to(outside)
    with pytest.raises(ValueError):
        visualization(directory, "nowcastnet", basemap)
    assert not StoredFields(directory).manifest["diagnostics"]
    assert not (directory / "diagnostics" / "visualization").exists()


def test_invalid_attempt_id():
    with pytest.raises(ValueError, match="invalid diagnostic attempt ID"):
        visualization("unused", "nowcastnet", "unused", attempt_id="ZZ" * 16)


def _run(tmp_path, *, projected, unit="dBZ", variable="CREF"):
    cadence = 6 if unit == "dBZ" else 5
    if projected:
        axis = np.array([-4500.0, -1500.0, 1500.0, 4500.0])
        grid = {
            "crs": CRS.from_proj4(
                "+proj=laea +lat_0=32 +lon_0=118 +datum=WGS84 +units=m"
            ).to_wkt(),
            "x": axis,
            "y": axis.copy(),
        }
        shape = (4, 4)
    else:
        grid = {
            "crs": "EPSG:4326",
            "x": np.array([100.0, 101.0, 102.0, 103.0]),
            "y": np.array([30.0, 31.0, 32.0]),
        }
        shape = (3, 4)
    times = [
        "2026-07-01T00:00:00Z",
        f"2026-07-01T00:{cadence:02d}:00Z",
        f"2026-07-01T00:{2 * cadence:02d}:00Z",
    ]
    values = np.arange(2 * shape[0] * shape[1], dtype=np.float32).reshape(2, *shape)
    values[0, 0, 1] = np.nan
    window = {
        "data_id": "cma_radar" if unit == "dBZ" else "sevir",
        "variable": variable,
        "unit": unit,
        "cadence_minutes": cadence,
        "context": np.zeros((1, *shape), np.float32),
        "target": values,
        "context_times": times[:1],
        "target_times": times[1:],
        "sample_id": "demo",
        "event_id": "20260701",
        "row": 0,
        "context_start_time": times[0],
        "init_time": times[0],
        "provenance": {"notice": "Cross-domain demo; skill unvalidated"},
    }
    directory = create_run(tmp_path, "run", window, grid, atlas_revision="dev")
    StoredFields(directory).manifest  # load before names update
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["model_names"] = {"nowcastnet": "NowcastNet"}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    add_forecast(
        directory, "nowcastnet", "test", "a" * 64, values, [cadence, 2 * cadence]
    )
    return directory, directory / "forecasts" / "nowcastnet" / "fields.npz"


def _basemap(root: Path) -> Path:
    viewer = root / "viewer"
    (viewer / "wmo" / "0" / "0").mkdir(parents=True)
    (viewer / "terrain-land.tif").write_bytes(b"terrain")
    (viewer / "wmo" / "0" / "0" / "0.pbf").write_bytes(b"pbf")
    record = {
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
                "origin": [-180, 90],
                "tile_size": 512,
                "resolutions": [0.7],
                "keys": ["0/0/0"],
                "symbol_types": {"0": "coastline"},
            },
            "styles": {"coastline": {"edgecolor": "#A1B6D3", "linewidth": 0.35}},
            "attribution": "WMO",
            "disclaimer": "no opinion",
        },
    }
    (viewer / "basemap.json").write_text(json.dumps(record))
    return root
