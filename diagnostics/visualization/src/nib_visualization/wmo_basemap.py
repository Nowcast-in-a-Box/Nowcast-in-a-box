"""Offline WMO WGS84 vector-tile adapter for the existing BasemapView renderer.

GDAL's MVT reader assumes Web Mercator XYZ. The WMO service instead uses an
EPSG:4326 ArcGIS tileInfo grid. Read every PBF at synthetic XYZ 0/0/0 to
decode tile-local geometry, then map its normalized coordinates to the
official tileInfo origin/resolution. No network or raster screenshot is used.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.affinity import affine_transform
from shapely.geometry import box

from nib_visualization.basemap import (
    BASEMAP_UNAVAILABLE,
    INVALID_SOURCE_RECORD,
    WGS84,
    BasemapError,
    BasemapView,
    _validate_extent,
)

_MERCATOR_HALF_WORLD = 20037508.342789244
_SYMBOLS = {
    0: "coastline",
    1: "international_boundary",
    2: "special_boundary_line",
    3: "armistice_or_international_administrative_line",
    4: "other_line_of_separation",
    5: "autonomous_region_boundary",
}


@dataclass(frozen=True)
class WmoBasemap:
    """Verified local WMO tile mirror and its official WGS84 tiling grid."""

    path: Path
    source_id: str
    attribution: str
    disclaimer: str
    origin_x: float
    origin_y: float
    tile_pixels: int
    resolutions: dict[int, float]
    max_lod: int


def load_wmo_basemap(path: Path | str, source_record: Path | str) -> WmoBasemap:
    """Load only metadata; tile data is opened lazily for each requested view."""
    root = Path(path)
    metadata_path = root / "VectorTileServer" / "metadata.json"
    acquisition_path = root / "acquisition.json"
    if not metadata_path.is_file():
        raise BasemapError(BASEMAP_UNAVAILABLE, f"WMO tile metadata missing: {metadata_path}")
    try:
        record = json.loads(Path(source_record).read_text(encoding="utf-8"))
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        acquisition = json.loads(acquisition_path.read_text(encoding="utf-8"))
        tile_info = metadata["tileInfo"]
        if tile_info["spatialReference"]["wkid"] != 4326 or tile_info["format"] != "pbf":
            raise ValueError("expected EPSG:4326 PBF tileInfo")
        if tile_info["rows"] != tile_info["cols"]:
            raise ValueError("non-square WMO tiles are unsupported")
        resolutions = {int(lod["level"]): float(lod["resolution"]) for lod in tile_info["lods"]}
        return WmoBasemap(
            path=root,
            source_id=record["source_id"],
            attribution=record["attribution"],
            disclaimer=record["disclaimer"],
            origin_x=float(tile_info["origin"]["x"]),
            origin_y=float(tile_info["origin"]["y"]),
            tile_pixels=int(tile_info["cols"]),
            resolutions=resolutions,
            max_lod=int(acquisition["cache_max_lod"]),
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as err:
        raise BasemapError(
            INVALID_SOURCE_RECORD, f"WMO metadata/source record invalid: {err}"
        ) from err


def resolve_tile(root: Path | str, lod: int, row: int, col: int) -> tuple[Path, int, int, int]:
    """Find physical tile at this LOD or nearest ancestor (ArcGIS resample semantics)."""
    root = Path(root) / "VectorTileServer" / "tile"
    for parent_lod in range(lod, -1, -1):
        shift = lod - parent_lod
        parent_row, parent_col = row >> shift, col >> shift
        tile = root / str(parent_lod) / str(parent_row) / f"{parent_col}.pbf"
        if tile.is_file():
            return tile, parent_lod, parent_row, parent_col
    raise FileNotFoundError(f"no WMO tile or ancestor for {lod}/{row}/{col}")


def _tile_bounds(
    basemap: WmoBasemap, lod: int, row: int, col: int
) -> tuple[float, float, float, float]:
    span = basemap.tile_pixels * basemap.resolutions[lod]
    west = basemap.origin_x + col * span
    north = basemap.origin_y - row * span
    return west, north - span, west + span, north


def _decode_layer(
    tile: Path, name: str, bounds: tuple[float, float, float, float]
) -> gpd.GeoDataFrame:
    """Decode MVT tile-local geometry and apply the WMO geographic grid."""
    frame = pyogrio.read_dataframe(tile, layer=name, X=0, Y=0, Z=0, METADATA_FILE="", CLIP="YES")
    west, south, east, north = bounds
    scale_x = (east - west) / (2 * _MERCATOR_HALF_WORLD)
    scale_y = (north - south) / (2 * _MERCATOR_HALF_WORLD)
    frame.geometry = frame.geometry.map(
        lambda geom: affine_transform(
            geom,
            [scale_x, 0, 0, scale_y, (west + east) / 2, (south + north) / 2],
        )
    )
    return frame.set_crs(WGS84, allow_override=True)


def _choose_lod(basemap: WmoBasemap, width_degrees: float) -> int:
    """At 1000px output, use enough tile detail without scanning z9 worldwide."""
    ideal = math.ceil(math.log2(360.0 / width_degrees * (1000.0 / basemap.tile_pixels)))
    return max(0, min(basemap.max_lod, ideal))


def crop_wmo_basemap(
    basemap: WmoBasemap, extent_wgs84: tuple[float, float, float, float]
) -> BasemapView:
    """Produce WGS84 land and typed boundaries for a viewport, fully offline."""
    west, east, south, north = _validate_extent(extent_wgs84)
    lod = _choose_lod(basemap, east - west)
    span = basemap.tile_pixels * basemap.resolutions[lod]
    first_col = math.floor((west - basemap.origin_x) / span)
    last_col = math.ceil((east - basemap.origin_x) / span) - 1
    first_row = math.floor((basemap.origin_y - north) / span)
    last_row = math.ceil((basemap.origin_y - south) / span) - 1

    physical: dict[Path, tuple[int, int, int]] = {}
    for row in range(first_row, last_row + 1):
        for col in range(first_col, last_col + 1):
            tile, tile_lod, tile_row, tile_col = resolve_tile(basemap.path, lod, row, col)
            physical[tile] = (tile_lod, tile_row, tile_col)

    viewport = box(west, south, east, north)
    lands = []
    oceans = []
    lines = []
    types = []
    for tile, (tile_lod, tile_row, tile_col) in physical.items():
        bounds = _tile_bounds(basemap, tile_lod, tile_row, tile_col)
        names = {name for name, _geometry_type in pyogrio.list_layers(tile)}
        if "WMO BNDA" in names:
            background = _decode_layer(tile, "WMO BNDA", bounds)
            for geom in background.geometry:
                land = geom.intersection(viewport)
                if not land.is_empty:
                    lands.append(land)
        if "Ocean" in names:
            ocean = _decode_layer(tile, "Ocean", bounds)
            for geom in ocean.geometry:
                water = geom.intersection(viewport)
                if not water.is_empty:
                    oceans.append(water)
        if "Boundaries" in names:
            boundary = _decode_layer(tile, "Boundaries", bounds)
            for symbol, geom in zip(boundary["_symbol"], boundary.geometry, strict=True):
                clipped = geom.intersection(viewport)
                if not clipped.is_empty:
                    lines.append(clipped)
                    types.append(_SYMBOLS.get(int(symbol), "other"))

    return BasemapView(
        layers={
            "ocean": gpd.GeoDataFrame(geometry=oceans, crs=WGS84),
            "country_land": gpd.GeoDataFrame(geometry=lands, crs=WGS84),
            "boundary_lines": gpd.GeoDataFrame({"boundary_type": types}, geometry=lines, crs=WGS84),
        },
        extent_wgs84=(west, east, south, north),
        attribution=basemap.attribution,
        disclaimer=basemap.disclaimer,
        source_id=basemap.source_id,
    )
