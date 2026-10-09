"""Local global basemap: GeoPackage loading and WGS84 viewport cropping.

VIS-001 scope (see doc_log/plans/visualization-mvp-tasks/2026-09-21-vis-001-global-basemap.md):
read the locally cached global vector master produced by ``scripts/acquire_basemap.py``
and crop it to a WGS84 extent. This module performs no network access; the
GeoPackage is the default VIS-001 data source. The optional WMO vector-tile
adapter shares BasemapView through a dispatch in ``crop_basemap``.

Extent order is fixed as ``(west, east, south, north)`` in EPSG:4326.
The source record (``basemap_sources/selected-source.json``) is always passed
explicitly and drives layer selection and boundary-type mapping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import geopandas as gpd
import numpy as np
from shapely.geometry import box

BASEMAP_UNAVAILABLE = "BASEMAP_UNAVAILABLE"
INVALID_SOURCE_RECORD = "INVALID_SOURCE_RECORD"
INVALID_EXTENT = "INVALID_EXTENT"
INVALID_BASEMAP_LAYER = "INVALID_BASEMAP_LAYER"

WGS84 = "EPSG:4326"


class BasemapError(Exception):
    """Basemap access failure; ``code`` carries the machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class LocalBasemap:
    """Opened local GeoPackage master plus the verified source record context."""

    path: Path
    source_id: str
    attribution: str
    disclaimer: str
    layers: dict[str, gpd.GeoDataFrame]
    boundary_type_field: str | None
    boundary_type_labels: dict[int, str]


@dataclass
class RasterBasemapView:
    """Source-independent scalar raster, ascending rows, georeferenced in WGS84."""

    values: np.ndarray
    extent_wgs84: tuple[float, float, float, float]
    color_stops: tuple[tuple[float, str], ...]
    alpha: float


class RasterBasemapSource(Protocol):
    """An opened local raster adapter; acquisition/format details stay outside rendering."""

    attribution: str
    disclaimer: str
    metadata: dict

    def crop(self, extent_wgs84) -> RasterBasemapView: ...


@dataclass
class CompositeBasemap:
    """Combine an existing vector provider with one local raster backdrop."""

    vector: object
    raster: RasterBasemapSource


@dataclass
class BasemapView:
    """Cropped basemap layers ready to draw for one viewport."""

    layers: dict[str, gpd.GeoDataFrame]
    extent_wgs84: tuple[float, float, float, float]
    attribution: str
    disclaimer: str
    source_id: str = ""
    raster: RasterBasemapView | None = None


def load_source_record(source_record: Path | str) -> dict:
    """Read and structurally validate a selected-source.json record."""
    record_path = Path(source_record)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise BasemapError(
            INVALID_SOURCE_RECORD, f"source record not found: {record_path}"
        ) from err
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise BasemapError(
            INVALID_SOURCE_RECORD, f"source record unreadable at {record_path}: {err}"
        ) from err
    required = ("source_id", "attribution", "layers")
    missing = [key for key in required if key not in record]
    if missing:
        raise BasemapError(
            INVALID_SOURCE_RECORD, f"source record missing keys {missing} at {record_path}"
        )
    return record


def load_basemap(path: Path | str, source_record: Path | str) -> LocalBasemap:
    """Open the local global GeoPackage described by ``source_record``.

    Raises ``BasemapError(BASEMAP_UNAVAILABLE)`` when the cache file is absent.
    """
    gpkg_path = Path(path)
    record = load_source_record(source_record)
    if not gpkg_path.is_file():
        raise BasemapError(BASEMAP_UNAVAILABLE, f"local basemap cache not found: {gpkg_path}")

    layers: dict[str, gpd.GeoDataFrame] = {}
    for name, spec in record["layers"].items():
        gpkg_layer = spec.get("gpkg_layer", name)
        try:
            layers[name] = gpd.read_file(gpkg_path, layer=gpkg_layer)
        except Exception as err:  # fiona/GDAL layer errors vary by driver version
            raise BasemapError(
                BASEMAP_UNAVAILABLE,
                f"layer '{gpkg_layer}' unreadable in {gpkg_path}: {err}",
            ) from err

    boundary_spec = record["layers"].get("boundary_lines", {})
    type_field = boundary_spec.get("type_field")
    type_labels = {
        int(code): label for code, label in boundary_spec.get("type_mapping", {}).items()
    }

    return LocalBasemap(
        path=gpkg_path,
        source_id=record["source_id"],
        attribution=record["attribution"],
        disclaimer=record["disclaimer"],
        layers=layers,
        boundary_type_field=type_field,
        boundary_type_labels=type_labels,
    )


def _validate_extent(extent_wgs84) -> tuple[float, float, float, float]:
    try:
        west, east, south, north = (float(value) for value in extent_wgs84)
    except (TypeError, ValueError) as err:
        raise BasemapError(
            INVALID_EXTENT,
            f"extent must be four numbers (west, east, south, north): {extent_wgs84!r}",
        ) from err
    if not (-180 <= west < east <= 180) or not (-90 <= south < north <= 90):
        raise BasemapError(
            INVALID_EXTENT,
            f"invalid extent west={west}, east={east}, south={south}, north={north} "
            "(need -180<=west<east<=180 and -90<=south<north<=90)",
        )
    return west, east, south, north


def crop_basemap(basemap: LocalBasemap | CompositeBasemap, extent_wgs84) -> BasemapView:
    """Crop the selected local provider to ``(west, east, south, north)``.

    Uses a spatial-index bbox prefilter followed by exact intersection, so
    features crossing the viewport edge come back clipped to the viewport.
    Line layers get a ``boundary_type`` label column derived from the source
    record's type mapping. All returned layers are EPSG:4326.
    """
    from nib_visualization.wmo_basemap import WmoBasemap, crop_wmo_basemap

    if isinstance(basemap, CompositeBasemap):
        view = crop_basemap(basemap.vector, extent_wgs84)
        return replace(
            view,
            raster=basemap.raster.crop(extent_wgs84),
            attribution=f"{view.attribution} {basemap.raster.attribution}",
            disclaimer=f"{view.disclaimer} {basemap.raster.disclaimer}",
        )
    if isinstance(basemap, WmoBasemap):
        return crop_wmo_basemap(basemap, extent_wgs84)

    west, east, south, north = _validate_extent(extent_wgs84)
    viewport = box(west, south, east, north)

    cropped: dict[str, gpd.GeoDataFrame] = {}
    for name, gdf in basemap.layers.items():
        if gdf.crs is None:
            raise BasemapError(INVALID_BASEMAP_LAYER, f"layer '{name}' has no CRS")
        layer = gdf.to_crs(WGS84) if not gdf.crs.equals(WGS84) else gdf
        if layer.empty:
            cropped[name] = layer.copy()
            continue
        candidates = layer.iloc[sorted(layer.sindex.query(viewport, predicate="intersects"))]
        hits = candidates[candidates.intersects(viewport)].copy()
        hits["geometry"] = hits.intersection(viewport)
        hits = hits[~hits.geometry.is_empty].reset_index(drop=True)
        if basemap.boundary_type_field in hits.columns and basemap.boundary_type_labels:
            hits["boundary_type"] = (
                hits[basemap.boundary_type_field].map(basemap.boundary_type_labels).astype("object")
            )
        cropped[name] = hits

    return BasemapView(
        layers=cropped,
        extent_wgs84=(west, east, south, north),
        attribution=basemap.attribution,
        disclaimer=basemap.disclaimer,
    )
