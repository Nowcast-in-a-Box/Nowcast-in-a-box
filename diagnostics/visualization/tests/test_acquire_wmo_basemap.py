from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "acquire_wmo_basemap.py"
SPEC = importlib.util.spec_from_file_location("acquire_wmo_basemap", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
acquire_wmo_basemap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquire_wmo_basemap)


def test_selects_first_lod_not_coarser_than_one_to_one_million() -> None:
    lods = [
        {"level": 7, "scale": 2_311_166.839488793},
        {"level": 8, "scale": 1_155_583.4197443966},
        {"level": 9, "scale": 577_791.7098721983},
        {"level": 10, "scale": 288_895.85493609915},
    ]

    assert acquire_wmo_basemap.select_cache_max_lod(lods, 1_000_000) == 9


def test_computes_global_wgs84_vector_tile_grid_at_lod_8() -> None:
    bounds = acquire_wmo_basemap.tile_grid_bounds(
        full_extent={
            "xmin": -180,
            "ymin": -89.9000015262132,
            "xmax": 180,
            "ymax": 84.96659482758929,
        },
        origin={"x": -180, "y": 90},
        resolution=0.00274658203125,
        tile_rows=512,
        tile_cols=512,
    )

    assert bounds == (3, 127, 0, 255)


def test_decodes_tilemap_block_in_row_major_order() -> None:
    payload = {
        "location": {"left": 10, "top": 20, "width": 2, "height": 2},
        "data": [1, 0, 0, 1],
    }

    assert acquire_wmo_basemap.available_tiles(payload, 8) == [
        (8, 20, 10),
        (8, 21, 11),
    ]


def test_empty_official_tilemap_block_returns_no_tiles() -> None:
    payload = {
        "error": {
            "code": 422,
            "message": "No tile available for the specified boundary.",
            "details": None,
        }
    }

    assert acquire_wmo_basemap.available_tiles(payload, 9) == []
