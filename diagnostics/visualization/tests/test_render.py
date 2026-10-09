"""VIS-004 spatial map renderer acceptance tests.

M1: lat/lon and projected spatial correctness with independent oracles.
M2: one combined input (descending y + local NaN + valid zero) and immutability.
M3: one real date-line seam case (0-360 longitudes crossing +/-180).
M4: scientific labels, layer order/styles, text layout, CLI artifact, offline.

All oracles are recomputed inside the tests (own edge extrapolation, own pyproj
transformers, literal time strings); production helpers are never used to
generate expected values. Unit tests draw self-built micro GeoDataFrames and
need no real cache; only the real-cache check is gated by ``$NIB_BASEMAP_PATH``.
"""

from __future__ import annotations

import copy
import json
import os
import socket
from pathlib import Path

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib.image as mimg
import matplotlib.pyplot as plt
import numpy as np
import pytest
import xarray as xr
from pyproj import Transformer
from shapely.geometry import LineString, Polygon

from nib_visualization import PreparedScene, RenderConfig, prepare_scene, render_frame
from nib_visualization.basemap import LocalBasemap, load_basemap
from nib_visualization.cli import main as cli_main
from nib_visualization.field import VisualizationField
from nib_visualization.fixtures import make_fixture

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_RECORD = REPO_ROOT / "basemap_sources" / "selected-source.json"


def test_explicit_world_view_keeps_regional_field_and_time_unchanged():
    field = make_fixture("latlon")
    original = field.values.copy(deep=True)
    scene = prepare_scene(
        field, RenderConfig(viewport_wgs84=(-180, 180, -90, 90)), germany_basemap()
    )
    try:
        assert scene.viewport == (-180, 180, -90, 90)
        assert scene.crop_extents_wgs84 == ((-180, 180, -90, 90),)
        assert scene.central_longitude == 0
        np.testing.assert_allclose(scene.axes.get_extent(ccrs.PlateCarree()),
                                   [-180, 180, -90, 90])
        np.testing.assert_allclose(scene.x_edges, oracle_edges(field.values.x))
        np.testing.assert_allclose(scene.y_edges, oracle_edges(field.values.y))
        assert scene.field_footprint[1] - scene.field_footprint[0] < 20
        render_frame(scene, field, 1)
        np.testing.assert_array_equal(scene.mesh.get_array(), field.values.values[1])
        xr.testing.assert_identical(field.values, original)
        assert str(field.valid_time[1])[:19] in scene.time_artist.get_text()
    finally:
        plt.close(scene.figure)


def test_explicit_viewport_rejects_invalid_geographic_extent():
    with pytest.raises(ValueError, match="viewport_wgs84"):
        RenderConfig(viewport_wgs84=(-180, 180, -91, 90))

# Canonical-length footer strings (from selected-source.json) exercise wrapping.
LONG_ATTRIBUTION = (
    "Boundaries: UN Geospatial, OCHA FISS (Global_AB_stylized_fs_imagery, "
    "UN Geodata Simplified)"
)
LONG_DISCLAIMER = (
    "The publisher dataset is a simplified/generalized world reference, not an official "
    "UN map product for boundary delimitation. Any map produced from it must not be "
    "presented as an official UN map. UN map use/credit guidance applies."
)


# ---------------------------------------------------------------------------
# Independent oracles and builders (no production helpers)
# ---------------------------------------------------------------------------


def oracle_edges(centres) -> np.ndarray:
    """Cell-edge oracle: inner edges are centre midpoints, ends extrapolate
    the adjacent half spacing; direction (ascending/descending) is preserved."""
    c = np.asarray(centres, dtype=float)
    mids = (c[:-1] + c[1:]) / 2.0
    return np.concatenate(([2 * c[0] - mids[0]], mids, [2 * c[-1] - mids[-1]]))


def cell_index_of(edges, value) -> int:
    """Index of the cell containing ``value`` on an ascending or descending 1D edge axis."""
    e = np.asarray(edges, dtype=float)
    ascending = e[0] < e[-1]
    ordered = e if ascending else e[::-1]
    idx = int(np.searchsorted(ordered, value, side="right")) - 1
    idx = int(np.clip(idx, 0, e.size - 2))
    return idx if ascending else e.size - 2 - idx


