"""Land masking preserves geographic identity, values and sparse-tile semantics."""

import numpy as np
from shapely.geometry import box

from nib_visualization.basemap import RasterBasemapView
from nib_visualization.terrain import ETOPO_COLORS
from nib_visualization.viewer_basemap import land_only_terrain, paint_land_tile


def test_fine_ocean_replaces_parent_land_without_touching_other_cells():
    mask = np.zeros((4, 4), dtype="uint8")
    extent = (0, 4, 0, 4)
    paint_land_tile(mask, extent, (0, 0, 4, 4), [box(0, 0, 4, 4)])
    paint_land_tile(mask, extent, (2, 2, 4, 4), [])
    np.testing.assert_array_equal(mask, [[1, 1, 0, 0], [1, 1, 0, 0],
                                       [1, 1, 1, 1], [1, 1, 1, 1]])


def test_mask_keeps_negative_land_and_correct_north_south_orientation(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import nib_visualization.viewer_basemap as module

    values = np.array([[-250, 100], [-400, -1000]], dtype="f4")
    view = RasterBasemapView(values, (0, 2, 0, 2), ETOPO_COLORS, .56)
    monkeypatch.setattr(module, "physical_tiles", lambda *_: [(0, 0, 0, "tile")])
    monkeypatch.setattr(module, "_tile_bounds", lambda *_: (0, 0, 2, 2))
    monkeypatch.setattr(module.pyogrio, "list_layers", lambda _: [("WMO BNDA", "Polygon")])
    monkeypatch.setattr(module, "_decode_layer",
                        lambda *_: SimpleNamespace(geometry=[box(0, 0, 1, 2), box(1, 0, 2, 1)]))
    (tmp_path / "VectorTileServer").mkdir()
    (tmp_path / "VectorTileServer/metadata.json").write_text(
        '{"fullExtent":{"xmin":0,"xmax":2,"ymin":0,"ymax":2}}')
    result = land_only_terrain(view, SimpleNamespace(max_lod=7, path=tmp_path))
    np.testing.assert_array_equal(result.values, [[-250, 100], [-400, np.nan]])
    np.testing.assert_array_equal(view.values, values)
    assert result.color_stops[0][1] == result.color_stops[1][1] == "#dce5d2"
    assert result.extent_wgs84 == view.extent_wgs84


def test_detailed_service_extent_does_not_erase_south_pole(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import nib_visualization.viewer_basemap as module

    view = RasterBasemapView(np.array([[100, 100], [200, 200]], dtype="f4"),
                             (0, 2, -90, -89.8), ETOPO_COLORS, .56)
    monkeypatch.setattr(module, "physical_tiles",
                        lambda *_: [(0, 0, 0, "root"), (1, 0, 0, "fine")])
    monkeypatch.setattr(module, "_tile_bounds", lambda *_: (0, -90, 2, -89.8))
    monkeypatch.setattr(module.pyogrio, "list_layers",
                        lambda path: [("WMO BNDA", "Polygon")] if path == "root" else [])
    monkeypatch.setattr(module, "_decode_layer",
                        lambda *_: SimpleNamespace(geometry=[box(0, -90, 2, -89.8)]))
    (tmp_path / "VectorTileServer").mkdir()
    (tmp_path / "VectorTileServer/metadata.json").write_text(
        '{"fullExtent":{"xmin":0,"xmax":2,"ymin":-89.9,"ymax":90}}')
    result = land_only_terrain(view, SimpleNamespace(max_lod=7, path=tmp_path))
    np.testing.assert_array_equal(result.values, [[100, 100], [np.nan, np.nan]])
