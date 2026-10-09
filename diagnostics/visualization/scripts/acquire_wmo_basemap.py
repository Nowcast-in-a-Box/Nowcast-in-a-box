"""Mirror the official WMO 1:1,000,000 vector basemap for local use.

The official ArcGIS item does not expose an exportable VTPK. This acquisition
script therefore mirrors the public VectorTileServer metadata, style assets,
glyphs, and all non-empty PBF tiles through the first service LOD that is not
coarser than the source's stated 1:1,000,000 cartographic scale.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

TARGET_SCALE_DENOMINATOR = 1_000_000
TILEMAP_BLOCK_SIZE = 128
WEBMAP_ITEM_ID = "c22f616a8130474eb6b8bda7d35936d9"
VECTOR_ITEM_ID = "f278face2e254f9c846eed2a212efcd1"
VECTOR_SERVICE_URL = (
    "https://tiles.arcgis.com/tiles/supi6xDj3qduY1gn/arcgis/rest/services/"
    "WMO_Agreed_Basemap_4326_/VectorTileServer"
)
ARCGIS_ITEM_URL = "https://www.arcgis.com/sharing/rest/content/items"
FONT_NAMES = ("Arial Regular", "Arial Bold", "Arial Italic")
FONT_RANGES = ("0-255", "256-511")
SPRITE_NAMES = ("sprite.json", "sprite.png", "sprite@2x.json", "sprite@2x.png")
USER_AGENT = "nib-visualization/0.1 (WMO internal basemap acquisition)"


class AcquisitionError(RuntimeError):
    """Raised when a required official resource is unavailable or malformed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_bytes(url: str) -> bytes:
    result = subprocess.run(
        [
            "curl", "--fail-with-body", "--silent", "--show-error", "--location",
            "--max-time", "60", "--retry", "2", "--retry-delay", "1",
            "--user-agent", USER_AGENT, url,
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise AcquisitionError(f"failed to fetch {url}: {message}")
    return result.stdout


def save_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(path)


def download_resource(url: str, path: Path, force: bool = False) -> Path:
    if force or not path.is_file() or path.stat().st_size == 0:
        save_bytes(path, fetch_bytes(url))
    return path


def select_cache_max_lod(lods: list[dict], target_scale: int) -> int:
    """Return the first LOD whose nominal scale is not coarser than target."""
    for lod in sorted(lods, key=lambda item: item["level"]):
        if lod["scale"] <= target_scale:
            return int(lod["level"])
    raise AcquisitionError(f"no service LOD reaches 1:{target_scale:,}")


def tile_grid_bounds(
    full_extent: dict,
    origin: dict,
    resolution: float,
    tile_rows: int,
    tile_cols: int,
) -> tuple[int, int, int, int]:
    """Return inclusive (row_min, row_max, col_min, col_max)."""
    tile_height = resolution * tile_rows
    tile_width = resolution * tile_cols
    row_min = math.floor((origin["y"] - full_extent["ymax"]) / tile_height)
    row_max = math.ceil((origin["y"] - full_extent["ymin"]) / tile_height) - 1
    col_min = math.floor((full_extent["xmin"] - origin["x"]) / tile_width)
    col_max = math.ceil((full_extent["xmax"] - origin["x"]) / tile_width) - 1
    return row_min, row_max, col_min, col_max


def available_tiles(payload: dict, level: int) -> list[tuple[int, int, int]]:
    """Decode an ArcGIS tilemap block into (level, row, col) coordinates."""
    if "error" in payload:
        error = payload["error"]
        if (error.get("code") == 422 and
                error.get("message") == "No tile available for the specified boundary."):
            return []
        raise AcquisitionError(f"tilemap error at LOD {level}: {error}")
    location = payload["location"]
    left, top = location["left"], location["top"]
    width, height = location["width"], location["height"]
    data = payload["data"]
    if len(data) != width * height or not all(value in (0, 1) for value in data):
        raise AcquisitionError(f"invalid tilemap block at LOD {level}, row {top}, col {left}")
    return [
        (level, top + index // width, left + index % width)
        for index, value in enumerate(data)
        if value == 1
    ]


def list_official_tiles(service: dict, max_lod: int) -> list[tuple[int, int, int]]:
    tile_info = service["tileInfo"]
    tiles = []
    for lod in tile_info["lods"]:
        level = lod["level"]
        if level > max_lod:
            continue
        row_min, row_max, col_min, col_max = tile_grid_bounds(
            service["fullExtent"],
            tile_info["origin"],
            lod["resolution"],
            tile_info["rows"],
            tile_info["cols"],
        )
        level_tiles = []
        for top in range((row_min // TILEMAP_BLOCK_SIZE) * TILEMAP_BLOCK_SIZE,
                         row_max + 1, TILEMAP_BLOCK_SIZE):
            for left in range((col_min // TILEMAP_BLOCK_SIZE) * TILEMAP_BLOCK_SIZE,
                              col_max + 1, TILEMAP_BLOCK_SIZE):
                width = min(TILEMAP_BLOCK_SIZE, col_max - left + 1)
                height = min(TILEMAP_BLOCK_SIZE, row_max - top + 1)
                tilemap_url = (
                    f"{VECTOR_SERVICE_URL}/tilemap/{level}/{top}/{left}/"
                    f"{width}/{height}?f=json"
                )
                block = json.loads(fetch_bytes(tilemap_url))
                level_tiles.extend(
                    tile for tile in available_tiles(block, level)
                    if row_min <= tile[1] <= row_max and col_min <= tile[2] <= col_max
                )
        print(f"LOD {level}: {len(level_tiles)} non-empty tiles")
        tiles.extend(level_tiles)
    return sorted(set(tiles))


def mirror_tile(root: Path, tile: tuple[int, int, int]) -> Path:
    level, row, col = tile
    path = root / "VectorTileServer" / "tile" / str(level) / str(row) / f"{col}.pbf"
    if path.is_file() and path.stat().st_size > 0:
        return path
    data = fetch_bytes(f"{VECTOR_SERVICE_URL}/tile/{level}/{row}/{col}.pbf")
    if not data.startswith(b"\x1f\x8b"):
        raise AcquisitionError(f"unexpected PBF response for LOD {level}, row {row}, col {col}")
    save_bytes(path, data)
    return path


def write_checksums(root: Path, paths: list[Path]) -> str:
    lines = [f"{sha256_file(path)}  {path.relative_to(root).as_posix()}" for path in sorted(paths)]
    manifest = root / "SHA256SUMS.txt"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sha256_file(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=Path("data/cache/basemap/wmo"))
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    metadata_urls = {
        "webmap-item.json": f"{ARCGIS_ITEM_URL}/{WEBMAP_ITEM_ID}?f=pjson",
        "webmap-data.json": f"{ARCGIS_ITEM_URL}/{WEBMAP_ITEM_ID}/data?f=json",
        "vector-item.json": f"{ARCGIS_ITEM_URL}/{VECTOR_ITEM_ID}?f=pjson",
        "VectorTileServer/metadata.json": f"{VECTOR_SERVICE_URL}?f=pjson",
        "VectorTileServer/resources/styles/root.json": (
            f"{VECTOR_SERVICE_URL}/resources/styles/root.json"
        ),
    }
    metadata_paths = [
        download_resource(url, root / name, force=True)
        for name, url in metadata_urls.items()
    ]
    webmap_item = json.loads((root / "webmap-item.json").read_text())
    webmap_data = json.loads((root / "webmap-data.json").read_text())
    vector_item = json.loads((root / "vector-item.json").read_text())
    service = json.loads((root / "VectorTileServer/metadata.json").read_text())
    style = json.loads((root / "VectorTileServer/resources/styles/root.json").read_text())
    if webmap_item.get("id") != WEBMAP_ITEM_ID or vector_item.get("id") != VECTOR_ITEM_ID:
        raise AcquisitionError("ArcGIS item identity mismatch")
    if service.get("serviceItemId") != VECTOR_ITEM_ID or style.get("version") != 8:
        raise AcquisitionError("WMO service/style identity mismatch")
    webmap_layers = webmap_data["baseMap"]["baseMapLayers"]
    if not any(layer.get("itemId") == VECTOR_ITEM_ID for layer in webmap_layers):
        raise AcquisitionError("web map no longer references the expected vector tile item")
    max_lod = select_cache_max_lod(service["tileInfo"]["lods"], TARGET_SCALE_DENOMINATOR)
    print(f"WMO source scale 1:{TARGET_SCALE_DENOMINATOR:,}; mirror through LOD {max_lod}")

    resource_paths = []
    for name in SPRITE_NAMES:
        url = f"{VECTOR_SERVICE_URL}/resources/sprites/{name}"
        resource_paths.append(download_resource(
            url, root / "VectorTileServer/resources/sprites" / name
        ))
    for font_name in FONT_NAMES:
        encoded_font = urllib.parse.quote(font_name)
        for glyph_range in FONT_RANGES:
            url = (
                f"{VECTOR_SERVICE_URL}/resources/fonts/"
                f"{encoded_font}/{glyph_range}.pbf"
            )
            resource_paths.append(download_resource(
                url,
                root / "VectorTileServer/resources/fonts" / font_name /
                f"{glyph_range}.pbf",
            ))

    tiles = list_official_tiles(service, max_lod)
    tile_paths = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(mirror_tile, root, tile): tile for tile in tiles}
        for index, future in enumerate(as_completed(futures), start=1):
            tile_paths.append(future.result())
            if index % 500 == 0 or index == len(tiles):
                print(f"PBF tiles: {index}/{len(tiles)}")

    checksums_sha256 = write_checksums(root, metadata_paths + resource_paths + tile_paths)
    acquired = {
        "acquired_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_webmap_item_id": WEBMAP_ITEM_ID,
        "vector_tile_item_id": VECTOR_ITEM_ID,
        "service_url": VECTOR_SERVICE_URL,
        "cartographic_scale_note": (
            "WMO states suitable for global mapping at 1:1,000,000 and above; "
            "not a positional-accuracy certification"
        ),
        "target_scale_denominator": TARGET_SCALE_DENOMINATOR,
        "cache_max_lod": max_lod,
        "cache_max_lod_nominal_scale": next(
            lod["scale"] for lod in service["tileInfo"]["lods"] if lod["level"] == max_lod
        ),
        "spatial_reference_wkid": service["tileInfo"]["spatialReference"]["wkid"],
        "full_extent": service["fullExtent"],
        "tile_count": len(tiles),
        "tile_counts_by_lod": {
            str(level): sum(tile[0] == level for tile in tiles)
            for level in range(max_lod + 1)
        },
        "resource_count": len(metadata_paths) + len(resource_paths),
        "checksum_manifest": "SHA256SUMS.txt",
        "checksum_manifest_sha256": checksums_sha256,
        "note": (
            "Public VectorTileServer mirror for approved local use; official service "
            "exportTilesAllowed=false. WMO disclaimer map service has no available "
            "export package and is recorded in webmap-data.json."
        ),
    }
    (root / "acquisition.json").write_text(
        json.dumps(acquired, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"complete: {len(tiles)} PBF tiles; checksums {checksums_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