def build_field(
    *,
    values,
    x,
    y,
    crs="EPSG:4326",
    name="air_temperature",
    units="K",
    statistic="instantaneous",
    init_time=None,
    lead_time=None,
    valid_time=None,
    interval_start=None,
    interval_end=None,
    provenance=None,
) -> VisualizationField:
    """Minimal valid VisualizationField builder for renderer inputs."""
    init_time = np.datetime64("2027-01-01T00:00:00") if init_time is None else init_time
    if lead_time is None:
        lead_time = np.arange(1, values.shape[0] + 1) * np.timedelta64(3600, "s")
    da = xr.DataArray(
        values,
        dims=("lead_time", "y", "x"),
        coords={"lead_time": lead_time, "y": y, "x": x},
        name=name,
        attrs={"units": units},
    )
    return VisualizationField(
        values=da,
        crs=crs,
        init_time=init_time,
        temporal_statistic=statistic,
        valid_time=valid_time,
        interval_start=interval_start,
        interval_end=interval_end,
        provenance=provenance,
    )


def germany_basemap(
    attribution="Micro Basemap Attribution", disclaimer="Micro disclaimer."
) -> LocalBasemap:
    """In-memory LocalBasemap intersecting the fixture domain (~7-13E, 49-53N)."""
    land = gpd.GeoDataFrame(
        {"name": ["micro_land"]},
        geometry=[Polygon([(4.0, 47.0), (16.0, 47.0), (16.0, 55.0), (4.0, 55.0)])],
        crs="EPSG:4326",
    )
    lines = gpd.GeoDataFrame(
        {"name": ["line_a", "line_b"]},
        geometry=[
            LineString([(6.0, 48.0), (6.0, 54.0)]),
            LineString([(8.0, 48.5), (14.0, 52.5)]),
        ],
        crs="EPSG:4326",
    )
    lakes = gpd.GeoDataFrame(
        {"name": ["micro_lake"]},
        geometry=[Polygon([(11.0, 51.2), (11.8, 51.2), (11.8, 51.9), (11.0, 51.9)])],
        crs="EPSG:4326",
    )
    return LocalBasemap(
        path=Path("micro-in-memory"),
        source_id="micro-test-source",
        attribution=attribution,
        disclaimer=disclaimer,
        layers={"country_land": land, "boundary_lines": lines, "lakes": lakes},
        boundary_type_field=None,
        boundary_type_labels={},
    )


def seam_basemap() -> object:
    """Micro basemap with one identifiable polygon on each side of +/-180."""
    land = gpd.GeoDataFrame(
        {"name": ["west_of_seam", "east_of_seam"]},
        geometry=[
            Polygon([(178.2, 9.0), (179.6, 9.0), (179.6, 14.0), (178.2, 14.0)]),
            Polygon([(-179.6, 9.0), (-178.2, 9.0), (-178.2, 14.0), (-179.6, 14.0)]),
        ],
        crs="EPSG:4326",
    )
    lines = gpd.GeoDataFrame(
        {"name": ["west_line", "east_line"]},
        geometry=[
            LineString([(178.4, 9.5), (178.4, 13.5)]),
            LineString([(-178.6, 9.5), (-178.6, 13.5)]),
        ],
        crs="EPSG:4326",
    )
    lakes = gpd.GeoDataFrame({"name": []}, geometry=[], crs="EPSG:4326")
    return LocalBasemap(
        path=Path("micro-seam-in-memory"),
        source_id="micro-seam-source",
        attribution="Micro Seam Source",
        disclaimer="Micro seam disclaimer.",
        layers={"country_land": land, "boundary_lines": lines, "lakes": lakes},
        boundary_type_field=None,
        boundary_type_labels={},
    )


def seam_field() -> VisualizationField:
    """Small valid EPSG:4326 field whose x centres cross +/-180 in 0-360 style."""
    x = np.array([178.0, 179.0, 180.0, 181.0, 182.0])
    y = np.array([10.0, 11.0, 12.0, 13.0])
    j = np.arange(4)[:, None]
    i = np.arange(5)[None, :]
    frames = np.stack(
        [
            np.exp(-(((i - 2.0) ** 2) + ((j - 1.5) ** 2)) / 3.0),
            np.exp(-(((i - 3.0) ** 2) + ((j - 2.0) ** 2)) / 3.0),
        ]
    )
    return build_field(
        values=frames,
        x=x,
        y=y,
        crs="EPSG:4326",
        name="seam_scalar",
        units="1",
        init_time=np.datetime64("2027-06-01T00:00:00"),
        lead_time=np.array([300, 600], dtype="timedelta64[s]"),
    )


