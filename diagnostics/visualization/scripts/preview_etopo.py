"""Crop ETOPO at native resolution and render two offline terrain previews.

Uses the existing CMA preview viewport unless --bbox W E S N is supplied.
Requires netCDF4 in addition to the project environment; see the terrain guide.
This source-side utility does not change the Forecast renderer or its contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import textwrap
import warnings
from datetime import UTC, datetime
from pathlib import Path

import cartopy.crs as ccrs
import matplotlib
import netCDF4
import numpy as np
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402

from nib_visualization.basemap import crop_basemap  # noqa: E402
from nib_visualization.render import _draw_basemap_layers  # noqa: E402
from nib_visualization.wmo_basemap import load_wmo_basemap  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PALETTE = [
    (-10000, "#173955"),
    (-6000, "#32678a"),
    (-3000, "#75a5bc"),
    (-1, "#d8eaf0"),
    (0, "#cad9b5"),
    (500, "#d5dbb5"),
    (1500, "#e4d3ae"),
    (3000, "#c2a580"),
    (5000, "#8d8070"),
    (6500, "#c5c0b6"),
    (8500, "#faf8f1"),
]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def covering_slice(axis: np.ndarray, low: float, high: float) -> slice:
    """Retain every native cell intersecting the requested viewport."""
    step = float(axis[1] - axis[0])
    if step <= 0 or not np.allclose(np.diff(axis), step, rtol=0, atol=1e-10):
        raise ValueError("Expected an ascending, regular ETOPO coordinate axis")
    edge = float(axis[0] - step / 2)
    first = max(0, math.floor((low - edge) / step + 1e-8))
    stop = min(len(axis), math.ceil((high - edge) / step - 1e-8))
    if first >= stop:
        raise ValueError("Viewport does not intersect the ETOPO grid")
    return slice(first, stop)


def crop_native(source: Path, target: Path, bbox: list[float]):
    west, east, south, north = bbox
    with netCDF4.Dataset(source) as src:
        lat, lon = np.asarray(src["lat"][:]), np.asarray(src["lon"][:])
        ys, xs = covering_slice(lat, south, north), covering_slice(lon, west, east)
        lat, lon = lat[ys], lon[xs]
        z = np.asarray(src["z"][ys, xs])
        if not np.isfinite(z).all() or np.any(z == src["z"]._FillValue):
            raise ValueError("Unexpected missing or nonfinite terrain cells")
        dx, dy = float(lon[1] - lon[0]), float(lat[1] - lat[0])
        edges = [
            float(lon[0] - dx / 2),
            float(lon[-1] + dx / 2),
            float(lat[0] - dy / 2),
            float(lat[-1] + dy / 2),
        ]
        with netCDF4.Dataset(target, "w", format="NETCDF4_CLASSIC") as dst:
            dst.setncatts({k: src.getncattr(k) for k in src.ncattrs()})
            dst.history = f"{datetime.now(UTC).isoformat()}: native grid slice; no regridding"
            dst.source = source.name
            dst.requested_bbox_west_east_south_north = np.asarray(bbox)
            for name, values in (("lat", lat), ("lon", lon)):
                dst.createDimension(name, len(values))
                variable = dst.createVariable(name, "f8", (name,))
                variable.setncatts({k: src[name].getncattr(k) for k in src[name].ncattrs()})
                variable[:] = values
            crs = dst.createVariable("crs", "S1")
            crs.setncatts({k: src["crs"].getncattr(k) for k in src["crs"].ncattrs()})
            crs.GeoTransform = f"{edges[0]} {dx} 0 {edges[3]} 0 {-dy}"
            variable = dst.createVariable(
                "z", "f4", ("lat", "lon"), zlib=True, complevel=4, fill_value=src["z"]._FillValue
            )
            variable.setncatts(
                {k: src["z"].getncattr(k) for k in src["z"].ncattrs() if k != "_FillValue"}
            )
            # netCDF4 1.7.4 uses the deprecated ndarray.shape setter internally
            # with NumPy 2.5; the write is verified by an exact full-array reread below.
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="Setting the shape on a NumPy array has been deprecated.*",
                    category=DeprecationWarning,
                )
                variable[...] = z
    # Reopen the saved crop: prove lossless storage, coordinate order and full decode.
    with netCDF4.Dataset(target) as check:
        np.testing.assert_array_equal(check["lat"][:], lat)
        np.testing.assert_array_equal(check["lon"][:], lon)
        np.testing.assert_array_equal(check["z"][:], z)
    return z, edges, {"lat": [ys.start, ys.stop], "lon": [xs.start, xs.stop]}


def display_sample(z: np.ndarray, max_width: int = 2000):
    """Nearest native cell at each display-pixel centre; no scientific regridding."""
    height, width = z.shape
    out_w = min(width, max_width)
    out_h = max(1, round(height * out_w / width))
    rows = np.floor((np.arange(out_h) + 0.5) * height / out_h).astype(int)
    cols = np.floor((np.arange(out_w) + 0.5) * width / out_w).astype(int)
    return z[np.ix_(rows, cols)]


def render_preview(display, edges, bbox, view, output: Path, overlay: bool):
    norm = Normalize(PALETTE[0][0], PALETTE[-1][0])
    cmap = LinearSegmentedColormap.from_list(
        "etopo_relief", [(float(norm(value)), color) for value, color in PALETTE], N=4096
    )
    fig = plt.figure(figsize=(12, 10), dpi=150, facecolor="white")
    ax = fig.add_axes([0.065, 0.225, 0.77, 0.635], projection=ccrs.PlateCarree())
    ax.set_extent(bbox, crs=ccrs.PlateCarree())
    if overlay:
        _draw_basemap_layers(ax, view)
    artist = ax.imshow(
        display,
        origin="lower",
        extent=edges,
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
        cmap=cmap,
        norm=norm,
        zorder=1,
    )
    grid = ax.gridlines(draw_labels=True, linewidth=0.35, color="#344553", alpha=0.20)
    grid.top_labels = grid.right_labels = False
    grid.xlabel_style = grid.ylabel_style = {"size": 10}
    cax = fig.add_axes([0.865, 0.28, 0.019, 0.53])
    fig.colorbar(
        artist,
        cax=cax,
        ticks=[-10000, -6000, -2000, 0, 2000, 4000, 6000, 8500],
        label="Elevation relative to EGM2008 (m)",
    )
    title = "Terrain + WMO boundaries" if overlay else "Terrain only"
    fig.text(0.065, 0.945, title, fontsize=21, weight="bold", color="#253444")
    fig.text(0.065, 0.907, "ETOPO 2022  |  CMA radar preview domain", fontsize=13, color="#52616d")
    west, east, south, north = bbox
    fig.text(
        0.065,
        0.183,
        f"{west:.3f}-{east:.3f} E  |  {south:.3f}-{north:.3f} N  |  WGS84 / 30 arc-sec",
        fontsize=10,
        color="#42515d",
    )
    fig.text(
        0.065,
        0.156,
        "Blue indicates elevation below 0 m, not a land/sea mask.",
        fontsize=9,
        color="#52616d",
    )
    fig.text(
        0.065,
        0.133,
        "Display sampled with nearest cells; the regional NetCDF retains all native values.",
        fontsize=9,
        color="#52616d",
    )
    if overlay:
        fig.text(
            0.065,
            0.109,
            "WMO native boundary types and line styles retained; no official place-name labels.",
            fontsize=9,
            color="#52616d",
        )
    footer = "Terrain: NOAA NCEI, ETOPO 2022, doi:10.25921/fd45-gt74. Not for navigation. " + (
        view.attribution + " " + view.disclaimer if overlay else ""
    )
    fig.text(
        0.065,
        0.025,
        textwrap.fill(footer.strip(), 153),
        fontsize=7.5,
        color="#5b646b",
        va="bottom",
        linespacing=1.5,
    )
    fig.savefig(output)
    plt.close(fig)
    with Image.open(output) as image:
        image.load()
        size = list(image.size)
    return {"path": str(output.relative_to(ROOT)), "sha256": sha256(output), "size_px": size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("W", "E", "S", "N"))
    parser.add_argument("--output", type=Path, default=ROOT / "output/terrain/cma-domain")
    parser.add_argument(
        "--crop", type=Path, default=ROOT / "data/cache/terrain/etopo2022/cma-domain.nc"
    )
    args = parser.parse_args()
    reference = ROOT / "output/cma-cref/npz-preview/manifest.json"
    bbox = args.bbox or json.loads(reference.read_text())["extent_west_east_south_north"]
    west, east, south, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Expected a non-wrapping bbox in WGS84: W E S N")
    source_record = ROOT / "basemap_sources/etopo2022-source.json"
    source = ROOT / json.loads(source_record.read_text())["local_path"]
    acquisition = json.loads((ROOT / "basemap_sources/etopo2022-acquisition.json").read_text())
    source_hash = sha256(source)
    if source_hash != acquisition["verification"]["sha256"]:
        raise ValueError("ETOPO source differs from the verified download")
    args.output.mkdir(parents=True, exist_ok=True)
    args.crop.parent.mkdir(parents=True, exist_ok=True)
    z, edges, indices = crop_native(source, args.crop, bbox)
    print(f"Saved native crop: {z.shape}, {z.size} cells", flush=True)
    display = display_sample(z)
    wmo_record = ROOT / "basemap_sources/wmo-source.json"
    view = crop_basemap(load_wmo_basemap(ROOT / "data/cache/basemap/wmo", wmo_record), bbox)
    products = [
        render_preview(display, edges, bbox, view, args.output / name, overlay)
        for name, overlay in [("terrain-only.png", False), ("terrain-wmo.png", True)]
    ]
    if sha256(source) != source_hash:
        raise ValueError("Source changed during the preview")
    manifest = {
        "kind": "ETOPO source-side crop and terrain preview; not renderer integration",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source": {"path": str(source), "sha256": source_hash},
        "viewport_origin": "explicit --bbox" if args.bbox else str(reference),
        "requested_bbox_west_east_south_north": bbox,
        "crop": {
            "path": str(args.crop),
            "sha256": sha256(args.crop),
            "shape": list(z.shape),
            "cell_edges_west_east_south_north": edges,
            "source_slices": indices,
            "minimum_m": float(z.min()),
            "maximum_m": float(z.max()),
            "horizontal_crs": "EPSG:4326",
            "vertical_crs": "EPSG:3855",
        },
        "display": {
            "shape": list(display.shape),
            "resampling": "nearest native cell",
            "norm": "linear",
            "range_m": [PALETTE[0][0], PALETTE[-1][0]],
            "palette_m_hex": PALETTE,
            "hillshade": False,
        },
        "wmo": {
            "source_record_sha256": sha256(wmo_record),
            "boundary_type_counts": {
                str(k): int(v)
                for k, v in view.layers["boundary_lines"].boundary_type.value_counts().items()
            },
        },
        "outputs": products,
        "script_sha256": sha256(Path(__file__)),
        "checks": {
            "source_unchanged": True,
            "native_crop_exact_equality": True,
            "all_crop_cells_decoded": True,
            "png_full_decode": True,
            "visual_review": "pending",
        },
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Saved previews and manifest: {args.output}", flush=True)


if __name__ == "__main__":
    main()
