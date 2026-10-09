"""Headless 2D spatial field map renderer (VIS-004).

Renders one scientific scalar field of a validated Forecast onto the local
basemap: cell edges are derived from the centres (no half-cell shift), the
geographic footprint gets a small buffer, the basemap is cropped once per
scene via :func:`~nib_visualization.basemap.crop_basemap`, and the color
normalization is fixed from all finite values of the whole lead sequence.
:func:`prepare_scene` performs every expensive step once;
:func:`render_frame` only swaps the mesh array and the time label, so VIS-005
can animate on the same fixed figure/mesh/norm/extent.

Boundaries (VIS-004 task card + main plan ``api``/``engineering`` sections):

- Matplotlib Agg only; no ``show()``, no display, no network. All geometry
  comes from the passed-in local :class:`~nib_visualization.basemap.LocalBasemap`;
  ``ax.coastlines``/NaturalEarthFeature helpers that could trigger Cartopy
  downloads are never used.
- Basemap file I/O and source-record parsing stay in ``basemap.py``/the CLI;
  this module receives an opened ``LocalBasemap``.
- The renderer does display preparation only: no regridding, no interpolation
  of scientific values, no mutation of the field, its coordinates or its
  metadata. Longitude seam handling keeps a locally continuous display copy
  (columns and values stay synchronized); complex/singular projections are
  rejected explicitly (geographic + UTM grids are the supported MVP set).
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless rendering, no display required

import cartopy.crs as ccrs  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from cartopy.feature import ShapelyFeature  # noqa: E402
from matplotlib.colors import Colormap, LinearSegmentedColormap, Normalize  # noqa: E402
from pyproj import CRS, Transformer  # noqa: E402

from nib_visualization.basemap import (  # noqa: E402
    WGS84,
    BasemapError,
    BasemapView,
    CompositeBasemap,
    LocalBasemap,
    _validate_extent,
    crop_basemap,
)
from nib_visualization.field import (  # noqa: E402
    VisualizationField,
    frame_at,
    validate_field,
)

# Missing values render as a fully transparent RGBA; valid zeros stay zeros.
_MISSING_RGBA = (0.0, 0.0, 0.0, 0.0)

# Constant-field padding: small, deterministic, symmetric around the value.
_CONSTANT_RELATIVE_PAD = 1e-6
_CONSTANT_ABSOLUTE_PAD = 1e-6

# Single-panel layer order: ocean/axes background < neutral country land <
# scientific field < optional lakes < boundary lines < labels/footer text.
_LAND_ZORDER = 0
_FIELD_ZORDER = 1
_LAKE_ZORDER = 1.5
_LINE_ZORDER = 2

# Layer styles mirror the VIS-001-approved semantics in cli.py (neutral land,
# distinguishable lakes, lines with facecolor="none" above the field). They are
# deliberately duplicated as private constants instead of importing the CLI or
# refactoring the already-reviewed VIS-001 path.
_COUNTRY_LAND_STYLE = {"facecolor": "#f1efe8", "edgecolor": "none", "linewidth": 0.0}
_WMO_COUNTRY_LAND_STYLE = {"facecolor": "#E6E7E8", "edgecolor": "none", "linewidth": 0.0}
_WMO_OCEAN_COLOR = "#E1E8F5"
_WMO_BOUNDARY_STYLES = {
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
_LAKE_STYLE = {"facecolor": "#cfe3f2", "edgecolor": "#2c5f8a", "linewidth": 0.4}
_BOUNDARY_STYLES: dict[str, dict] = {
    "coastline": {"facecolor": "none", "edgecolor": "#2c5f8a", "linewidth": 0.9, "linestyle": "-"},
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
    "other": {"facecolor": "none", "edgecolor": "#b0b0b0", "linewidth": 0.5, "linestyle": ":"},
}
_UNCLASSIFIED_LINE_STYLE = {
    "facecolor": "none",
    "edgecolor": "#b0b0b0",
    "linewidth": 0.5,
    "linestyle": ":",
}

# Footer layout, mirroring the VIS-001 F-002 fix: deterministic wrapping with
# the axes region raised so the footer cannot be clipped or overlap the map.
_FOOTER_FONTSIZE = 6
_FOOTER_WRAP_WIDTH = 210
_FOOTER_FIRST_LINE_Y = 0.008
_FOOTER_LINE_SPACING = 0.019
_FOOTER_BOTTOM_MARGIN = 0.14


@dataclass
class RenderConfig:
    """Single source of the 2D map defaults.

    ``buffer_fraction`` extends the viewport (and therefore the basemap crop)
    by that fraction of the field footprint's longitude/latitude span on every
    side; scientific values are never cropped or altered. ``vmin``/``vmax``
    default to the finite extremes of the whole lead sequence; giving one end
    derives the other from the data, and the resolved limits must satisfy
    ``vmin < vmax``. Missing values are always fully transparent and valid
    zeros always stay real numeric values.

    ``viewport_wgs84`` optionally fixes the display extent (west, east, south,
    north), overriding the footprint buffer without changing the field grid.
    This permits regional data on a global map. Omit it for automatic framing.
    """

    cmap: str | Colormap = "viridis"
    vmin: float | None = None
    vmax: float | None = None
    buffer_fraction: float = 0.05
    figsize: tuple[float, float] = (10.0, 7.0)
    dpi: int = 100
    field_alpha: float = 1.0
    display_min: float | None = None
    notice: str | None = None
    colorbar_extend: str = "neither"
    viewport_wgs84: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        if self.viewport_wgs84 is not None:
            try:
                self.viewport_wgs84 = _validate_extent(self.viewport_wgs84)
            except BasemapError as err:
                raise ValueError(f"invalid viewport_wgs84: {err}") from err
        if self.buffer_fraction < 0:
            raise ValueError(f"buffer_fraction must be non-negative, got {self.buffer_fraction!r}")
        if self.dpi <= 0:
            raise ValueError(f"dpi must be positive, got {self.dpi!r}")
        if self.figsize[0] <= 0 or self.figsize[1] <= 0:
            raise ValueError(f"figsize entries must be positive, got {self.figsize!r}")
        if self.display_min is not None and not np.isfinite(self.display_min):
            raise ValueError("display_min must be finite when supplied")
        if self.colorbar_extend not in ("neither", "min", "max", "both"):
            raise ValueError("invalid colorbar_extend")
        if self.vmin is not None and self.vmax is not None and self.vmin >= self.vmax:
            raise ValueError(
                f"explicit color limits must satisfy vmin < vmax, "
                f"got vmin={self.vmin!r}, vmax={self.vmax!r}"
            )


@dataclass
class PreparedScene:
    """One prepared 2D map scene, reusable across lead frames.

    ``x_edges``/``y_edges`` are the native cell edges handed to the mesh. For
    geographic inputs the longitudes are kept in the locally continuous
    display expression chosen at preparation time (a wrapped copy, or the
    source's own continuous 0-360 expression when the domain crosses the
    conventional ±180 line); the mesh columns always stay synchronized with
    the source values. ``field_x_centres``/``field_y_centres`` keep the
    source's native centres for per-frame grid compatibility checks.

    ``field_footprint`` (unbuffered) and ``viewport`` (buffered) are
    ``(west, east, south, north)`` in that same display-longitude frame;
    ``crop_extents_wgs84`` are the one or two legal, non-crossing WGS84
    extents actually queried through :func:`crop_basemap`.
    """

    figure: Any
    axes: Any
    mesh: Any
    colorbar: Any
    title_artist: Any
    time_artist: Any
    display_crs: ccrs.PlateCarree
    data_cartopy_crs: ccrs.Projection
    data_crs: CRS
    x_edges: np.ndarray
    y_edges: np.ndarray
    field_x_centres: np.ndarray
    field_y_centres: np.ndarray
    field_footprint: tuple[float, float, float, float]
    viewport: tuple[float, float, float, float]
    crop_extents_wgs84: tuple[tuple[float, float, float, float], ...]
    basemap_views: tuple[BasemapView, ...]
    norm: Normalize
    cmap: Colormap
    config: RenderConfig
    central_longitude: float
    variable_name: str
    units: str
    init_time: np.datetime64
    lead_times: np.ndarray
    temporal_statistic: str
    interval_start: np.ndarray | None
    interval_end: np.ndarray | None


def _centres_to_edges(centres: np.ndarray) -> np.ndarray:
    """Cell edges for one 1D regularly spaced centre axis.

    Inner edges are adjacent-centre midpoints; both ends extrapolate the
    adjacent spacing by half a cell. Ascending axes stay ascending and
    descending axes stay descending; values are never flipped or re-sorted.
    """
    c = np.asarray(centres, dtype=float)
    edges = np.empty(c.size + 1, dtype=float)
    edges[1:-1] = (c[:-1] + c[1:]) / 2.0
    edges[0] = c[0] - (c[1] - c[0]) / 2.0
    edges[-1] = c[-1] + (c[-1] - c[-2]) / 2.0
    return edges


def _wrap180(value: float) -> float:
    """Wrap one longitude into ``[-180, 180)``."""
    return float(((value + 180.0) % 360.0) - 180.0)


def _display_longitude(x_centres: np.ndarray) -> np.ndarray:
    """Locally continuous longitude display copy of a geographic x axis.

    Centres that wrap cleanly into ``[-180, 180)`` while keeping their
    monotonic direction use the wrapped copy. When wrapping would break
    monotonicity (a 0-360 style domain such as 178..182 crossing the
    conventional ±180 line), the source's own continuous expression is kept,
    so columns and values stay synchronized; the display central longitude is
    adjusted instead of reordering anything.
    """
    x = np.asarray(x_centres, dtype=float)
    wrapped = ((x + 180.0) % 360.0) - 180.0
    if np.all(np.diff(x) > 0):
        keeps_direction = np.all(np.diff(wrapped) > 0)
    else:
        keeps_direction = np.all(np.diff(wrapped) < 0)
    return wrapped if keeps_direction else x.copy()


def _central_longitude_of(footprint: tuple[float, float, float, float]) -> float:
    """Display PlateCarree central longitude from the footprint's midpoint.

    Canonicalized to ``(-180, 180]`` so an antimeridian-centred domain (the
    seam case) reports ``180`` rather than ``-180``; both describe the same
    projection, the positive value matches the input's local expression.
    """
    mid = (footprint[0] + footprint[1]) / 2.0
    wrapped = _wrap180(mid)
    return 180.0 if wrapped == -180.0 else wrapped


def _cartopy_data_crs(crs: CRS) -> ccrs.Projection:
    """Cartopy transform matching the field's CRS (geographic or UTM only)."""
    if crs.is_geographic:
        return ccrs.PlateCarree()
    zone = crs.utm_zone
    if zone is not None:
        return ccrs.UTM(int(zone[:-1]), southern_hemisphere=zone.endswith("S"))
    raise ValueError(
        f"projected CRS {crs.to_string()!r} is not supported by the MVP renderer; "
        "geographic and UTM grids are supported, complex/singular projections are rejected"
    )