def write_micro_cache(tmp_path: Path) -> tuple[Path, Path]:
    """Micro GeoPackage + source record on disk for CLI-level tests."""
    gpkg = tmp_path / "micro.gpkg"
    land = gpd.GeoDataFrame(
        {"name": ["micro_land"]},
        geometry=[Polygon([(4.0, 47.0), (16.0, 47.0), (16.0, 55.0), (4.0, 55.0)])],
        crs="EPSG:4326",
    )
    land.to_file(gpkg, layer="country_land", driver="GPKG")
    lines = gpd.GeoDataFrame(
        {"name": ["line_a", "line_b"]},
        geometry=[
            LineString([(6.0, 48.0), (6.0, 54.0)]),
            LineString([(8.0, 48.5), (14.0, 52.5)]),
        ],
        crs="EPSG:4326",
    )
    lines.to_file(gpkg, layer="boundary_lines", driver="GPKG")
    lakes = gpd.GeoDataFrame(
        {"name": ["micro_lake"]},
        geometry=[Polygon([(11.0, 51.2), (11.8, 51.2), (11.8, 51.9), (11.0, 51.9)])],
        crs="EPSG:4326",
    )
    lakes.to_file(gpkg, layer="lakes", driver="GPKG")
    record = {
        "source_id": "micro-test-source",
        "attribution": "Micro Test Source",
        "disclaimer": "Micro test disclaimer.",
        "layers": {
            "boundary_lines": {
                "gpkg_layer": "boundary_lines",
                "type_field": "bdytyp",
                "type_mapping": {"0": "coastline", "1": "international_boundary"},
            },
            "country_land": {"gpkg_layer": "country_land"},
            "lakes": {"gpkg_layer": "lakes"},
        },
    }
    record_path = tmp_path / "micro-source.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return gpkg, record_path


def save_and_decode(scene: PreparedScene, tmp_path: Path, name: str) -> np.ndarray:
    out = tmp_path / name
    scene.figure.savefig(out, dpi=scene.config.dpi)
    return mimg.imread(out)


def argmax_cell(array: np.ndarray) -> tuple[int, int]:
    # nanargmax: a local NaN must never win the peak lookup
    return np.unravel_index(int(np.nanargmax(array)), array.shape)


# ---------------------------------------------------------------------------
# R0 - config and exports
# ---------------------------------------------------------------------------


def test_r0_root_exports_reference_same_objects():
    import nib_visualization as nv

    assert nv.RenderConfig is RenderConfig
    assert nv.PreparedScene is PreparedScene
    assert nv.prepare_scene is prepare_scene
    assert nv.render_frame is render_frame


@pytest.mark.parametrize(
    "kwargs",
    [
        {"buffer_fraction": -0.01},
        {"buffer_fraction": -1.0},
        {"dpi": 0},
        {"dpi": -100},
        {"figsize": (0.0, 7.0)},
        {"figsize": (10.0, -2.0)},
        {"vmin": 2.0, "vmax": 1.0},
        {"vmin": 1.0, "vmax": 1.0},
    ],
)
def test_r0_invalid_config_raises_value_error(kwargs):
    with pytest.raises(ValueError, match="buffer|dpi|figsize|vmin|vmax|color limit"):
        RenderConfig(**kwargs)


def test_r0_constant_field_and_single_color_limit(tmp_path):
    constant = build_field(
        values=np.full((2, 4, 5), 3.25), x=np.linspace(8.0, 12.0, 5), y=np.linspace(49.0, 52.0, 4)
    )
    scene = prepare_scene(constant, RenderConfig(), germany_basemap())
    try:
        assert np.isfinite(scene.norm.vmin) and np.isfinite(scene.norm.vmax)
        assert scene.norm.vmin < 3.25 < scene.norm.vmax
        assert 3.25 - scene.norm.vmin == pytest.approx(scene.norm.vmax - 3.25)
        scene2 = prepare_scene(constant, RenderConfig(vmin=3.0), germany_basemap())
        try:
            assert scene2.norm.vmin == 3.0
            assert scene2.norm.vmax > 3.0
        finally:
            plt.close(scene2.figure)
        img = save_and_decode(scene, tmp_path, "r0_constant.png")
        assert img.shape[:2] == (700, 1000)
        assert img.std() > 0.0
    finally:
        plt.close(scene.figure)


