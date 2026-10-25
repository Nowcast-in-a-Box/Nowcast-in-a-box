"""Prepare local viewer basemaps; keep Core and original source files unchanged."""

import gzip
import json
import math
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path, PurePosixPath

import numpy as np
import pyogrio
from rasterio.features import rasterize
from rasterio.transform import from_origin

from nib_visualization.wmo_basemap import _SYMBOLS, _decode_layer, _tile_bounds

# User-selected soft-natural palette for the land-only interactive viewer.
LAND_TERRAIN_COLORS = (
    (-10000, "#dce5d2"),
    (0, "#dce5d2"),
    (500, "#e7e5cd"),
    (1500, "#e3d3b6"),
    (3000, "#c9b293"),
    (5000, "#a59a89"),
    (6500, "#d2cdc3"),
    (8500, "#faf8f2"),
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
    c0 = max(0, math.ceil((left - west) / dx - 0.5))
    c1 = min(mask.shape[1], math.ceil((right - west) / dx - 0.5))
    r0 = max(0, math.ceil((north - top) / dy - 0.5))
    r1 = min(mask.shape[0], math.ceil((north - bottom) / dy - 0.5))
    if c0 >= c1 or r0 >= r1:
        return
    shapes = [(g, 1) for g in geometries if not g.is_empty]
    mask[r0:r1, c0:c1] = (
        rasterize(
            shapes,
            out_shape=(r1 - r0, c1 - c0),
            dtype="uint8",
            transform=from_origin(west + c0 * dx, north - r0 * dy, dx, dy),
        )
        if shapes
        else 0
    )


def land_only_terrain(view, basemap, max_lod=7):
    """Mask by WMO land geometry, never by the sign of elevation."""
    mask = np.zeros(view.values.shape, dtype="uint8")
    coverage = json.loads(
        (basemap.path / "VectorTileServer" / "metadata.json").read_text()
    )["fullExtent"]
    for z, row, col, path in physical_tiles(basemap, min(max_lod, basemap.max_lod)):
        bounds = _tile_bounds(basemap, z, row, col)
        names = {name for name, _ in pyogrio.list_layers(path)}
        geometries = (
            _decode_layer(path, "WMO BNDA", bounds).geometry
            if "WMO BNDA" in names
            else []
        )
        if z > 0:
            # Retain the global parent's polar caps outside the detailed
            # service coverage; an artificial -89.9 cut is not a coastline.
            bounds = (
                max(bounds[0], coverage["xmin"]),
                max(bounds[1], coverage["ymin"]),
                min(bounds[2], coverage["xmax"]),
                min(bounds[3], coverage["ymax"]),
            )
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
        "keys": keys,
        "symbol_types": _SYMBOLS,
    }


_PREPARE = "prepare the offline global terrain and WMO basemap cache"
_WMO_SOURCE = (
    Path(__file__).resolve().parents[2] / "basemap_sources" / "wmo-source.json"
)
_CROP_EXTENT = (-180.0, 180.0, -90.0, 90.0)
_MAP_EXTENT = [-180.0, -90.0, 180.0, 90.0]


def cache_relative(url):
    """Return a cache-relative posix path, rejecting external and escaping paths."""
    if not isinstance(url, str) or url == "" or url.startswith(("/", "\\", "//")):
        raise ValueError(f"basemap asset path must stay inside the cache: {url!r}")
    if ":" in url or "\\" in url:
        raise ValueError(f"basemap asset path must stay inside the cache: {url!r}")
    parts = PurePosixPath(url).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"basemap asset path must stay inside the cache: {url!r}")
    return "/".join(parts)