def _grid_perimeter(x_edges: np.ndarray, y_edges: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """All vertices along the four outer sides of the cell-edge grid."""
    top = np.column_stack([x_edges, np.full_like(x_edges, y_edges[0])])
    bottom = np.column_stack([x_edges, np.full_like(x_edges, y_edges[-1])])
    left = np.column_stack([np.full_like(y_edges, x_edges[0]), y_edges])
    right = np.column_stack([np.full_like(y_edges, x_edges[-1]), y_edges])
    ring = np.concatenate([top, bottom, left, right])
    return ring[:, 0], ring[:, 1]


def _geographic_footprint(
    x_edges: np.ndarray, y_edges: np.ndarray, data_crs: CRS
) -> tuple[float, float, float, float]:
    """Unbuffered geographic footprint (west, east, south, north) of the grid.

    Geographic grids read the edge bounds directly; projected grids transform
    the full edge-grid perimeter (all four sides, not two diagonal corners) to
    WGS84 with ``always_xy=True`` and fail explicitly on non-finite results.
    """
    if data_crs.is_geographic:
        west, east = float(min(x_edges[0], x_edges[-1])), float(max(x_edges[0], x_edges[-1]))
        south, north = float(min(y_edges[0], y_edges[-1])), float(max(y_edges[0], y_edges[-1]))
        return west, east, south, north
    to_wgs84 = Transformer.from_crs(data_crs, WGS84, always_xy=True)
    perimeter_x, perimeter_y = _grid_perimeter(x_edges, y_edges)
    lon, lat = to_wgs84.transform(perimeter_x, perimeter_y)
    if not (np.isfinite(lon).all() and np.isfinite(lat).all()):
        raise ValueError(
            "cell-edge footprint transform to WGS84 produced non-finite coordinates; "
            "the field grid lies outside the valid domain of its CRS"
        )
    return float(lon.min()), float(lon.max()), float(lat.min()), float(lat.max())


def _buffered_viewport(
    footprint: tuple[float, float, float, float], buffer_fraction: float
) -> tuple[float, float, float, float]:
    """Viewport adding ``buffer_fraction`` of each span on every side.

    Only the viewport (and thus the basemap crop) grows; the field footprint
    and its values are untouched. Latitudes are clamped to ``[-90, 90]``.
    """
    west, east, south, north = footprint
    pad_lon = buffer_fraction * (east - west)
    pad_lat = buffer_fraction * (north - south)
    south_b = max(south - pad_lat, -90.0)
    north_b = min(north + pad_lat, 90.0)
    if not south_b < north_b:
        raise ValueError(
            f"buffered viewport is degenerate after latitude clamping: "
            f"south={south_b!r}, north={north_b!r}"
        )
    return west - pad_lon, east + pad_lon, south_b, north_b


def _clip180(value: float) -> float:
    return max(-180.0, min(180.0, value))


def _crop_extents(
    viewport: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float, float], ...]:
    """Split the display-frame viewport into legal, non-crossing WGS84 extents.

    Any viewport segment that crosses a meridian congruent to 180 (mod 360)
    is split there: one extent ends at 180 and the next starts at -180, both
    satisfying ``-180 <= west < east <= 180`` so the VIS-001 extent contract
    stays intact. Local viewports come back as a single extent.
    """
    west, east, south, north = viewport
    first = math.floor((west - 180.0) / 360.0)
    last = math.floor((east - 180.0) / 360.0)
    if first == last:
        return ((_clip180(_wrap180(west)), _clip180(_wrap180(east)), south, north),)

    cuts = [180.0 + 360.0 * step for step in range(first + 1, last + 1)]
    bounds = [west, *cuts, east]
    extents = []
    for a, b in zip(bounds[:-1], bounds[1:], strict=True):
        start = _wrap180(a)
        end = start + (b - a)
        if end <= start:
            continue  # degenerate split point exactly on an edge
        extents.append((_clip180(start), _clip180(end), south, north))
    return tuple(extents)