# ---------------------------------------------------------------------------
# M1 - lat/lon and projected spatial correctness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_m1_decodable_default_size_png(grid, tmp_path):
    field = make_fixture(grid)
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        figure = render_frame(scene, field, 0)
        assert figure is scene.figure
        img = save_and_decode(scene, tmp_path, f"m1_{grid}.png")
        assert img.shape[:2] == (700, 1000)
        assert float(img.std()) > 0.0
    finally:
        plt.close(scene.figure)


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_m1_mesh_uses_native_edges_from_centres(grid):
    field = make_fixture(grid)
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        x = field.values.coords["x"].values
        y = field.values.coords["y"].values
        expected_x, expected_y = oracle_edges(x), oracle_edges(y)
        assert scene.x_edges.size == x.size + 1
        assert scene.y_edges.size == y.size + 1
        np.testing.assert_allclose(scene.x_edges, expected_x, rtol=1e-12)
        np.testing.assert_allclose(scene.y_edges, expected_y, rtol=1e-12)
        # the QuadMesh as placed in the scene carries exactly these edges
        coords = scene.mesh.get_coordinates()
        assert coords.shape == (y.size + 1, x.size + 1, 2)
        np.testing.assert_allclose(
            coords[..., 0], np.broadcast_to(expected_x, (y.size + 1, x.size + 1)), rtol=1e-9
        )
        np.testing.assert_allclose(
            coords[..., 1],
            np.broadcast_to(expected_y[:, None], (y.size + 1, x.size + 1)),
            rtol=1e-9,
        )
    finally:
        plt.close(scene.figure)


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_m1_gaussian_peak_geographic_alignment(grid):
    field = make_fixture(grid)
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        render_frame(scene, field, 0)
        frame = field.values.values[0]
        j, i = argmax_cell(frame)
        x = field.values.coords["x"].values
        y = field.values.coords["y"].values

        # own transformer: peak-cell centre -> WGS84, within one cell of (8.5E, 50N)
        own = Transformer.from_crs(field.crs, "EPSG:4326", always_xy=True)
        if grid == "latlon":
            peak_lon, peak_lat = float(x[i]), float(y[j])
            cell_dx, cell_dy = x[1] - x[0], y[1] - y[0]
        else:
            peak_lon, peak_lat = own.transform(x[i], y[j])
            corners = own.transform(
                np.array([x[i], x[i + 1], x[i], x[i + 1]]),
                np.array([y[j], y[j], y[j + 1], y[j + 1]]),
            )
            cell_dx = corners[0].max() - corners[0].min()
            cell_dy = corners[1].max() - corners[1].min()
        assert abs(peak_lon - 8.5) <= cell_dx
        assert abs(peak_lat - 50.0) <= cell_dy

        # data CRS transform is metrologically correct (own pyproj oracle)
        cc_pt = scene.data_cartopy_crs.transform_point(8.5, 50.0, ccrs.PlateCarree())
        if grid == "projected":
            fwd = Transformer.from_crs("EPSG:4326", field.crs, always_xy=True)
            ref_x, ref_y = fwd.transform(8.5, 50.0)
            assert cc_pt == pytest.approx((ref_x, ref_y), rel=1e-6)
        else:
            assert cc_pt == pytest.approx((8.5, 50.0))

        # scene-level display alignment: known lon/lat -> display -> data cell
        disp = scene.display_crs.transform_point(8.5, 50.0, ccrs.PlateCarree())
        native = scene.data_cartopy_crs.transform_point(disp[0], disp[1], scene.display_crs)
        ci = cell_index_of(scene.x_edges, native[0])
        cj = cell_index_of(scene.y_edges, native[1])
        assert abs(ci - i) <= 1
        assert abs(cj - j) <= 1

        # viewport actually contains the known position
        extent = scene.axes.get_extent(scene.display_crs)
        assert extent[0] <= disp[0] <= extent[1]
        assert extent[2] <= disp[1] <= extent[3]
    finally:
        plt.close(scene.figure)


def test_m1_latlon_viewport_is_footprint_plus_five_percent_buffer():
    field = make_fixture("latlon")
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        x = field.values.coords["x"].values
        y = field.values.coords["y"].values
        ex, ey = oracle_edges(x), oracle_edges(y)
        west, east, south, north = float(ex[0]), float(ex[-1]), float(ey[0]), float(ey[-1])
        span_lon, span_lat = east - west, north - south
        assert scene.field_footprint == pytest.approx((west, east, south, north))
        assert scene.viewport == pytest.approx(
            (
                west - 0.05 * span_lon,
                east + 0.05 * span_lon,
                south - 0.05 * span_lat,
                north + 0.05 * span_lat,
            )
        )
        assert scene.crop_extents_wgs84 == (
            pytest.approx(
                (
                    west - 0.05 * span_lon,
                    east + 0.05 * span_lon,
                    south - 0.05 * span_lat,
                    north + 0.05 * span_lat,
                )
            ),
        )
        assert len(scene.basemap_views) == 1
    finally:
        plt.close(scene.figure)


def test_m1_projected_display_extent_is_geographic_not_metres():
    field = make_fixture("projected")
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        extent = scene.axes.get_extent(ccrs.PlateCarree())
        assert 6.0 < extent[0] < 8.0
        assert 12.0 < extent[1] < 14.0
        assert 48.0 < extent[2] < 50.0
        assert 52.0 < extent[3] < 54.0
        # native mesh edges stay in UTM metres (not interpreted as degrees)
        assert 1.0e5 < np.abs(scene.x_edges).max() < 1.0e6
        assert 5.0e6 < np.abs(scene.y_edges).max() < 7.0e6
    finally:
        plt.close(scene.figure)


