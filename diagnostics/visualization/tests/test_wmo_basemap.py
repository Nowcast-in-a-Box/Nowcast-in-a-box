"""Offline WMO vector-tile adapter checks."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
from PIL import Image
from shapely.geometry import Point

from nib_visualization.basemap import crop_basemap
from nib_visualization.cli import main
from nib_visualization.wmo_basemap import (
    WmoBasemap,
    crop_wmo_basemap,
    load_wmo_basemap,
    resolve_tile,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "basemap_sources" / "wmo-source.json"


def test_parent_tile_fallback_uses_original_physical_tile(tmp_path):
    tile = tmp_path / "VectorTileServer" / "tile" / "7" / "13" / "67.pbf"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b"physical-tile")
    assert resolve_tile(tmp_path, 9, 55, 270) == (tile, 7, 13, 67)


def test_missing_ancestor_is_an_explicit_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="9/55/270"):
        resolve_tile(tmp_path, 9, 55, 270)


def test_missing_tile_inside_source_coverage_fails_crop(tmp_path):
    basemap = WmoBasemap(
        path=tmp_path,
        source_id="wmo-agreed-basemap-wgs84",
        attribution="WMO",
        disclaimer="test",
        origin_x=-180,
        origin_y=90,
        tile_pixels=512,
        resolutions={0: 0.703125},
        max_lod=0,
    )
    with pytest.raises(FileNotFoundError, match="0/0/0"):
        crop_wmo_basemap(basemap, (5, 15, 47, 55))


@pytest.mark.parametrize(
    ("extent", "expected_tiles"),
    [
        ((-180, 180, -90, 90), {(1, 0, 0), (1, 0, 1)}),
        ((0, 180, 0, 90), {(2, 0, 2), (2, 0, 3)}),
    ],
)
def test_exact_tile_boundaries_do_not_request_tiles_outside_viewport(
    tmp_path, monkeypatch, extent, expected_tiles
):
    basemap = WmoBasemap(
        path=tmp_path,
        source_id="wmo-agreed-basemap-wgs84",
        attribution="WMO",
        disclaimer="test",
        origin_x=-180,
        origin_y=90,
        tile_pixels=512,
        resolutions={0: 0.703125, 1: 0.3515625, 2: 0.17578125},
        max_lod=2,
    )
    requested = set()

    def tile_for_viewport(_root, lod, row, col):
        tile = (lod, row, col)
        requested.add(tile)
        if tile not in expected_tiles:
            raise FileNotFoundError(f"unexpected tile outside viewport: {tile}")
        return tmp_path / f"{lod}-{row}-{col}.pbf", lod, row, col

    monkeypatch.setattr("nib_visualization.wmo_basemap.resolve_tile", tile_for_viewport)
    monkeypatch.setattr("nib_visualization.wmo_basemap.pyogrio.list_layers", lambda _tile: [])

    view = crop_wmo_basemap(basemap, extent)
    assert requested == expected_tiles
    assert view.extent_wgs84 == extent


def test_real_cache_is_georeferenced_and_offline(monkeypatch):
    cache = os.environ.get("NIB_WMO_BASEMAP_PATH")
    if not cache:
        pytest.skip("set NIB_WMO_BASEMAP_PATH for downloaded WMO tile integration")
    root = Path(cache)
    assert root.is_dir(), f"WMO cache missing: {root}"
    metadata = json.loads((root / "VectorTileServer" / "metadata.json").read_text())
    assert metadata["tileInfo"]["spatialReference"]["wkid"] == 4326

    def no_network(*_args, **_kwargs):
        raise AssertionError("WMO crop must not use network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    basemap = load_wmo_basemap(root, SOURCE)
    assert resolve_tile(root, 9, 55, 270)[1:] == (7, 13, 67)
    view = crop_basemap(basemap, (5, 15, 47, 55))
    assert view.source_id == "wmo-agreed-basemap-wgs84"
    assert view.extent_wgs84 == (5, 15, 47, 55)
    assert len(view.layers["country_land"]) > 0
    assert len(view.layers["boundary_lines"]) > 0
    assert "ocean" in view.layers
    assert not view.layers["ocean"].geometry.union_all().covers(Point(10, 47.5))
    for layer in view.layers.values():
        assert layer.crs.to_epsg() == 4326
        if not layer.empty:
            west, south, east, north = layer.total_bounds
            assert 5 <= west <= east <= 15
            assert 47 <= south <= north <= 55


def test_wmo_cli_renders_pure_and_field_maps_offline(tmp_path, monkeypatch):
    cache = os.environ.get("NIB_WMO_BASEMAP_PATH")
    if not cache:
        pytest.skip("set NIB_WMO_BASEMAP_PATH for downloaded WMO tile integration")

    def no_network(*_args, **_kwargs):
        raise AssertionError("WMO rendering must not use network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    pure = tmp_path / "pure"
    assert (
        main(
            [
                "basemap",
                "--provider",
                "wmo",
                "--basemap",
                cache,
                "--bbox",
                "5",
                "15",
                "47",
                "55",
                "--output",
                str(pure),
            ]
        )
        == 0
    )
    with Image.open(pure / "basemap.png") as image:
        assert image.size == (1000, 700)
    field = tmp_path / "field.png"
    assert (
        main(
            [
                "map",
                "--provider",
                "wmo",
                "--basemap",
                cache,
                "--grid",
                "latlon",
                "--output",
                str(field),
            ]
        )
        == 0
    )
    with Image.open(field) as image:
        assert image.size == (1000, 700)
