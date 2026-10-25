"""Shared basemap publication stays inside the cache and fails closed."""

import hashlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import netCDF4
import numpy as np
import pytest
from pyproj import CRS

from nib_visualization.viewer_basemap import cache_relative, prepare_basemap_bundle

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "diagnostics" / "visualization" / "scripts" / "prepare_map_viewer.py"


def test_cache_relative_rejects_external_and_escaping_paths():
    assert cache_relative("wmo/0/0/0.pbf") == "wmo/0/0/0.pbf"
    for bad in (
        "",
        "/etc/passwd",
        "https://example.com/t.tif",
        "../x",
        "wmo/../x.pbf",
        "C:x",
    ):
        with pytest.raises(ValueError, match="inside the cache"):
            cache_relative(bad)


def test_prepare_publishes_relative_assets_and_reuses_cache(tmp_path, monkeypatch):
    wmo, terrain, record, digest = _sources(tmp_path)
    output = tmp_path / "viewer"
    monkeypatch.setattr(
        "nib_visualization.viewer_basemap.land_only_terrain", _global_land
    )
    bundle = prepare_basemap_bundle(
        wmo, terrain, record, digest, output, terrain_width=4
    )
    assert bundle["map_extent"] == [-180.0, -90.0, 180.0, 90.0]
    assert bundle["terrain"]["url"] == "terrain-land.tif"
    assert bundle["boundaries"]["url"] == "wmo"
    assert "0/0/0" in bundle["boundaries"]["tiles"]["keys"]
    assert bundle["cache"]["key"]["version"] == 4
    assert bundle["cache"]["key"]["source_sha256"] == digest
    assert bundle["cache"]["key"]["max_width"] == 4
    assert bundle["cache"]["key"]["land_mask"]
    assert (output / "wmo" / "0" / "0" / "0.pbf").read_bytes() == b"parent-tile"
    text = (output / "basemap.json").read_text()
    assert str(terrain.resolve()) not in text
    assert str(wmo.resolve()) not in text

    def fail(*_args, **_kwargs):
        raise AssertionError("cache hit must not rebuild terrain")

    monkeypatch.setattr("nib_visualization.viewer_basemap.land_only_terrain", fail)
    again = prepare_basemap_bundle(
        wmo, terrain, record, digest, output, terrain_width=4
    )
    assert again["terrain"]["sha256"] == bundle["terrain"]["sha256"]


def test_corrupt_or_escaping_inputs_do_not_publish(tmp_path, monkeypatch):
    wmo, terrain, record, digest = _sources(tmp_path)
    output = tmp_path / "viewer"
    (wmo / "SHA256SUMS.txt").write_text(
        "0" * 64 + "  VectorTileServer/tile/0/0/0.pbf\n"
    )
    with pytest.raises(ValueError, match="prepare the offline global terrain"):
        prepare_basemap_bundle(wmo, terrain, record, digest, output, terrain_width=4)
    assert not (output / "basemap.json").exists()

    _sources(tmp_path)
    monkeypatch.setattr(
        "nib_visualization.viewer_basemap.land_only_terrain", _global_land
    )
    monkeypatch.setattr(
        "nib_visualization.viewer_basemap.export_wmo_tiles",
        lambda *_args, **_kwargs: {
            "origin": [-180, 90],
            "tile_size": 512,
            "resolutions": [1],
            "keys": ["../x"],
            "symbol_types": {},
        },
    )
    with pytest.raises(ValueError, match="inside the cache"):
        prepare_basemap_bundle(wmo, terrain, record, digest, output, terrain_width=4)
    assert not (output / "basemap.json").exists()


def test_cli_requires_explicit_inputs_and_publishes(tmp_path, monkeypatch):
    module = _script()
    with pytest.raises(SystemExit):
        module.main([])
    wmo, terrain, record, digest = _sources(tmp_path)
    output = tmp_path / "viewer"
    monkeypatch.setattr(
        "nib_visualization.viewer_basemap.land_only_terrain", _global_land
    )
    assert (
        module.main(
            [
                "--wmo-cache",
                str(wmo),
                "--terrain",
                str(terrain),
                "--terrain-source-record",
                str(record),
                "--terrain-sha256",
                digest,
                "--basemap-output",
                str(output),
                "--terrain-width",
                "4",
            ]
        )
        == 0
    )
    assert (output / "basemap.json").is_file()


def _global_land(view, _basemap, max_lod=7):
    assert max_lod == 7
    values = np.zeros(view.values.shape, np.float32)
    values[0, 0] = np.nan
    return replace(view, values=values)


def _sources(tmp_path):
    wmo = tmp_path / "wmo"
    tile = wmo / "VectorTileServer" / "tile" / "0" / "0" / "0.pbf"
    tile.parent.mkdir(parents=True, exist_ok=True)
    tile.write_bytes(b"parent-tile")
    (wmo / "VectorTileServer" / "metadata.json").write_text(
        json.dumps(
            {
                "tileInfo": {
                    "spatialReference": {"wkid": 4326},
                    "format": "pbf",
                    "rows": 512,
                    "cols": 512,
                    "origin": {"x": -180, "y": 90},
                    "lods": [{"level": 0, "resolution": 0.703125}],
                },
                "fullExtent": {"xmin": -180, "ymin": -90, "xmax": 180, "ymax": 90},
            }
        )
    )
    (wmo / "acquisition.json").write_text(json.dumps({"cache_max_lod": 0}))
    digest = hashlib.sha256(tile.read_bytes()).hexdigest()
    (wmo / "SHA256SUMS.txt").write_text(f"{digest}  VectorTileServer/tile/0/0/0.pbf\n")
    terrain = tmp_path / "terrain.nc"
    lat, lon = np.array([-45.0, 45.0]), np.array([-135.0, -45.0, 45.0, 135.0])
    with netCDF4.Dataset(terrain, "w") as ds:
        for name, centres, units in (
            ("lat", lat, "degrees_north"),
            ("lon", lon, "degrees_east"),
        ):
            ds.createDimension(name, len(centres))
            variable = ds.createVariable(name, "f8", (name,))
            variable.units = units
            variable[:] = centres
        crs = ds.createVariable("crs", "S1")
        crs.spatial_ref = CRS.from_epsg(4326).to_wkt()
        z = ds.createVariable("z", "f4", ("lat", "lon"))
        z.grid_mapping, z.units = "crs", "meters"
        z[:] = np.arange(8, dtype="f4").reshape(2, 4)
    record = tmp_path / "terrain-source.json"
    record.write_text(
        json.dumps(
            {
                "source_id": "test-etopo",
                "horizontal_crs": "EPSG:4326",
                "vertical_crs": "EPSG:3855",
                "citation": "Test terrain attribution",
                "disclaimer": "Test terrain disclaimer",
            }
        )
    )
    file_digest = hashlib.sha256(terrain.read_bytes()).hexdigest()
    return wmo, terrain, record, file_digest


def _script():
    spec = importlib.util.spec_from_file_location("prepare_map_viewer", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