def test_m1_out_of_domain_projected_transform_fails_clearly():
    field = build_field(
        values=np.ones((1, 2, 3)),
        x=np.array([4.0e7, 4.0e7 + 2000.0, 4.0e7 + 4000.0]),
        y=np.array([5.0e6, 5.0e6 + 2000.0]),
        crs="EPSG:32632",
    )
    with pytest.raises(ValueError, match="non-finite|footprint"):
        prepare_scene(field, RenderConfig(), germany_basemap())


def test_m1_unsupported_non_utm_projected_crs_rejected():
    field = build_field(
        values=np.ones((1, 3, 4)),
        x=np.linspace(1.0e6, 1.1e6, 4),
        y=np.linspace(6.0e6, 6.1e6, 3),
        crs="EPSG:3857",
    )
    with pytest.raises(ValueError, match="UTM|projected CRS"):
        prepare_scene(field, RenderConfig(), germany_basemap())


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_m1_scene_reused_across_two_leads(grid, monkeypatch):
    import nib_visualization.render as render_module

    crop_calls = []
    original_crop = render_module.crop_basemap

    def counting_crop(basemap, extent):
        crop_calls.append(extent)
        return original_crop(basemap, extent)

    monkeypatch.setattr(render_module, "crop_basemap", counting_crop)

    field = make_fixture(grid)
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        crops_after_prepare = len(crop_calls)
        assert crops_after_prepare in (1, 2)
        vmin0, vmax0 = scene.norm.vmin, scene.norm.vmax
        views0 = scene.basemap_views

        figure0 = render_frame(scene, field, 0)
        values0 = np.array(scene.mesh.get_array(), copy=True)
        text0 = scene.time_artist.get_text()
        extent0 = scene.axes.get_extent(scene.display_crs)

        figure1 = render_frame(scene, field, 11)
        assert figure1 is figure0 is scene.figure
        assert len(crop_calls) == crops_after_prepare  # no re-crop per frame
        assert scene.basemap_views is views0

        values1 = np.array(scene.mesh.get_array(), copy=True)
        assert not np.array_equal(values0, values1)
        assert scene.time_artist.get_text() != text0

        # the moving Gaussian peak moves east and north between lead 0 and 11
        j0, i0 = argmax_cell(values0)
        j1, i1 = argmax_cell(values1)
        assert i1 > i0 and j1 > j0

        # fixed objects and semantics
        assert scene.axes.get_extent(scene.display_crs) == pytest.approx(extent0)
        assert scene.norm.vmin == vmin0 and scene.norm.vmax == vmax0
        assert scene.mesh.norm is scene.norm
        assert scene.mesh.get_cmap() is scene.cmap
        assert scene.figure.get_size_inches() == pytest.approx(scene.config.figsize)

        # a different grid must not silently reuse the prepared scene
        other = make_fixture("projected" if grid == "latlon" else "latlon")
        with pytest.raises(ValueError, match="grid|scene"):
            render_frame(scene, other, 0)
    finally:
        plt.close(scene.figure)


def test_review_f001_projected_default_has_no_cell_compositing_seams(tmp_path):
    field = make_fixture("projected")
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        image = save_and_decode(scene, tmp_path, "projected-default.png")
        # Interior of the low-valued north-east field, away from coastlines,
        # grid labels and the moving Gaussian. A smooth cell must not acquire
        # dense short dark lines when UTM quads are rasterized.
        roi = np.rint(image[150:210, 700:770, :3] * 255).astype(np.uint8)
        colors, counts = np.unique(roi.reshape(-1, 3), axis=0, return_counts=True)
        modal = colors[np.argmax(counts)]
        outliers = np.linalg.norm(roi.astype(float) - modal.astype(float), axis=2) > 10
        assert outliers.mean() < 0.02
    finally:
        plt.close(scene.figure)


def test_review_f002_scene_rejects_different_field_semantics_before_update():
    field = make_fixture("latlon")
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        render_frame(scene, field, 0)
        original_mesh = np.array(scene.mesh.get_array(), copy=True)
        original_time = scene.time_artist.get_text()

        different = copy.deepcopy(field)
        different.values.name = "wind_speed"
        different.values.attrs["units"] = "m/s"
        different.values.values[:] = 100.0
        with pytest.raises(ValueError, match="scene|semantics|limits"):
            render_frame(scene, different, 1)
        np.testing.assert_array_equal(scene.mesh.get_array(), original_mesh)
        assert scene.time_artist.get_text() == original_time

        # A separate object with the same grid and all display semantics is
        # still a legitimate reusable input.
        equivalent = copy.deepcopy(field)
        assert render_frame(scene, equivalent, 1) is scene.figure
    finally:
        plt.close(scene.figure)


