"""Thin argparse CLI: pure local basemap render (VIS-001), 2D field map
(VIS-004), lead-time GIF animation (VIS-005), the one-command Core demo
chain (VIS-006), and a local artifact gallery (VIS-008).

The ``basemap`` subcommand loads the local GeoPackage master, crops it to a
WGS84 viewport and writes a PNG. The ``map`` subcommand chains the synthetic
fixture, the local basemap and the shared renderer into one scientific map
PNG (make_fixture -> load_basemap -> prepare_scene -> render_frame -> save).
The ``animate`` subcommand plays every existing lead of the same fixture
through one prepared scene into a GIF (prepare_scene once ->
write_animation). The ``demo`` subcommand runs that same chain once per
output directory and additionally writes the run manifest (map.png +
animation.gif + manifest.json); it draws and animates through the exact
same APIs, never a second copy of the drawing logic.

Usage:
    python -m nib_visualization.cli basemap \
        --basemap data/cache/basemap/global.gpkg \
        --source-record basemap_sources/selected-source.json \
        --bbox 5 15 47 55 --output output/basemap
    python -m nib_visualization.cli map --grid latlon --lead-index 0 \
        --basemap data/cache/basemap/global.gpkg \
        --source-record basemap_sources/selected-source.json \
        --output output/map/latlon.png
    python -m nib_visualization.cli animate --provider wmo --grid latlon \
        --basemap data/cache/basemap/wmo \
        --output output/animation/wmo/animation.gif
    python -m nib_visualization.cli demo --provider wmo --grid latlon \
        --basemap data/cache/basemap/wmo \
        --output output/core-mvp/wmo/latlon
    python -m nib_visualization.cli gallery \
        --manifest output/core-mvp/wmo/latlon/manifest.json
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering, no display required

import cartopy.crs as ccrs  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from cartopy.feature import ShapelyFeature  # noqa: E402

from nib_visualization.animation import write_animation
from nib_visualization.artifacts import inspect_outputs, verify_source_identity, write_manifest
from nib_visualization.basemap import BasemapError, BasemapView, crop_basemap, load_basemap
from nib_visualization.field import FieldValidationError
from nib_visualization.fixtures import DEFAULT_N_LEADS, make_fixture
from nib_visualization.gallery import write_gallery
from nib_visualization.render import RenderConfig, prepare_scene, render_frame
from nib_visualization.wmo_basemap import load_wmo_basemap

DEFAULT_SOURCE_RECORD = Path("basemap_sources") / "selected-source.json"
WMO_SOURCE_RECORD = Path("basemap_sources") / "wmo-source.json"
DEFAULT_ACQUISITION_RECORD = Path("basemap_sources") / "acquisition.json"
WMO_ACQUISITION_RECORD = Path("basemap_sources") / "wmo-acquisition.json"
WMO_CACHE = Path("data") / "cache" / "basemap" / "wmo"

# Line styles per boundary type preserve the source's boundary-type semantics
# (coastline vs international vs other separation lines) in the output figure.
# Every line style must keep facecolor="none": line geometries handed to
# ShapelyFeature otherwise get matplotlib's opaque default face fill, which
# renders as spurious filled wedges (review finding F-001).
BOUNDARY_STYLES: dict[str, dict] = {
    "coastline": {
        "facecolor": "none",
        "edgecolor": "#2c5f8a",
        "linewidth": 0.9,
        "linestyle": "-",
    },
    "international_boundary": {
        "facecolor": "none",
        "edgecolor": "#5a5a5a",
        "linewidth": 0.9,
        "linestyle": "-",
    },
    "armistice_or_international_administrative_line": {
        "facecolor": "none",
        "edgecolor": "#7a7a7a",
        "linewidth": 0.7,
        "linestyle": (0, (6, 2, 1, 2)),
    },
    "special_boundary_line": {
        "facecolor": "none",
        "edgecolor": "#7a7a7a",
        "linewidth": 0.7,
        "linestyle": "--",
    },
    "other_line_of_separation": {
        "facecolor": "none",
        "edgecolor": "#9a9a9a",
        "linewidth": 0.6,
        "linestyle": "--",
    },
    "autonomous_region_boundary": {
        "facecolor": "none",
        "edgecolor": "#9a9a9a",
        "linewidth": 0.6,
        "linestyle": ":",
    },
    "sovereign_base_limit": {
        "facecolor": "none",
        "edgecolor": "#9a9a9a",
        "linewidth": 0.6,
        "linestyle": ":",
    },
    "other": {
        "facecolor": "none",
        "edgecolor": "#b0b0b0",
        "linewidth": 0.5,
        "linestyle": ":",
    },
}
UNCLASSIFIED_LINE_STYLE = {
    "facecolor": "none",
    "edgecolor": "#b0b0b0",
    "linewidth": 0.5,
    "linestyle": ":",
}
# Optional lakes retain a water fill; country land uses a separate neutral background.
LAKE_STYLE = {"facecolor": "#cfe3f2", "edgecolor": "#2c5f8a", "linewidth": 0.4}
# Country polygons provide a quiet land/ocean figure-ground cue beneath linework.
# They deliberately carry no labels or thematic color semantics.
COUNTRY_LAND_STYLE = {"facecolor": "#f1efe8", "edgecolor": "none", "linewidth": 0.0}
WMO_COUNTRY_LAND_STYLE = {"facecolor": "#E6E7E8", "edgecolor": "none", "linewidth": 0.0}
WMO_OCEAN_COLOR = "#E1E8F5"
WMO_BOUNDARY_STYLES = {
    "coastline": {"facecolor": "none", "edgecolor": "#A1B6D3", "linewidth": 0.35},
    "international_boundary": {
        "facecolor": "none",
        "edgecolor": "#A9ABAD",
        "linewidth": 1.0,
        "linestyle": "--",
    },
    "special_boundary_line": {
        "facecolor": "none",
        "edgecolor": "#4E4E4E",
        "linewidth": 1.3,
        "linestyle": "--",
    },
    "armistice_or_international_administrative_line": {
        "facecolor": "none",
        "edgecolor": "#A9ABAD",
        "linewidth": 1.0,
        "linestyle": "--",
    },
    "other_line_of_separation": {
        "facecolor": "none",
        "edgecolor": "#A9ABAD",
        "linewidth": 1.0,
        "linestyle": ":",
    },
    "autonomous_region_boundary": {
        "facecolor": "none",
        "edgecolor": "#A9ABAD",
        "linewidth": 0.7,
        "linestyle": "--",
    },
}

# Footer layout (review finding F-002): attribution + disclaimer are wrapped
# deterministically instead of one overlong single line, with the axes region
# raised so the footer cannot be clipped by or overlap the map.
FOOTER_FONTSIZE = 6
FOOTER_WRAP_WIDTH = 210  # chars: ~865px at 6pt, inside the 1000px canvas
FOOTER_FIRST_LINE_Y = 0.008
FOOTER_LINE_SPACING = 0.019  # figure fraction, ~13px at the 700px canvas height
FOOTER_BOTTOM_MARGIN = 0.14  # axes bottom reserved above the wrapped footer


def _add_footer(fig, attribution: str, disclaimer: str) -> list:
    """Add the wrapped attribution/disclaimer footer; return the text artists.

    The F-002 regression tests draw these artists on the real 1000x700 canvas
    and assert every bbox stays inside the canvas and below the axes area.
    """
    lines = textwrap.wrap(f"{attribution} | {disclaimer}", width=FOOTER_WRAP_WIDTH)
    return [
        fig.text(
            0.01,
            # reading order top-to-bottom: the first wrapped line is the topmost
            FOOTER_FIRST_LINE_Y + (len(lines) - 1 - i) * FOOTER_LINE_SPACING,
            line,
            fontsize=FOOTER_FONTSIZE,
            color="#444444",
            ha="left",
            va="bottom",
        )
        for i, line in enumerate(lines)
    ]


def render_basemap_png(
    view: BasemapView,
    output_path: Path | str,
    dpi: int = 100,
    figsize: tuple[float, float] = (10.0, 7.0),
) -> Path:
    """Draw one cropped basemap viewport to a PNG file and return its path.

    Purely local: reads only the in-memory ``BasemapView``; no network access.
    """
    west, east, south, north = view.extent_wgs84
    projection = ccrs.PlateCarree()
    fig = plt.figure(figsize=figsize, facecolor="white")
    ax = fig.add_subplot(1, 1, 1, projection=projection)
    is_wmo = view.source_id == "wmo-agreed-basemap-wgs84"
    if is_wmo:
        ax.set_facecolor(WMO_COUNTRY_LAND_STYLE["facecolor"])
    ax.set_extent([west, east, south, north], crs=projection)

    for layer_name, layer in view.layers.items():
        geometries = list(layer.geometry)
        if not geometries:
            continue
        if layer.geom_type.isin(["Polygon", "MultiPolygon"]).all():
            if layer_name == "ocean" and is_wmo:
                style = {"facecolor": WMO_OCEAN_COLOR, "edgecolor": "none", "linewidth": 0.0}
            elif layer_name == "country_land":
                style = WMO_COUNTRY_LAND_STYLE if is_wmo else COUNTRY_LAND_STYLE
            else:
                style = LAKE_STYLE
            zorder = 0 if layer_name in ("country_land", "ocean") else 1
            ax.add_feature(ShapelyFeature(geometries, projection, **style), zorder=zorder)
            continue
        if "boundary_type" in layer.columns:
            for boundary_type, group in layer.groupby("boundary_type"):
                style = (WMO_BOUNDARY_STYLES if is_wmo else BOUNDARY_STYLES).get(
                    boundary_type, UNCLASSIFIED_LINE_STYLE
                )
                ax.add_feature(ShapelyFeature(list(group.geometry), projection, **style), zorder=2)
        else:
            ax.add_feature(
                ShapelyFeature(geometries, projection, **UNCLASSIFIED_LINE_STYLE), zorder=2
            )

    ax.set_title(f"Pure basemap viewport [{west}, {east}, {south}, {north}] (WGS84)", fontsize=11)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3, color="#cccccc", alpha=0.8)
    gl.top_labels = False
    gl.right_labels = False
    fig.subplots_adjust(bottom=FOOTER_BOTTOM_MARGIN)  # reserve room for the wrapped footer
    _add_footer(fig, view.attribution, view.disclaimer)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=dpi)
    plt.close(fig)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nib_visualization.cli",
        description="NiB Visualization CLI (MVP): pure local basemap and 2D field map rendering.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    basemap_cmd = subparsers.add_parser(
        "basemap", help="render the pure local basemap for one WGS84 viewport"
    )
    basemap_cmd.add_argument("--provider", choices=["ocha", "wmo"], default="ocha")
    basemap_cmd.add_argument(
        "--basemap",
        default=None,
        help=(
            "OCHA GeoPackage or WMO cache directory; OCHA defaults to $NIB_BASEMAP_PATH, "
            "WMO to $NIB_WMO_BASEMAP_PATH or data/cache/basemap/wmo"
        ),
    )
    basemap_cmd.add_argument(
        "--source-record",
        default=None,
        help="source record; defaults to the selected provider's record",
    )
    basemap_cmd.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        required=True,
        metavar=("WEST", "EAST", "SOUTH", "NORTH"),
        help="viewport extent in EPSG:4326, order west east south north",
    )
    basemap_cmd.add_argument(
        "--output",
        default=str(Path("output") / "basemap"),
        help="output directory for basemap.png",
    )

    map_cmd = subparsers.add_parser(
        "map", help="render one 2D scientific field map (synthetic fixture + local basemap)"
    )
    map_cmd.add_argument("--provider", choices=["ocha", "wmo"], default="ocha")
    map_cmd.add_argument(
        "--grid",
        choices=["latlon", "projected"],
        required=True,
        help="synthetic fixture grid to render",
    )
    map_cmd.add_argument(
        "--lead-index",
        type=int,
        default=0,
        help="lead frame to render (default: 0)",
    )
    map_cmd.add_argument(
        "--basemap",
        default=None,
        help=(
            "OCHA GeoPackage or WMO cache directory; OCHA defaults to $NIB_BASEMAP_PATH, "
            "WMO to $NIB_WMO_BASEMAP_PATH or data/cache/basemap/wmo"
        ),
    )
    map_cmd.add_argument(
        "--source-record",
        default=None,
        help="source record; defaults to the selected provider's record",
    )
    map_cmd.add_argument(
        "--output",
        required=True,
        help="full output PNG path, e.g. output/map/latlon.png",
    )

    animate_cmd = subparsers.add_parser(
        "animate",
        help="animate every existing lead of the synthetic fixture into a GIF",
    )
    animate_cmd.add_argument("--provider", choices=["ocha", "wmo"], default="ocha")
    animate_cmd.add_argument(
        "--grid",
        choices=["latlon", "projected"],
        required=True,
        help="synthetic fixture grid to animate",
    )
    animate_cmd.add_argument(
        "--basemap",
        default=None,
        help=(
            "OCHA GeoPackage or WMO cache directory; OCHA defaults to $NIB_BASEMAP_PATH, "
            "WMO to $NIB_WMO_BASEMAP_PATH or data/cache/basemap/wmo"
        ),
    )
    animate_cmd.add_argument(
        "--source-record",
        default=None,
        help="source record; defaults to the selected provider's record",
    )
    animate_cmd.add_argument(
        "--fps",
        type=float,
        default=2.0,
        help="GIF playback frames per second; playback speed only (default: 2)",
    )
    animate_cmd.add_argument(
        "--output",
        required=True,
        help="full output GIF path, e.g. output/animation/wmo/animation.gif",
    )

    demo_cmd = subparsers.add_parser(
        "demo",
        help="one-command Core chain: map.png + animation.gif + manifest.json (VIS-006)",
    )
    demo_cmd.add_argument("--provider", choices=["ocha", "wmo"], default="ocha")
    demo_cmd.add_argument(
        "--grid",
        choices=["latlon", "projected"],
        required=True,
        help="synthetic fixture grid to render",
    )
    demo_cmd.add_argument(
        "--basemap",
        default=None,
        help=(
            "OCHA GeoPackage or WMO cache directory; OCHA defaults to $NIB_BASEMAP_PATH, "
            "WMO to $NIB_WMO_BASEMAP_PATH or data/cache/basemap/wmo"
        ),
    )
    demo_cmd.add_argument(
        "--source-record",
        default=None,
        help="source record; defaults to the selected provider's record",
    )
    demo_cmd.add_argument(
        "--fps",
        type=float,
        default=2.0,
        help="GIF playback frames per second; playback speed only (default: 2)",
    )
    demo_cmd.add_argument(
        "--n-leads",
        type=int,
        default=DEFAULT_N_LEADS,
        help=f"synthetic fixture lead count (default: {DEFAULT_N_LEADS})",
    )
    demo_cmd.add_argument(
        "--output",
        required=True,
        help="output directory receiving map.png, animation.gif and manifest.json",
    )

    from nib_visualization.cma_demo import (
        DEFAULT_COLOR_FILE,
        DEFAULT_CROP_RECORD,
        DEFAULT_INPUT,
        DEFAULT_TERRAIN,
        DEFAULT_TERRAIN_RECORD,
    )

    cma_cmd = subparsers.add_parser(
        "cma-mock", help="all CMA observation times + ETOPO + WMO through the shared Core"
    )
    cma_cmd.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    cma_cmd.add_argument("--output", type=Path, default=Path("output/core-mvp/cma-mock-terrain"))
    cma_cmd.add_argument(
        "--basemap", type=Path, default=Path(os.environ.get("NIB_WMO_BASEMAP_PATH") or WMO_CACHE)
    )
    cma_cmd.add_argument("--source-record", type=Path, default=WMO_SOURCE_RECORD)
    cma_cmd.add_argument("--acquisition-record", type=Path, default=WMO_ACQUISITION_RECORD)
    cma_cmd.add_argument("--terrain", type=Path, default=DEFAULT_TERRAIN)
    cma_cmd.add_argument("--terrain-record", type=Path, default=DEFAULT_TERRAIN_RECORD)
    cma_cmd.add_argument("--crop-record", type=Path, default=DEFAULT_CROP_RECORD)
    cma_cmd.add_argument("--color-file", type=Path, default=DEFAULT_COLOR_FILE)
    cma_cmd.add_argument(
        "--display-width", type=int, default=1000,
        help="maximum spatial display columns; never changes the number of time frames",
    )
    cma_cmd.add_argument("--fps", type=float, default=5, help="playback speed only (default: 5)")

    gallery_cmd = subparsers.add_parser(
        "gallery", help="create one offline HTML gallery from an existing run manifest"
    )
    gallery_cmd.add_argument("--manifest", required=True, help="existing run manifest.json")
    gallery_cmd.add_argument(
        "--output", default=None, help="HTML path (default: index.html beside manifest)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "cma-mock":
        from nib_visualization.cma_demo import run_cma_mock

        command = [".venv/bin/python", "-m", "nib_visualization.cli", "cma-mock"]
        for flag in (
            "input", "output", "basemap", "source_record", "acquisition_record", "terrain",
            "terrain_record", "crop_record", "color_file", "display_width", "fps",
        ):
            command.extend(["--" + flag.replace("_", "-"), str(getattr(args, flag))])
        try:
            manifest = run_cma_mock(
                input_dir=args.input, output_dir=args.output, basemap_path=args.basemap,
                source_record=args.source_record, acquisition_record=args.acquisition_record,
                terrain_path=args.terrain, terrain_record=args.terrain_record,
                crop_record=args.crop_record, color_file=args.color_file,
                display_width=args.display_width, fps=args.fps, command=command,
            )
        except (BasemapError, FieldValidationError, ValueError, OSError, KeyError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        print(f"CMA mock Core outputs: {args.output} ({manifest['render']['frame_count']} frames)")
        return 0

    if args.command == "gallery":
        try:
            gallery_path = write_gallery(args.manifest, args.output)
        except (ValueError, OSError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        print(f"gallery HTML written: {gallery_path}")
        return 0

    def resolve_provider_paths() -> tuple[Path, Path]:
        """Provider/cache/source selection per main plan §3.3.

        ``--basemap`` wins, then the provider's environment variable, then the
        WMO default cache; ``--source-record`` wins, else the provider's
        default record. OCHA has no cache default: a missing explicit path is
        a usage error, not a guess.
        """
        if args.provider == "wmo":
            basemap_path = Path(args.basemap or os.environ.get("NIB_WMO_BASEMAP_PATH") or WMO_CACHE)
            source_record = Path(args.source_record) if args.source_record else WMO_SOURCE_RECORD
        else:
            basemap_arg = args.basemap or os.environ.get("NIB_BASEMAP_PATH")
            if not basemap_arg:
                parser.error(
                    f"{args.command}: --basemap is required when $NIB_BASEMAP_PATH is not set"
                )
            basemap_path = Path(basemap_arg)
            source_record = (
                Path(args.source_record) if args.source_record else DEFAULT_SOURCE_RECORD
            )
        return basemap_path, source_record

    def open_selected_basemap():
        basemap_path, source_record = resolve_provider_paths()
        if args.provider == "wmo":
            return load_wmo_basemap(basemap_path, source_record)
        return load_basemap(basemap_path, source_record)

    if args.command == "basemap":
        try:
            basemap = open_selected_basemap()
            view = crop_basemap(basemap, args.bbox)
            output_path = render_basemap_png(view, Path(args.output) / "basemap.png")
        except BasemapError as err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        print(f"basemap PNG written: {output_path}")
        return 0

    if args.command == "map":
        try:
            field = make_fixture(args.grid)
            basemap = open_selected_basemap()
            scene = prepare_scene(field, RenderConfig(), basemap)
            render_frame(scene, field, args.lead_index)
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            scene.figure.savefig(output_path, dpi=scene.config.dpi)
            plt.close(scene.figure)
        except (BasemapError, FieldValidationError, ValueError, IndexError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        print(f"map PNG written: {output_path}")
        return 0

    if args.command == "animate":
        try:
            field = make_fixture(args.grid)
            basemap = open_selected_basemap()
            scene = prepare_scene(field, RenderConfig(), basemap)
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            animation_path = write_animation(scene, field, output_path, fps=args.fps)
            plt.close(scene.figure)
        except (BasemapError, FieldValidationError, ValueError, IndexError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        print(f"animation GIF written: {animation_path}")
        return 0

    if args.command == "demo":
        try:
            basemap_path, source_record_path = resolve_provider_paths()
            acquisition_record = (
                WMO_ACQUISITION_RECORD if args.provider == "wmo" else DEFAULT_ACQUISITION_RECORD
            )
            field = make_fixture(args.grid, n_leads=args.n_leads)
            basemap = open_selected_basemap()
            # reject an explicitly mismatched source record before any pixel is
            # drawn, so a failed run cannot leave wrongly-attributed artifacts
            verify_source_identity(args.provider, basemap, source_record_path)
            scene = prepare_scene(field, RenderConfig(), basemap)
            output_dir = Path(args.output)
            output_dir.mkdir(parents=True, exist_ok=True)
            map_path = output_dir / "map.png"
            animation_path = output_dir / "animation.gif"
            manifest_path = output_dir / "manifest.json"
            render_frame(scene, field, 0)
            scene.figure.savefig(map_path, dpi=scene.config.dpi)
            write_animation(scene, field, animation_path, fps=args.fps)
            plt.close(scene.figure)
            checks = inspect_outputs(map_path, animation_path, field.values.sizes["lead_time"])
            command = [
                ".venv/bin/python",
                "-m",
                "nib_visualization.cli",
                "demo",
                "--provider",
                args.provider,
                "--grid",
                args.grid,
                "--basemap",
                str(basemap_path),
            ]
            if args.source_record:
                command += ["--source-record", str(args.source_record)]
            command += [
                "--fps",
                str(args.fps),
                "--n-leads",
                str(args.n_leads),
                "--output",
                str(args.output),
            ]
            write_manifest(
                field,
                scene,
                provider=args.provider,
                basemap=basemap,
                source_record_path=source_record_path,
                acquisition_record_path=acquisition_record,
                outputs={
                    "map": map_path,
                    "animation": animation_path,
                    "manifest": manifest_path,
                },
                command=command,
                fps=args.fps,
                checks=checks,
            )
        except (BasemapError, FieldValidationError, ValueError, IndexError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        print(f"demo map PNG written: {map_path}")
        print(f"demo animation GIF written: {animation_path}")
        print(f"demo manifest written: {manifest_path}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
