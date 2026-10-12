"""Prepare local map layers once; browsing never calls the scientific adapters."""

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio

from nib_visualization.adapters.cma_mock import read_cma_mock
from nib_visualization.artifacts import sha256_file, verify_wmo_checksum_manifest
from nib_visualization.cma_demo import (
    DEFAULT_COLOR_FILE,
    DEFAULT_INPUT,
    reflectivity_colormap,
)
from nib_visualization.render import _WMO_BOUNDARY_STYLES
from nib_visualization.terrain import ETOPO_COLORS, load_etopo
from nib_visualization.viewer_bundle import export_field_cogs, export_terrain_cog
from nib_visualization.wmo_basemap import crop_wmo_basemap, load_wmo_basemap

WORLD = (-180., 180., -90., 90.)  # Adapter extent order: west, east, south, north.


def prepare_basemaps(bundle, output, terrain_width=7200):
    """Global static context is independent of the weather footprint and frames."""
    output.mkdir(parents=True, exist_ok=True)
    acquisition = json.loads(Path("basemap_sources/etopo2022-acquisition.json").read_text())
    terrain = load_etopo(
        acquisition["local_path"], "basemap_sources/etopo2022-source.json",
        expected_sha256=acquisition["verification"]["sha256"], max_width=terrain_width,
    )
    terrain_path = output / "terrain-world.tif"
    cache_path = output / "terrain-world-cache.json"
    cache_key = {"source_sha256": terrain.metadata["sha256"], "extent": list(WORLD),
                 "max_width": terrain_width, "overview": "nearest", "version": 1}
    cached = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    if (cached.get("key") == cache_key and terrain_path.exists()
            and sha256_file(terrain_path) == cached.get("info", {}).get("sha256")):
        print("Reusing global terrain COG", flush=True)
        info = cached["info"]
    else:
        print(f"Preparing global terrain ({terrain_width} columns)...", flush=True)
        info = export_terrain_cog(terrain.crop(WORLD), terrain_path)
        np.testing.assert_allclose(info["bounds"], [-180, -90, 180, 90], atol=1e-8)
        cache_path.write_text(json.dumps({"key": cache_key, "info": info}))
    bundle["map_extent"] = [-180, -90, 180, 90]
    bundle["terrain"] = {
        "url": terrain_path.name, "colors": ETOPO_COLORS,
        "attribution": terrain.attribution, "disclaimer": terrain.disclaimer,
        "source_sha256": terrain.metadata["sha256"], **info,
        "sampling": terrain.metadata["sampling"],
    }
    wmo = load_wmo_basemap(Path("data/cache/basemap/wmo"), Path("basemap_sources/wmo-source.json"))
    checksum = verify_wmo_checksum_manifest(wmo.path)
    if checksum["mismatched"]:
        raise ValueError("WMO cache checksum mismatch")
    vectors = crop_wmo_basemap(wmo, WORLD)
    vector_path = output / "boundaries-world.geojson"
    vector_path.write_text(vectors.layers["boundary_lines"].to_json(separators=(",", ":")))
    bundle["boundaries"] = {
        "url": vector_path.name, "attribution": wmo.attribution,
        "disclaimer": wmo.disclaimer, "styles": _WMO_BOUNDARY_STYLES,
        "sha256": sha256_file(vector_path), "extent": bundle["map_extent"],
        "detail": "WMO global overview (LOD 1); fixed geometry, no place labels",
        "source_manifest_sha256": checksum["manifest_sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=Path("output/map-viewer/data"))
    parser.add_argument("--display-width", type=int, default=1000)
    parser.add_argument("--sample", action="store_true",
                        help="First/middle/last proof, separate output")
    parser.add_argument("--terrain-width", type=int, default=7200,
                        help="Global terrain display width; source data remain unchanged")
    parser.add_argument("--basemap-only", action="store_true",
                        help="Update an existing viewer bundle without rewriting weather COGs")
    args = parser.parse_args()
    if args.basemap_only:
        manifest = args.output / "viewer.json"
        bundle = json.loads(manifest.read_text())
        original_frames = {f["url"]: sha256_file(args.output / f["url"])
                           for f in bundle["frames"]}
        prepare_basemaps(bundle, args.output, args.terrain_width)
        assert all(sha256_file(args.output / name) == checksum
                   for name, checksum in original_frames.items())
        manifest.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False))
        print(f"Global basemaps ready; {len(original_frames)} weather COGs unchanged", flush=True)
        return
    print("Reading the existing observation-derived mock through its adapter...", flush=True)
    field = read_cma_mock(args.input, crs="EPSG:4326", display_width=args.display_width)
    if args.sample:
        from dataclasses import replace
        indices = sorted({0, field.values.sizes["lead_time"] // 2,
                          field.values.sizes["lead_time"] - 1})
        field = replace(field, values=field.values.isel(lead_time=indices),
                        valid_time=field.valid_time[indices])
    bundle = export_field_cogs(field, args.output)
    bundle.update(
        mock=True, is_real_forecast=False, title="CMA CREF · 雷达反射率",
        notice="实况回放 · 非真实预报", native_shape=field.provenance["native_shape"],
        spatial_sampling=field.provenance["spatial_sampling"],
        special_codes=field.provenance["special_codes"],
        input_files=field.provenance["input_files"],
    )
    cmap = reflectivity_colormap(DEFAULT_COLOR_FILE)
    bundle["weather_style"] = {
        "min": 10, "max": 75, "step": .5,
        "colors": [list(cmap(i)[:3]) for i in range(cmap.N)],
        "over": list(cmap(2.)[:3]), "color_file_sha256": sha256_file(DEFAULT_COLOR_FILE),
    }
    prepare_basemaps(bundle, args.output, args.terrain_width)
    # Full-resolution COG readback: compare every display cell and its affine centres.
    for i, frame in enumerate(bundle["frames"]):
        with rasterio.open(args.output / frame["url"]) as ds:
            expected = field.values.isel(lead_time=i).values
            if field.values.y.values[1] > field.values.y.values[0]:
                expected = expected[::-1]
            if field.values.x.values[1] < field.values.x.values[0]:
                expected = expected[:, ::-1]
            np.testing.assert_array_equal(ds.read(1), expected)
            np.testing.assert_allclose(ds.bounds, bundle["bounds"], atol=1e-9)
    bundle["verification"] = {"all_weather_cells_equal": True, "frame_count": len(bundle["frames"])}
    (args.output / "viewer.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Prepared and verified {len(bundle['frames'])} frames at {args.output}", flush=True)


if __name__ == "__main__":
    main()