def test_review_f002_scene_rejects_changed_implicit_limits():
    field = make_fixture("latlon")
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        different = copy.deepcopy(field)
        different.values.values[:] = 100.0
        with pytest.raises(ValueError, match="scene|semantics|limits"):
            render_frame(scene, different, 1)
    finally:
        plt.close(scene.figure)


# ---------------------------------------------------------------------------
# M2 - descending y + partial NaN + valid zero + immutability
# ---------------------------------------------------------------------------


def test_m2_descending_y_nan_zero_alignment_and_immutability(tmp_path):
    src = make_fixture("latlon")
    src_before = copy.deepcopy(src)
    x = src.values.coords["x"].values
    y = src.values.coords["y"].values

    data = src.values.values[:, ::-1, :].copy()
    data[0, 3, 4] = np.nan  # one local NaN in the rendered frame
    data[0, 5, 6] = 0.0  # a valid zero that must not become missing
    assert np.isnan(data[0, 3, 4]) and np.any(data[0] == 0.0)
    combo = build_field(
        values=data,
        x=x,
        y=y[::-1],
        crs="EPSG:4326",
        name=src.values.name,
        units=src.values.attrs["units"],
        init_time=src.init_time,
        lead_time=src.values.coords["lead_time"].values,
    )
    combo_before = copy.deepcopy(combo)

    scene = prepare_scene(combo, RenderConfig(), germany_basemap())
    try:
        render_frame(scene, combo, 0)
        # descending y preserved, values not flipped to match ascending rows
        expected_y = oracle_edges(y[::-1])
        assert np.all(np.diff(scene.y_edges) < 0)
        np.testing.assert_allclose(scene.y_edges, expected_y, rtol=1e-12)

        mesh_values = np.array(scene.mesh.get_array())
        assert np.isnan(mesh_values[3, 4])
        assert mesh_values[5, 6] == 0.0

        # NaN renders fully transparent; valid zero keeps a real colour
        bad = scene.cmap.get_bad()
        assert bad[3] == 0.0
        zero_rgba = scene.cmap(scene.norm(0.0))
        assert not np.ma.is_masked(scene.norm(0.0))
        assert zero_rgba[3] > 0.99

        # spatial oracle through the scene: analytical peak (8.5E, 50N)
        j, i = argmax_cell(mesh_values)
        disp = scene.display_crs.transform_point(8.5, 50.0, ccrs.PlateCarree())
        native = scene.data_cartopy_crs.transform_point(disp[0], disp[1], scene.display_crs)
        ci = cell_index_of(scene.x_edges, native[0])
        cj = cell_index_of(scene.y_edges, native[1])
        assert abs(ci - i) <= 1
        assert abs(cj - j) <= 1

        img = save_and_decode(scene, tmp_path, "m2_descending_nan_zero.png")
        assert img.shape[:2] == (700, 1000)
    finally:
        plt.close(scene.figure)

    # input immutability: values, coords, attrs, dims, times, provenance
    xr.testing.assert_identical(combo_before.values, combo.values)
    assert dict(combo_before.values.attrs) == dict(combo.values.attrs)
    assert combo_before.crs == combo.crs
    assert combo_before.init_time == combo.init_time
    assert (combo_before.provenance or {}) == (combo.provenance or {})
    xr.testing.assert_identical(src_before.values, src.values)
    assert np.array_equal(src_before.valid_time, src.valid_time)


# ---------------------------------------------------------------------------
# M3 - one date-line seam case
# ---------------------------------------------------------------------------


