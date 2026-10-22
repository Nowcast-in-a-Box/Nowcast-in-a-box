"""Prepare local viewer basemaps; keep Core and original source files unchanged."""

import gzip
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pyogrio
from rasterio.features import rasterize
from rasterio.transform import from_origin

from nib_visualization.wmo_basemap import _SYMBOLS, _decode_layer, _tile_bounds

# User-selected soft-natural palette for the land-only interactive viewer.
LAND_TERRAIN_COLORS = (
    (-10000, "#dce5d2"), (0, "#dce5d2"), (500, "#e7e5cd"),
    (1500, "#e3d3b6"), (3000, "#c9b293"), (5000, "#a59a89"),
    (6500, "#d2cdc3"), (8500, "#faf8f2"),
)


def physical_tiles(basemap, max_lod):
    root = basemap.path / "VectorTileServer" / "tile"
    tiles = []
    for path in root.glob("*/*/*.pbf"):
        z, row, col = map(int, path.relative_to(root).with_suffix("").parts)
        if z <= max_lod:
            tiles.append((z, row, col, path))
    return sorted(tiles)


def paint_land_tile(mask, extent, bounds, geometries):
    """Replace only cells whose centres belong to this tile, including ocean.

    mask is north-up. Coarse-to-fine replacement reproduces sparse parent
    fallback without promoting land in an ancestor into a finer ocean tile.
    """
    west, east, south, north = extent
    dx, dy = (east - west) / mask.shape[1], (north - south) / mask.shape[0]
    left, bottom, right, top = bounds
    c0 = max(0, math.ceil((left - west) / dx - .5))
    c1 = min(mask.shape[1], math.ceil((right - west) / dx - .5))
    r0 = max(0, math.ceil((north - top) / dy - .5))
    r1 = min(mask.shape[0], math.ceil((north - bottom) / dy - .5))
    if c0 >= c1 or r0 >= r1:
        return
    shapes = [(g, 1) for g in geometries if not g.is_empty]
    mask[r0:r1, c0:c1] = rasterize(
        shapes, out_shape=(r1 - r0, c1 - c0), dtype="uint8",
        transform=from_origin(west + c0 * dx, north - r0 * dy, dx, dy),
    ) if shapes else 0


def land_only_terrain(view, basemap, max_lod=7):
    """Mask by WMO land geometry, never by the sign of elevation."""
    mask = np.zeros(view.values.shape, dtype="uint8")
    coverage = json.loads(
        (basemap.path / "VectorTileServer" / "metadata.json").read_text()
    )["fullExtent"]
    for z, row, col, path in physical_tiles(basemap, min(max_lod, basemap.max_lod)):
        bounds = _tile_bounds(basemap, z, row, col)
        names = {name for name, _ in pyogrio.list_layers(path)}
        geometries = (_decode_layer(path, "WMO BNDA", bounds).geometry
                      if "WMO BNDA" in names else [])
        if z > 0:
            # Retain the global parent's polar caps outside the detailed
            # service coverage; an artificial -89.9 cut is not a coastline.
            bounds = (max(bounds[0], coverage["xmin"]), max(bounds[1], coverage["ymin"]),
                      min(bounds[2], coverage["xmax"]), min(bounds[3], coverage["ymax"]))
        paint_land_tile(mask, view.extent_wgs84, bounds, geometries)
    # RasterBasemapView is south-up; the rasterization buffer above is north-up.
    values = np.where(mask[::-1], view.values, np.nan).astype("f4")
    # Negative land elevations use the lowland palette, not bathymetric blue.
    return replace(view, values=values, color_stops=LAND_TERRAIN_COLORS)


def export_wmo_tiles(basemap, output):
    """Copy actual cached PBFs to static files with an explicit geographic grid.

    Some mirrors contain gzip bytes without an HTTP Content-Encoding header.
    Write plain PBF so any existing static file server can serve them correctly.
    """
    output = Path(output)
    keys = []
    for z, row, col, path in physical_tiles(basemap, basemap.max_lod):
        key = f"{z}/{row}/{col}"
        target = output / f"{key}.pbf"
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        target.write_bytes(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)
        keys.append(key)
    if "0/0/0" not in keys:
        raise ValueError("WMO global parent tile is missing")
    return {
        "origin": [basemap.origin_x, basemap.origin_y],
        "tile_size": basemap.tile_pixels,
        "resolutions": [basemap.resolutions[z] for z in range(basemap.max_lod + 1)],
        "keys": keys, "symbol_types": _SYMBOLS,
    }
