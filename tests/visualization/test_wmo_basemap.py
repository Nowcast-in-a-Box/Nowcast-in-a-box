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
    _decode_layer,
    _tile_bounds,
    crop_wmo_basemap,
    load_wmo_basemap,
    resolve_tile,
)

ROOT = Path(__file__).resolve().parents[2] / "diagnostics" / "visualization"
SOURCE = ROOT / "basemap_sources" / "wmo-source.json"


def test_buffered_multipart_mvt_is_clipped_to_the_geographic_tile(tmp_path):
    # Self-built MVT v2, extent 4096; two disjoint clockwise rectangles:
    # (4000,4000)-(4200,4200) and (3000,1000)-(4200,3000).
    # Both extend into the buffer; no third-party map data is embedded here.
    tile = tmp_path / "multipart.pbf"
    tile.write_bytes(
        bytes.fromhex(
            "1a350a08574d4f20424e444112241803222009c03ec03e1a900300009003"
            "8f03000f09cf0fff311ae0120000a01fdf12000f2880207802"
        )
    )
    frame = _decode_layer(tile, "WMO BNDA", (10, 20, 14, 24))
    land = frame.geometry.union_all()
    assert land.covers(Point(13.95, 20.05))
    assert land.covers(Point(13.5, 22))
    assert not land.covers(
        Point(14.05, 22)
    ), "tile buffer must not leak into its neighbour"
    assert land.geom_type == "MultiPolygon" and len(land.geoms) == 2
    assert land.bounds == pytest.approx((12.9296875, 20, 14, 23.0234375))
    assert frame.crs.to_epsg() == 4326


def test_real_multipart_land_survives_tile_edge_clipping():
    """The second polygon used to disappear in GDAL's built-in MVT clipping."""
    cache = os.environ.get("NIB_WMO_BASEMAP_PATH")
    if not cache:
        pytest.skip("set NIB_WMO_BASEMAP_PATH for downloaded WMO tile integration")
    basemap = load_wmo_basemap(cache, SOURCE)
    tile = Path(cache) / "VectorTileServer/tile/7/19/91.pbf"
    assert tile.is_file(), f"WMO regression tile missing: {tile}"
    bounds = _tile_bounds(basemap, 7, 19, 91)
    frame = _decode_layer(tile, "WMO BNDA", bounds)
    land = frame.geometry.union_all()
    assert land.covers(
        Point(78.425, 35.175)
    ), "valid inland terrain must not become ocean"
    west, south, east, north = frame.total_bounds
    assert bounds[0] <= west <= east <= bounds[2]
    assert bounds[1] <= south <= north <= bounds[3]
    assert frame.crs.to_epsg() == 4326


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
    monkeypatch.setattr(
        "nib_visualization.wmo_basemap.pyogrio.list_layers", lambda _tile: []
    )

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
                "--source-record",
                str(SOURCE),
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
                "--source-record",
                str(SOURCE),
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