def test_m3_dateline_seam_two_legal_crops_local_viewport(tmp_path):
    field = seam_field()
    before = copy.deepcopy(field)
    x = field.values.coords["x"].values
    y = field.values.coords["y"].values

    scene = prepare_scene(field, RenderConfig(), seam_basemap())
    try:
        extents = scene.crop_extents_wgs84
        assert len(extents) == 2
        assert len(scene.basemap_views) == 2
        (w1, e1, s1, n1), (w2, e2, s2, n2) = extents
        assert e1 == pytest.approx(180.0)
        assert w2 == pytest.approx(-180.0)
        assert -180.0 <= w1 < e1 <= 180.0
        assert -180.0 <= w2 < e2 <= 180.0
        assert s1 == pytest.approx(s2) and n1 == pytest.approx(n2)

        # independent expected values from the centres
        ex, ey = oracle_edges(x), oracle_edges(y)
        west, east = float(ex[0]), float(ex[-1])
        south, north = float(ey[0]), float(ey[-1])
        span = east - west
        assert w1 == pytest.approx(west - 0.05 * span)
        assert e2 == pytest.approx(-180.0 + (east + 0.05 * span - 180.0))
        assert s1 == pytest.approx(south - 0.05 * (north - south))
        assert n1 == pytest.approx(north + 0.05 * (north - south))

        # viewport stays local, display central longitude follows the input centre
        viewport = scene.viewport
        assert viewport[1] - viewport[0] < 20.0
        assert scene.central_longitude == pytest.approx(180.0)
        display_extent = scene.axes.get_extent(scene.display_crs)
        assert display_extent[1] - display_extent[0] < 20.0

        # both sides of the seam survive in the cropped geometry
        land_geoms = [
            g for view in scene.basemap_views for g in view.layers["country_land"].geometry
        ]
        assert any(g.bounds[0] > 170.0 for g in land_geoms)
        assert any(g.bounds[2] < -170.0 for g in land_geoms)

        # mesh columns stay in the source's continuous 0-360 expression
        np.testing.assert_allclose(scene.x_edges, ex, rtol=1e-12)
        mesh_values = np.array(scene.mesh.get_array())
        assert mesh_values.shape == (y.size, x.size)
        np.testing.assert_allclose(mesh_values, field.values.values[0], rtol=1e-12)

        render_frame(scene, field, 1)
        img = save_and_decode(scene, tmp_path, "m3_seam.png")
        assert img.shape[:2] == (700, 1000)
        assert float(img.std()) > 0.0
    finally:
        plt.close(scene.figure)

    # source coords/values untouched by the seam handling
    xr.testing.assert_identical(before.values, field.values)


# ---------------------------------------------------------------------------
# M4 - labels, layer order, layout, CLI artifact, offline
# ---------------------------------------------------------------------------


def test_m4_instantaneous_labels_and_time_semantics():
    field = make_fixture("latlon")
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        render_frame(scene, field, 3)
        title = scene.axes.get_title()
        assert "synthetic_scalar" in title
        assert "(1)" in title

        text = scene.time_artist.get_text()
        assert "instantaneous" in text
        assert "2026-09-21T00:00:00" in text  # init_time
        assert "20 min" in text  # lead at index 3
        assert "2026-09-21T00:20:00" in text  # valid time

        colorbar_label = scene.colorbar.ax.get_ylabel()
        assert "synthetic_scalar" in colorbar_label
        assert "1" in colorbar_label
    finally:
        plt.close(scene.figure)


def test_m4_accumulation_interval_displayed():
    x = np.linspace(8.0, 12.0, 6)
    y = np.linspace(49.0, 52.0, 5)
    data = (np.arange(2 * 5 * 6).reshape(2, 5, 6) % 97) / 10.0 + 1.0
    init = np.datetime64("2027-03-01T06:00:00")
    lead = np.array([3600, 7200], dtype="timedelta64[s]")
    valid = init + lead
    field = build_field(
        values=data,
        x=x,
        y=y,
        statistic="accumulation",
        init_time=init,
        lead_time=lead,
        valid_time=valid,
        interval_start=np.array(
            ["2027-03-01T05:00:00", "2027-03-01T06:00:00"], dtype="datetime64[s]"
        ),
        interval_end=valid.copy(),
        name="precipitation_amount",
        units="kg m-2",
    )
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        render_frame(scene, field, 0)
        text = scene.time_artist.get_text()
        assert "accumulation" in text
        assert "2027-03-01T06:00:00" in text  # init
        assert "2027-03-01T05:00:00" in text  # interval start of this lead
        assert "2027-03-01T07:00:00" in text  # interval end == valid time
        assert "precipitation_amount" in scene.axes.get_title()
    finally:
        plt.close(scene.figure)


def test_m4_layer_order_and_line_styles(monkeypatch):
    from cartopy.mpl.geoaxes import GeoAxes

    import nib_visualization.render as render_module

    created: list[dict] = []

    class RecordingFeature(render_module.ShapelyFeature):
        def __init__(self, geoms, crs, **kwargs):
            created.append({"geoms": list(geoms), "crs": crs, **kwargs})
            super().__init__(geoms, crs, **kwargs)

    monkeypatch.setattr(render_module, "ShapelyFeature", RecordingFeature)

    added: list[tuple[object, dict]] = []
    original_add = GeoAxes.add_feature

    def recording_add(self, feature, **kwargs):
        added.append((feature, kwargs))
        return original_add(self, feature, **kwargs)

    monkeypatch.setattr(GeoAxes, "add_feature", recording_add)

    field = make_fixture("latlon")
    scene = prepare_scene(field, RenderConfig(), germany_basemap())
    try:
        assert len(created) == len(added)
        line_zorders, land_zorder, lake_zorder = [], None, None
        for spec, (_, add_kwargs) in zip(created, added, strict=True):
            geom_names = {g.geom_type for g in spec["geoms"]}
            if "LineString" in geom_names:
                line_zorders.append(add_kwargs["zorder"])
                assert spec["facecolor"] == "none"  # lines never get a face fill
            elif spec.get("edgecolor") == "none":
                land_zorder = add_kwargs["zorder"]
            else:
                lake_zorder = add_kwargs["zorder"]
        assert land_zorder is not None and lake_zorder is not None and line_zorders
        mesh_zorder = scene.mesh.get_zorder()
        assert land_zorder < mesh_zorder < lake_zorder < min(line_zorders)
    finally:
        plt.close(scene.figure)