def prepare_basemap_bundle(
    wmo_path,
    terrain_path,
    terrain_source_record,
    terrain_sha256,
    output,
    *,
    terrain_width=7200,
):
    """Publish terrain, boundaries, map_extent and cache identity under output.

    Writes a temporary directory first and publishes only after the global
    terrain COG, parent tile and relative paths validate. Does not download
    or fall back to a default data directory.
    """
    output = Path(output)
    _require_sha256(terrain_sha256)
    if not isinstance(terrain_width, int) or terrain_width < 2:
        raise ValueError(f"{_PREPARE}: terrain width must be an integer >= 2")
    wmo, checksum, terrain = _open_sources(
        wmo_path, terrain_path, terrain_source_record, terrain_sha256, terrain_width
    )
    cache_key = {
        "source_sha256": terrain_sha256,
        "extent": list(_CROP_EXTENT),
        "max_width": terrain_width,
        "overview": "nearest",
        "version": 4,
        "land_mask": checksum["manifest_sha256"],
        "mask_max_lod": 7,
    }
    published = _matching_cache(output, cache_key)
    if published is not None:
        print("Reusing global terrain COG", flush=True)
        return published
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".basemap-", dir=output.parent))
    try:
        print(f"Preparing land-only terrain ({terrain_width} columns)...", flush=True)
        bundle = _stage_bundle(staging, wmo, terrain, checksum, cache_key)
        _validate_staged(staging, bundle)
        _publish(staging, output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return json.loads((output / "basemap.json").read_text())


def _require_sha256(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{_PREPARE}: terrain sha256 must be 64 lowercase hex digits")


def _open_sources(wmo_path, terrain_path, source_record, terrain_sha256, terrain_width):
    from nib_visualization.artifacts import verify_wmo_checksum_manifest
    from nib_visualization.basemap import BasemapError
    from nib_visualization.terrain import load_etopo
    from nib_visualization.wmo_basemap import load_wmo_basemap

    wmo_path, terrain_path, source_record = (
        Path(wmo_path),
        Path(terrain_path),
        Path(source_record),
    )
    if not _WMO_SOURCE.is_file():
        raise FileNotFoundError(f"WMO source record missing: {_WMO_SOURCE}")
    for path, role in (
        (wmo_path, "WMO cache"),
        (terrain_path, "terrain"),
        (source_record, "terrain source record"),
    ):
        if not path.exists():
            raise FileNotFoundError(f"{_PREPARE}: {role} is missing: {path}")
    try:
        wmo = load_wmo_basemap(wmo_path, _WMO_SOURCE)
        checksum = verify_wmo_checksum_manifest(wmo.path)
    except (OSError, ValueError, BasemapError) as err:
        raise ValueError(
            f"{_PREPARE}: WMO cache is missing or corrupt ({err})"
        ) from err
    if checksum["mismatched"]:
        raise ValueError(f"{_PREPARE}: WMO cache checksum mismatch")
    try:
        terrain = load_etopo(
            terrain_path,
            source_record,
            expected_sha256=terrain_sha256,
            max_width=terrain_width,
        )
    except (OSError, ValueError) as err:
        raise ValueError(f"{_PREPARE}: terrain is missing or corrupt ({err})") from err
    return wmo, checksum, terrain


def _stage_bundle(staging, wmo, terrain, checksum, cache_key):
    from nib_visualization.artifacts import sha256_file
    from nib_visualization.render import _WMO_BOUNDARY_STYLES
    from nib_visualization.viewer_bundle import export_terrain_cog

    terrain_file = staging / "terrain-land.tif"
    view = land_only_terrain(terrain.crop(_CROP_EXTENT), wmo)
    info = _json_ready(export_terrain_cog(view, terrain_file))
    bounds = [float(value) for value in info["bounds"]]
    if any(
        abs(found - expected) > 1e-6 for found, expected in zip(bounds, _MAP_EXTENT)
    ):
        raise ValueError(f"{_PREPARE}: prepared terrain is not the global extent")
    info["bounds"] = bounds
    info["sha256"] = sha256_file(terrain_file)
    tiles = _json_ready(export_wmo_tiles(wmo, staging / "wmo"))
    for key in tiles["keys"]:
        cache_relative(f"wmo/{key}.pbf")
    bundle = {
        "map_extent": list(_MAP_EXTENT),
        "terrain": {
            "url": "terrain-land.tif",
            "colors": [list(stop) for stop in LAND_TERRAIN_COLORS],
            "attribution": terrain.attribution,
            "disclaimer": terrain.disclaimer,
            "source_sha256": cache_key["source_sha256"],
            **info,
            "sampling": terrain.metadata["sampling"],
            "land_only": True,
            "mask_source": "WMO BNDA; physical LOD 0–7; cell centres",
            "mask_source_manifest_sha256": checksum["manifest_sha256"],
        },
        "boundaries": {
            "url": "wmo",
            "tiles": tiles,
            "attribution": wmo.attribution,
            "disclaimer": wmo.disclaimer,
            "styles": _WMO_BOUNDARY_STYLES,
            "extent": list(_MAP_EXTENT),
            "detail": (
                "WMO local vector tiles LOD 0–9; view-dependent with parent "
                "fallback; no labels"
            ),
            "source_manifest_sha256": checksum["manifest_sha256"],
        },
        "cache": {"key": cache_key, "info": info},
    }
    text = (
        json.dumps(_json_ready(bundle), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n"
    )
    (staging / "basemap.json").write_text(text)
    return json.loads(text)


def _validate_staged(staging, bundle):
    from nib_visualization.artifacts import sha256_file

    terrain_url = cache_relative(bundle["terrain"]["url"])
    boundary_url = cache_relative(bundle["boundaries"]["url"])
    terrain_file = staging / terrain_url
    if not terrain_file.is_file():
        raise ValueError(f"{_PREPARE}: terrain COG was not written")
    if sha256_file(terrain_file) != bundle["terrain"]["sha256"]:
        raise ValueError(f"{_PREPARE}: terrain COG hash does not match basemap.json")
    keys = bundle["boundaries"]["tiles"]["keys"]
    if "0/0/0" not in keys:
        raise ValueError(f"{_PREPARE}: global parent tile is missing")
    for key in keys:
        tile = staging / cache_relative(f"{boundary_url}/{key}.pbf")
        if not tile.is_file() or staging.resolve() not in tile.resolve().parents:
            raise ValueError(f"{_PREPARE}: boundary tile escapes the cache")
    leaked = str(staging.resolve())
    if leaked in (staging / "basemap.json").read_text():
        raise ValueError(f"{_PREPARE}: basemap.json contains an absolute path")


def _matching_cache(output, cache_key):
    from nib_visualization.artifacts import sha256_file

    manifest = output / "basemap.json"
    if not manifest.is_file():
        return None
    try:
        record = json.loads(manifest.read_text())
        if record.get("cache", {}).get("key") != cache_key:
            return None
        terrain = output / cache_relative(record["terrain"]["url"])
        if not terrain.is_file() or sha256_file(terrain) != record["terrain"]["sha256"]:
            return None
        boundary = cache_relative(record["boundaries"]["url"])
        for key in record["boundaries"]["tiles"]["keys"]:
            if not (output / cache_relative(f"{boundary}/{key}.pbf")).is_file():
                return None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return record


def _publish(staging, output):
    output.mkdir(parents=True, exist_ok=True)
    files = [
        path
        for path in staging.rglob("*")
        if path.is_file() and path.name != "basemap.json"
    ]
    for path in files:
        dest = output / path.relative_to(staging)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_name(dest.name + ".new")
        shutil.copy2(path, temporary)
        os.replace(temporary, dest)
    os.replace(staging / "basemap.json", output / "basemap.json")


def _json_ready(value):
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value