def _resolve_limits(values: np.ndarray, config: RenderConfig) -> tuple[float, float]:
    """Fixed color limits from all finite values of the whole sequence.

    Explicit config limits override their end; a missing end is derived from
    the data. Constant fields get a small deterministic symmetric padding so
    the Normalize and colorbar never become zero-width.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("no finite values in the field sequence; cannot resolve color limits")
    vmin = float(config.vmin) if config.vmin is not None else float(finite.min())
    vmax = float(config.vmax) if config.vmax is not None else float(finite.max())
    if vmin == vmax:
        pad = max(abs(vmin) * _CONSTANT_RELATIVE_PAD, _CONSTANT_ABSOLUTE_PAD)
        vmin, vmax = vmin - pad, vmax + pad
    if not vmin < vmax:
        raise ValueError(
            f"resolved color limits must satisfy vmin < vmax, got vmin={vmin!r}, vmax={vmax!r}"
        )
    return vmin, vmax


def _resolve_colormap(name: str | Colormap) -> Colormap:
    """Resolved colormap copy with fully transparent missing color."""
    try:
        base = name if isinstance(name, Colormap) else matplotlib.colormaps[name]
    except KeyError as err:
        raise ValueError(f"unknown colormap {name!r}") from err
    return base.with_extremes(bad=_MISSING_RGBA)


def _display_values(values: np.ndarray, config: RenderConfig) -> np.ma.MaskedArray:
    """Apply a display-only threshold without changing the field or valid zeros."""
    data = np.ma.masked_invalid(values)
    return data if config.display_min is None else np.ma.masked_less(data, config.display_min)


def _format_time(value: np.datetime64) -> str:
    return str(np.datetime_as_string(np.datetime64(value, "s"), unit="s"))


def _format_timedelta(delta: np.timedelta64) -> str:
    seconds = int(delta / np.timedelta64(1, "s"))
    if seconds % 86400 == 0:
        return f"{seconds // 86400} d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def _time_text(scene: PreparedScene, frame, lead_index: int) -> str:
    """Complete, readable time semantics for one rendered lead."""
    parts = [
        scene.temporal_statistic,
        f"init {_format_time(scene.init_time)} UTC",
        f"lead +{_format_timedelta(np.timedelta64(frame.coords['lead_time'].values))}",
        f"valid {_format_time(np.datetime64(frame.coords['valid_time'].values))} UTC",
    ]
    if scene.temporal_statistic in ("accumulation", "mean") and scene.interval_start is not None:
        parts.append(
            f"interval {_format_time(scene.interval_start[lead_index])} → "
            f"{_format_time(scene.interval_end[lead_index])} UTC"
        )
    return " | ".join(parts)


def _draw_basemap_layers(axes, view: BasemapView) -> None:
    """Draw one cropped basemap view with VIS-001-approved layer semantics."""
    is_wmo = view.source_id == "wmo-agreed-basemap-wgs84"
    if is_wmo:
        axes.set_facecolor(_WMO_COUNTRY_LAND_STYLE["facecolor"])
    for layer_name, layer in view.layers.items():
        geometries = list(layer.geometry)
        if not geometries:
            continue
        if layer.geom_type.isin(["Polygon", "MultiPolygon"]).all():
            is_land = layer_name == "country_land"
            if layer_name == "ocean" and is_wmo:
                style = {"facecolor": _WMO_OCEAN_COLOR, "edgecolor": "none", "linewidth": 0.0}
            elif is_land:
                style = _WMO_COUNTRY_LAND_STYLE if is_wmo else _COUNTRY_LAND_STYLE
            else:
                style = _LAKE_STYLE
            zorder = _LAND_ZORDER if is_land or layer_name == "ocean" else _LAKE_ZORDER
            axes.add_feature(ShapelyFeature(geometries, ccrs.PlateCarree(), **style), zorder=zorder)
            continue
        if "boundary_type" in layer.columns:
            for boundary_type, group in layer.groupby("boundary_type"):
                style = (_WMO_BOUNDARY_STYLES if is_wmo else _BOUNDARY_STYLES).get(
                    boundary_type, _UNCLASSIFIED_LINE_STYLE
                )
                axes.add_feature(
                    ShapelyFeature(list(group.geometry), ccrs.PlateCarree(), **style),
                    zorder=_LINE_ZORDER,
                )
        else:
            axes.add_feature(
                ShapelyFeature(geometries, ccrs.PlateCarree(), **_UNCLASSIFIED_LINE_STYLE),
                zorder=_LINE_ZORDER,
            )
    if view.raster is not None:
        raster = view.raster
        norm = Normalize(raster.color_stops[0][0], raster.color_stops[-1][0])
        cmap = LinearSegmentedColormap.from_list(
            "basemap_relief", [(float(norm(value)), color) for value, color in raster.color_stops],
            N=4096,
        )
        # The Core display is PlateCarree with a shifted longitude origin.
        # Translate the extent exactly; Cartopy's generic image warp would
        # resample this already-geographic backdrop and require scipy/pykdtree.
        west, east, south, north = raster.extent_wgs84
        # Use the public CRS conversion: Cartopy may encode the origin as
        # a prime meridian (pm), rather than a proj4 lon_0 parameter.
        display_west, _ = axes.projection.transform_point(west, south, ccrs.PlateCarree())
        axes.imshow(
            raster.values,
            extent=(display_west, display_west + east - west, south, north), origin="lower",
            transform=axes.projection, interpolation="nearest", cmap=cmap, norm=norm,
            alpha=raster.alpha, zorder=0.5,
        )


def _add_footer_texts(figure, attribution: str, disclaimer: str) -> list:
    """Add the wrapped attribution/disclaimer footer; return the text artists."""
    lines = textwrap.wrap(f"{attribution} | {disclaimer}", width=_FOOTER_WRAP_WIDTH)
    return [
        figure.text(
            0.01,
            # reading order top-to-bottom: the first wrapped line is the topmost
            _FOOTER_FIRST_LINE_Y + (len(lines) - 1 - i) * _FOOTER_LINE_SPACING,
            line,
            fontsize=_FOOTER_FONTSIZE,
            color="#444444",
            ha="left",
            va="bottom",
        )
        for i, line in enumerate(lines)
    ]


def prepare_scene(
    field: VisualizationField, config: RenderConfig, basemap: LocalBasemap | CompositeBasemap
) -> PreparedScene:
    """Prepare the reusable 2D map scene for ``field`` on the local basemap.

    Validates the field, derives native cell edges, resolves the display
    longitude expression and central longitude, computes the buffered viewport
    and its one or two legal basemap crops, fixes the color normalization from
    all finite values of the whole lead sequence, then builds the figure,
    basemap artists, first-frame mesh, colorbar, labels and footer. The
    returned scene is updated per lead by :func:`render_frame`.
    """
    validated = validate_field(field)
    data_crs: CRS = validated.crs
    x_centres = np.asarray(validated.values.coords["x"].values, dtype=float)
    y_centres = np.asarray(validated.values.coords["y"].values, dtype=float)
    data_cartopy = _cartopy_data_crs(data_crs)

    display_x = _display_longitude(x_centres) if data_crs.is_geographic else x_centres.copy()
    x_edges = _centres_to_edges(display_x)
    y_edges = _centres_to_edges(y_centres)

    footprint = _geographic_footprint(x_edges, y_edges, data_crs)
    viewport = config.viewport_wgs84 or _buffered_viewport(footprint, config.buffer_fraction)
    crop_extents = _crop_extents(viewport)
    views = tuple(crop_basemap(basemap, extent) for extent in crop_extents)

    central_longitude = _central_longitude_of(
        viewport if config.viewport_wgs84 is not None else footprint
    )
    display_crs = ccrs.PlateCarree(central_longitude=central_longitude)

    vmin, vmax = _resolve_limits(validated.values.values, config)
    norm = Normalize(vmin=vmin, vmax=vmax)
    cmap = _resolve_colormap(config.cmap)

    figure = plt.figure(figsize=config.figsize, dpi=config.dpi, facecolor="white")
    axes = figure.add_subplot(1, 1, 1, projection=display_crs)
    display_extent = (
        viewport[0] - central_longitude,
        viewport[1] - central_longitude,
        viewport[2],
        viewport[3],
    )
    axes.set_extent(display_extent, crs=display_crs)

    for view in views:
        _draw_basemap_layers(axes, view)

    first_frame = frame_at(validated, 0)
    mesh = axes.pcolormesh(
        x_edges,
        y_edges,
        _display_values(first_frame.values, config),
        transform=data_cartopy,
        cmap=cmap,
        norm=norm,
        # Opaque finite cells avoid dark compositing seams between projected
        # pcolormesh quads; the colormap's bad color keeps NaNs transparent.
        alpha=config.field_alpha,
        shading="flat",
        edgecolors="none",
        zorder=_FIELD_ZORDER,
    )

    label = f"{validated.name} ({validated.units})"
    title_artist = axes.set_title(label, fontsize=11, pad=18)
    time_artist = axes.text(
        0.5, 1.015, "", transform=axes.transAxes, ha="center", va="bottom", fontsize=9
    )
    gridlines = axes.gridlines(draw_labels=True, linewidth=0.3, color="#cccccc", alpha=0.8)
    gridlines.top_labels = False
    gridlines.right_labels = False

    figure.subplots_adjust(bottom=_FOOTER_BOTTOM_MARGIN)  # reserve room for the footer
    colorbar = figure.colorbar(
        mesh, ax=axes, fraction=0.046, pad=0.02, extend=config.colorbar_extend
    )
    colorbar.set_label(label, fontsize=9)
    _add_footer_texts(figure, views[0].attribution, views[0].disclaimer)
    if config.notice:
        figure.text(0.01, 0.12, config.notice, fontsize=9, weight="bold", color="#8a5a16")

    scene = PreparedScene(
        figure=figure,
        axes=axes,
        mesh=mesh,
        colorbar=colorbar,
        title_artist=title_artist,
        time_artist=time_artist,
        display_crs=display_crs,
        data_cartopy_crs=data_cartopy,
        data_crs=data_crs,
        x_edges=x_edges,
        y_edges=y_edges,
        field_x_centres=x_centres,
        field_y_centres=y_centres,
        field_footprint=footprint,
        viewport=viewport,
        crop_extents_wgs84=crop_extents,
        basemap_views=views,
        norm=norm,
        cmap=cmap,
        config=config,
        central_longitude=central_longitude,
        variable_name=validated.name,
        units=validated.units,
        init_time=validated.init_time,
        lead_times=np.array(validated.values.coords["lead_time"].values, copy=True),
        temporal_statistic=validated.temporal_statistic,
        interval_start=(
            None
            if validated.interval_start is None
            else np.array(validated.interval_start, copy=True)
        ),
        interval_end=(
            None if validated.interval_end is None else np.array(validated.interval_end, copy=True)
        ),
    )
    scene.time_artist.set_text(_time_text(scene, first_frame, 0))
    return scene


def render_frame(scene: PreparedScene, field: VisualizationField, lead_index: int):
    """Render one lead of ``field`` into the prepared ``scene``.

    Uses :func:`~nib_visualization.field.frame_at` (full validation plus lead
    index bounds). The field's grid, CRS, display semantics and color limits
    must match the scene's, but object identity is not required. Only the mesh
    array and the time label change:
    the figure, axes, mesh, colorbar, norm, extent, projection and basemap
    artists stay exactly as prepared, and the passed field is never modified.
    Returns the scene's own figure.
    """
    frame = frame_at(field, lead_index)
    same_x = np.array_equal(
        np.asarray(frame.coords["x"].values, dtype=float), scene.field_x_centres
    )
    same_y = np.array_equal(
        np.asarray(frame.coords["y"].values, dtype=float), scene.field_y_centres
    )
    if not same_x or not same_y or not CRS.from_user_input(field.crs).equals(scene.data_crs):
        raise ValueError(
            "field grid or CRS does not match the prepared scene; prepare a new scene "
            "for a different grid instead of rendering it onto fixed edges and extent"
        )
    same_semantics = (
        field.name == scene.variable_name
        and field.units == scene.units
        and field.init_time == scene.init_time
        and field.temporal_statistic == scene.temporal_statistic
        and np.array_equal(field.values.coords["lead_time"].values, scene.lead_times)
        and np.array_equal(field.interval_start, scene.interval_start)
        and np.array_equal(field.interval_end, scene.interval_end)
    )
    same_limits = _resolve_limits(field.values.values, scene.config) == (
        scene.norm.vmin,
        scene.norm.vmax,
    )
    if not same_semantics or not same_limits:
        raise ValueError(
            "field semantics or color limits do not match the prepared scene; "
            "prepare a new scene"
        )
    scene.mesh.set_array(_display_values(np.asarray(frame.values), scene.config))
    scene.time_artist.set_text(_time_text(scene, frame, lead_index))
    return scene.figure