def test_m4_texts_inside_canvas_and_footer_clear_of_axes(tmp_path):
    field = make_fixture("projected")
    long_footer_basemap = germany_basemap(
        attribution=LONG_ATTRIBUTION, disclaimer=LONG_DISCLAIMER
    )
    scene = prepare_scene(field, RenderConfig(), long_footer_basemap)
    try:
        render_frame(scene, field, 0)
        fig = scene.figure
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        width, height = fig.get_size_inches() * fig.dpi

        def inside(artist):
            box = artist.get_window_extent(renderer=renderer)
            return box.x0 >= 0.0 and box.y0 >= 0.0 and box.x1 <= width and box.y1 <= height

        assert inside(scene.axes.title)
        assert inside(scene.time_artist)
        assert inside(scene.colorbar.ax.yaxis.label)
        assert inside(scene.colorbar.ax)
        footer = fig.texts
        assert len(footer) >= 2  # the long disclaimer wraps
        assert all(inside(text) for text in footer)

        axes_box = scene.axes.get_window_extent(renderer=renderer)
        for text in footer:
            box = text.get_window_extent(renderer=renderer)
            assert box.y1 <= axes_box.y0 + 1e-6
    finally:
        plt.close(scene.figure)


def test_m4_cli_map_smoke(tmp_path, capsys):
    gpkg, record = write_micro_cache(tmp_path)
    out = tmp_path / "nested" / "dir" / "map.png"
    rc = cli_main(
        [
            "map", "--grid", "latlon", "--lead-index", "2",
            "--basemap", str(gpkg), "--source-record", str(record),
            "--output", str(out),
        ]
    )
    assert rc == 0
    assert str(out) in capsys.readouterr().out
    img = mimg.imread(out)
    assert img.shape[:2] == (700, 1000)
    assert float(img.std()) > 0.0

    rc_bad_record = cli_main(
        [
            "map", "--grid", "latlon", "--lead-index", "0",
            "--basemap", str(gpkg), "--source-record", str(tmp_path / "missing.json"),
            "--output", str(tmp_path / "bad.png"),
        ]
    )
    assert rc_bad_record != 0

    rc_bad_lead = cli_main(
        [
            "map", "--grid", "latlon", "--lead-index", "99",
            "--basemap", str(gpkg), "--source-record", str(record),
            "--output", str(tmp_path / "bad2.png"),
        ]
    )
    assert rc_bad_lead != 0
    assert not (tmp_path / "bad2.png").exists()


def test_m4_offline_render_with_sockets_blocked(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted during map rendering")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)

    gpkg, record = write_micro_cache(tmp_path)
    out = tmp_path / "offline.png"
    rc = cli_main(
        [
            "map", "--grid", "projected", "--lead-index", "0",
            "--basemap", str(gpkg), "--source-record", str(record),
            "--output", str(out),
        ]
    )
    assert rc == 0
    img = mimg.imread(out)
    assert img.shape[:2] == (700, 1000)


@pytest.mark.skipif(
    not os.environ.get("NIB_BASEMAP_PATH"),
    reason="real cache not enabled; set NIB_BASEMAP_PATH for the real-basemap check",
)
@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_m4_real_cache_map_png(tmp_path, grid):
    cache = Path(os.environ["NIB_BASEMAP_PATH"])
    if not cache.is_file():
        pytest.fail(f"NIB_BASEMAP_PATH={cache} does not point to an existing cache file")
    field = make_fixture(grid)
    basemap = load_basemap(cache, SOURCE_RECORD)
    scene = prepare_scene(field, RenderConfig(), basemap)
    try:
        render_frame(scene, field, 0)
        img = save_and_decode(scene, tmp_path, f"m4_real_{grid}.png")
        assert img.shape[:2] == (700, 1000)
        assert float(img.std()) > 0.0
    finally:
        plt.close(scene.figure)
