"""VIS-001 basemap acceptance tests.

B1: local micro-vector crop semantics (tmp_path, self-built geometry only).
B2: real global master integrity against basemap_sources/acquisition.json.
B3: offline (sockets disabled) rendering of the real local cache to a decodable PNG.

Real-cache checks (B2/B3) are gated by ``$NIB_BASEMAP_PATH``: when the variable
is set but the cache or source record is missing, the tests FAIL rather than
skip, per the VIS-001 acceptance rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from pathlib import Path

import geopandas as gpd
import matplotlib.image as mimg
import matplotlib.pyplot as plt
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

from nib_visualization.basemap import (
    BASEMAP_UNAVAILABLE,
    INVALID_EXTENT,
    INVALID_SOURCE_RECORD,
    BasemapError,
    crop_basemap,
    load_basemap,
)
from nib_visualization.cli import (
    BOUNDARY_STYLES,
    LAKE_STYLE,
    UNCLASSIFIED_LINE_STYLE,
    render_basemap_png,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_RECORD = REPO_ROOT / "basemap_sources" / "selected-source.json"
ACQUISITION_RECORD = REPO_ROOT / "basemap_sources" / "acquisition.json"

VIEWPORT = (0.0, 10.0, 0.0, 10.0)  # west, east, south, north
REAL_VIEWPORT = (5.0, 15.0, 47.0, 55.0)


# ---------------------------------------------------------------------------
# B1 - local micro vector layers built in tmp_path (no third-party geometry)
# ---------------------------------------------------------------------------


@pytest.fixture
def micro_source_record(tmp_path):
    record = {
        "source_id": "micro-test-source",
        "attribution": "Micro Test Source",
        "disclaimer": "micro test disclaimer",
        "layers": {
            "boundary_lines": {
                "gpkg_layer": "boundary_lines",
                "type_field": "bdytyp",
                "type_mapping": {"0": "coastline", "1": "international_boundary"},
            },
            "country_land": {"gpkg_layer": "country_land"},
            "lakes": {"gpkg_layer": "lakes"},
            "projected_lines": {"gpkg_layer": "projected_lines"},
        },
    }
    path = tmp_path / "micro-source.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


@pytest.fixture
def micro_gpkg(tmp_path):
    gpkg = tmp_path / "micro.gpkg"
    lines = gpd.GeoDataFrame(
        {"name": ["inside_coast", "crossing_border", "outside_line"], "bdytyp": [0, 1, 0]},
        geometry=[
            LineString([(2, 2), (8, 2)]),  # fully inside the viewport
            LineString([(-5, 5), (5, 5)]),  # crosses the west edge
            LineString([(20, 20), (30, 25)]),  # fully outside
        ],
        crs="EPSG:4326",
    )
    lines.to_file(gpkg, layer="boundary_lines", driver="GPKG")

    country_land = gpd.GeoDataFrame(
        {"name": ["viewport_land"]},
        geometry=[Polygon([(-2, -2), (12, -2), (12, 12), (-2, 12)])],
        crs="EPSG:4326",
    )
    country_land.to_file(gpkg, layer="country_land", driver="GPKG")

    lakes = gpd.GeoDataFrame(
        {"name": ["partial_lake"]},
        geometry=[Polygon([(5, -5), (9, -5), (9, 8), (5, 8)])],  # crosses the south edge
        crs="EPSG:4326",
    )
    lakes.to_file(gpkg, layer="lakes", driver="GPKG")

    projected = gpd.GeoDataFrame(
        {"name": ["projected_crossing"]},
        geometry=[LineString([(-5, 5), (5, 5)])],
        crs="EPSG:4326",
    ).to_crs("EPSG:3857")
    projected.to_file(gpkg, layer="projected_lines", driver="GPKG")
    return gpkg


def test_b1_crop_keeps_only_intersecting_geometry(micro_gpkg, micro_source_record):
    basemap = load_basemap(micro_gpkg, micro_source_record)
    view = crop_basemap(basemap, VIEWPORT)

    lines = view.layers["boundary_lines"]
    assert set(lines["name"]) == {"inside_coast", "crossing_border"}

    inside = lines.loc[lines["name"] == "inside_coast", "geometry"].iloc[0]
    assert inside.equals(LineString([(2, 2), (8, 2)])), "interior feature must survive unclipped"

    crossing = lines.loc[lines["name"] == "crossing_border", "geometry"].iloc[0]
    expected = LineString([(0, 5), (5, 5)])  # exact intersection with the west edge
    assert crossing.equals(expected), "edge-crossing feature must be clipped exactly"


def test_b1_crop_preserves_crs_types_and_exact_polygon_clip(micro_gpkg, micro_source_record):
    basemap = load_basemap(micro_gpkg, micro_source_record)
    view = crop_basemap(basemap, VIEWPORT)

    for layer in view.layers.values():
        assert layer.crs is not None and layer.crs.to_epsg() == 4326

    lines = view.layers["boundary_lines"]
    types = dict(zip(lines["name"], lines["boundary_type"], strict=True))
    assert types["inside_coast"] == "coastline"
    assert types["crossing_border"] == "international_boundary"

    lake = view.layers["lakes"].geometry.iloc[0]
    assert lake.equals(Polygon([(5, 0), (9, 0), (9, 8), (5, 8)])), (
        "lake crossing the south edge must be clipped exactly"
    )

    assert view.extent_wgs84 == VIEWPORT
    assert view.attribution == "Micro Test Source"
    assert view.disclaimer == "micro test disclaimer"


def test_b1_crop_reprojects_projected_layers(micro_gpkg, micro_source_record):
    basemap = load_basemap(micro_gpkg, micro_source_record)
    view = crop_basemap(basemap, VIEWPORT)

    projected = view.layers["projected_lines"]
    assert len(projected) == 1
    assert projected.crs.to_epsg() == 4326
    clipped = projected.geometry.iloc[0]
    expected = LineString([(0, 5), (5, 5)])
    assert clipped.equals_exact(expected, tolerance=1e-6), (
        "projected layer must be queried in its own CRS and returned clipped in WGS84"
    )


def test_b1_missing_cache_raises_basemap_unavailable(tmp_path, micro_source_record):
    with pytest.raises(BasemapError) as excinfo:
        load_basemap(tmp_path / "missing-cache.gpkg", micro_source_record)
    assert excinfo.value.code == BASEMAP_UNAVAILABLE


def test_b1_missing_source_record_raises(micro_gpkg, tmp_path):
    with pytest.raises(BasemapError) as excinfo:
        load_basemap(micro_gpkg, tmp_path / "missing-record.json")
    assert excinfo.value.code == INVALID_SOURCE_RECORD


def test_b1_rejects_invalid_extents(micro_gpkg, micro_source_record):
    basemap = load_basemap(micro_gpkg, micro_source_record)
    bad_extents = [
        (10.0, 0.0, 0.0, 10.0),  # west >= east
        (0.0, 10.0, 5.0, 0.0),  # south >= north
        (0.0, 10.0, 0.0, 91.0),  # north out of range
        (0.0, 190.0, 0.0, 10.0),  # east out of range
    ]
    for extent in bad_extents:
        with pytest.raises(BasemapError) as excinfo:
            crop_basemap(basemap, extent)
        assert excinfo.value.code == INVALID_EXTENT


# ---------------------------------------------------------------------------
# F-001/F-002 regressions (review 2026-09-22): line artists must not receive
# an opaque face fill; the footer must fit the real canvas with full text.
# ---------------------------------------------------------------------------


def test_f001_line_styles_disable_face_fill_but_keep_lake_fill():
    """Every boundary-line style must explicitly disable face fill (F-001)."""
    for name, style in BOUNDARY_STYLES.items():
        assert style.get("facecolor") == "none", f"line style '{name}' lacks facecolor='none'"
    assert UNCLASSIFIED_LINE_STYLE.get("facecolor") == "none"
    # the fix must not strip the optional lake polygon fill
    assert LAKE_STYLE.get("facecolor") not in (None, "none")


def test_f001_rendered_line_artists_get_no_opaque_facecolor(
    tmp_path, micro_gpkg, micro_source_record, monkeypatch
):
    """The styles actually handed to line FeatureArtists must stay unfilled (F-001)."""
    import nib_visualization.cli as cli

    calls: list[dict] = []
    real_shapely_feature = cli.ShapelyFeature

    def recording_feature(geometries, crs, **kwargs):
        calls.append(kwargs)
        return real_shapely_feature(geometries, crs, **kwargs)

    monkeypatch.setattr(cli, "ShapelyFeature", recording_feature)

    view = crop_basemap(load_basemap(micro_gpkg, micro_source_record), VIEWPORT)
    render_basemap_png(view, tmp_path / "f001.png")

    line_calls = [kwargs for kwargs in calls if "linestyle" in kwargs]
    fill_calls = [kwargs for kwargs in calls if "linestyle" not in kwargs]
    assert line_calls, "expected boundary-line artists to be drawn"
    assert fill_calls, "expected the lake polygon artist to be drawn"
    for kwargs in line_calls:
        assert kwargs.get("facecolor") == "none", (
            f"line artist received an opaque face fill: {kwargs!r}"
        )
    for kwargs in fill_calls:
        assert kwargs.get("facecolor") not in (None, "none"), "lake fill must survive the fix"


def test_country_land_uses_neutral_fill_distinct_from_lakes(
    tmp_path, micro_gpkg, micro_source_record, monkeypatch
):
    """Country polygons provide a neutral land cue, never the lake-blue fill."""
    import nib_visualization.cli as cli

    source = json.loads(SOURCE_RECORD.read_text(encoding="utf-8"))
    assert source["layers"]["country_land"]["service_layer_id"] == 107

    calls: list[dict] = []
    real_shapely_feature = cli.ShapelyFeature

    def recording_feature(geometries, crs, **kwargs):
        calls.append(kwargs)
        return real_shapely_feature(geometries, crs, **kwargs)

    monkeypatch.setattr(cli, "ShapelyFeature", recording_feature)

    view = crop_basemap(load_basemap(micro_gpkg, micro_source_record), VIEWPORT)
    render_basemap_png(view, tmp_path / "country-land.png")

    assert cli.COUNTRY_LAND_STYLE["facecolor"] == "#f1efe8"
    assert cli.COUNTRY_LAND_STYLE["edgecolor"] == "none"
    assert any(call.get("facecolor") == "#f1efe8" for call in calls)
    assert any(call.get("facecolor") == LAKE_STYLE["facecolor"] for call in calls)


def test_f002_footer_fits_canvas_and_keeps_full_text():
    """Footer must sit fully inside the real 1000x700 canvas, below the axes,
    and preserve the complete attribution + disclaimer text (F-002)."""
    from nib_visualization.cli import FOOTER_BOTTOM_MARGIN, _add_footer

    record = json.loads(SOURCE_RECORD.read_text(encoding="utf-8"))
    attribution, disclaimer = record["attribution"], record["disclaimer"]

    fig = plt.figure(figsize=(10.0, 7.0), facecolor="white")  # render_basemap_png canvas @100dpi
    fig.subplots_adjust(bottom=FOOTER_BOTTOM_MARGIN)
    ax = fig.add_subplot(1, 1, 1)
    artists = _add_footer(fig, attribution, disclaimer)
    assert artists, "footer helper must create at least one text artist"
    fig.canvas.draw()

    canvas_w, canvas_h = fig.canvas.get_width_height()
    assert (canvas_w, canvas_h) == (1000, 700)

    rendered = " ".join(artist.get_text() for artist in artists)
    assert rendered == f"{attribution} | {disclaimer}", (
        "footer text must be complete, word for word"
    )

    for artist in artists:
        bbox = artist.get_window_extent()
        assert bbox.x0 >= 0 and bbox.x1 <= canvas_w, f"footer line clipped horizontally: {bbox}"
        assert bbox.y0 >= 0 and bbox.y1 <= canvas_h, f"footer line outside the canvas: {bbox}"
    footer_top = max(artist.get_window_extent().y1 for artist in artists)
    assert footer_top <= ax.bbox.y0, "footer must stay inside the reserved bottom margin"

    # reading order: wrapped line i must sit ABOVE line i+1 (visual top-to-bottom)
    tops = [artist.get_window_extent().y1 for artist in artists]
    assert tops == sorted(tops, reverse=True), "footer lines must read top-to-bottom in wrap order"


# ---------------------------------------------------------------------------
# B2/B3 gate - real global master via $NIB_BASEMAP_PATH (FAIL, never skip,
# when the variable is set but the cache is missing)
# ---------------------------------------------------------------------------


def _require_real_cache() -> Path:
    value = os.environ.get("NIB_BASEMAP_PATH")
    if not value:
        pytest.skip(
            "NIB_BASEMAP_PATH not set; real-cache checks gated "
            "(VIS-001 acceptance runs with it set)"
        )
    path = Path(value)
    if not path.is_file():
        pytest.fail(f"NIB_BASEMAP_PATH={value} does not point to an existing cache file")
    return path


# ---------------------------------------------------------------------------
# B2 - real global master: independent four-way count equality + raw OID
# oracle (F-003), type distribution, checksum and completeness evidence
# ---------------------------------------------------------------------------


def _assert_layer_integrity(record: dict, layer: gpd.GeoDataFrame) -> None:
    """F-003 oracle: independently verify one real layer against its raw export.

    Establishes ``official == collected == features_written == len(layer)``,
    requires the raw export to exist with a matching hash, be a FeatureCollection,
    and carry the same complete, unique OID set as the GeoPackage layer.
    """
    name = record.get("gpkg_layer", "<unnamed layer>")
    official, collected, written = record["official_count"], record["collected_count"], record[
        "features_written"
    ]
    assert official == collected, f"{name}: official {official} != collected {collected}"
    assert collected == written, f"{name}: collected {collected} != written {written}"
    assert written == len(layer), (
        f"{name}: cache holds {len(layer)} features, record says {written}"
    )

    raw = Path(record["raw_file"])
    if not raw.is_absolute():
        raw = REPO_ROOT / raw
    assert raw.is_file(), f"{name}: raw export {raw} is missing"
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    assert digest == record["raw_sha256"], f"{name}: raw export sha256 mismatch"

    payload = json.loads(raw.read_text(encoding="utf-8"))
    assert payload.get("type") == "FeatureCollection", (
        f"{name}: raw export is not a FeatureCollection"
    )

    oid_field = record["order_field"]
    raw_ids = [feature["properties"].get(oid_field) for feature in payload["features"]]
    assert len(raw_ids) == collected, (
        f"{name}: raw holds {len(raw_ids)} features, collected {collected}"
    )
    assert all(
        value is not None for value in raw_ids
    ), f"{name}: raw has missing {oid_field} values"
    assert len(set(raw_ids)) == len(raw_ids), f"{name}: raw has duplicate {oid_field} values"

    assert oid_field in layer.columns, f"{name}: cache layer lacks the OID column {oid_field}"
    assert not layer[oid_field].isna().any(), f"{name}: cache has missing {oid_field} values"
    gpkg_ids = [int(value) for value in layer[oid_field]]
    assert len(set(gpkg_ids)) == len(gpkg_ids), f"{name}: cache has duplicate {oid_field} values"
    assert set(gpkg_ids) == {int(value) for value in raw_ids}, (
        f"{name}: cache OID set differs from the raw export"
    )

    assert layer.crs is not None and layer.crs.to_epsg() == 4326, (
        f"{name}: cache layer CRS is not EPSG:4326"
    )


def test_f003_integrity_helper_rejects_off_by_one(tmp_path):
    """Negative regression (F-003): a one-feature drift between counts must FAIL.

    Counterexample from the review: with actual/written at 742 and
    collected/official at 743, the previous two separate assertions both held.
    """
    good_record = {
        "gpkg_layer": "mini_lines",
        "official_count": 3,
        "collected_count": 3,
        "features_written": 3,
        "raw_file": "does/not/exist.geojson",
        "raw_sha256": "0" * 64,
        "order_field": "oid",
    }
    layer = gpd.GeoDataFrame(
        {"oid": [1, 2, 3]},
        geometry=[
            LineString([(0, 0), (1, 1)]),
            LineString([(2, 2), (3, 3)]),
            LineString([(4, 4), (5, 5)]),
        ],
        crs="EPSG:4326",
    )

    # cache/written lose one feature while official/collected stay at 3 (review case)
    with pytest.raises(AssertionError):
        _assert_layer_integrity({**good_record, "features_written": 2}, layer.iloc[:2])
    with pytest.raises(AssertionError):
        _assert_layer_integrity({**good_record, "collected_count": 2}, layer)
    # counts consistent but the raw export is gone: must fail, never silently skip
    with pytest.raises(AssertionError):
        _assert_layer_integrity(good_record, layer)

    # a fully consistent record + layer + raw export passes end to end
    raw_path = tmp_path / "mini_lines.geojson"
    features = [
        {"type": "Feature", "properties": {"oid": oid}, "geometry": None} for oid in (1, 2, 3)
    ]
    raw_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    full_record = {
        **good_record,
        "raw_file": str(raw_path),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }
    _assert_layer_integrity(full_record, layer)


def test_b2_layer_integrity_against_raw_and_service_counts():
    cache = _require_real_cache()
    acquisition = json.loads(ACQUISITION_RECORD.read_text(encoding="utf-8"))
    basemap = load_basemap(cache, SOURCE_RECORD)

    for name, record in acquisition["layers"].items():
        assert name in basemap.layers, f"acquisition layer {name} missing from the cache"
        _assert_layer_integrity(record, basemap.layers[name])


def test_b2_master_type_distribution_matches_acquisition_record():
    cache = _require_real_cache()
    acquisition = json.loads(ACQUISITION_RECORD.read_text(encoding="utf-8"))
    basemap = load_basemap(cache, SOURCE_RECORD)

    lines = basemap.layers["boundary_lines"]
    record = acquisition["layers"]["boundary_lines"]
    observed = {str(code): int(n) for code, n in lines["bdytyp"].value_counts().items()}
    assert observed == record["type_distribution_observed"], (
        "stored type distribution must match the acquisition evidence"
    )
    labels = basemap.boundary_type_labels

    def count_type(label: str) -> int:
        return sum(n for code, n in observed.items() if labels[int(code)] == label)

    assert count_type("coastline") > 0, "required layer type 'coastline' missing from master"
    assert (
        count_type("international_boundary") > 0
    ), "required layer type 'international_boundary' missing from master"


def test_b2_checksum_and_completeness_evidence():
    cache = _require_real_cache()
    acquisition = json.loads(ACQUISITION_RECORD.read_text(encoding="utf-8"))

    digest = hashlib.sha256(cache.read_bytes()).hexdigest()
    assert digest == acquisition["output_gpkg_sha256"], "GeoPackage checksum drift"

    assert acquisition["completeness"]["passed"] is True
    assert acquisition["access_date"] and acquisition["service_url"]
    assert acquisition["conversion"]["commands"]
    for name, record in acquisition["layers"].items():
        assert record["pages"] >= 1
        assert record["raw_sha256"], f"layer {name} missing raw export hash"


def test_b2_global_coverage_of_master():
    cache = _require_real_cache()
    acquisition = json.loads(ACQUISITION_RECORD.read_text(encoding="utf-8"))
    lines = load_basemap(cache, SOURCE_RECORD).layers["boundary_lines"]

    bounds = lines.total_bounds  # minx, miny, maxx, maxy
    recorded = acquisition["observed_global_coverage"]["boundary_lines_bounds_wgs84"]
    np.testing.assert_allclose(bounds, recorded, atol=1e-4)
    assert bounds[2] - bounds[0] > 300, "longitude span must be near-global"
    assert bounds[3] > 70 and bounds[1] < -50, "latitude span must reach high arctic and far south"


# ---------------------------------------------------------------------------
# B3 - offline rendering of the real local cache (sockets disabled)
# ---------------------------------------------------------------------------


@pytest.fixture
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise RuntimeError("network access attempted during offline render test")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    yield


def test_b3_offline_render_produces_decodable_png(tmp_path, no_network):
    cache = _require_real_cache()

    basemap = load_basemap(cache, SOURCE_RECORD)
    view = crop_basemap(basemap, REAL_VIEWPORT)
    assert "country_land" in view.layers and not view.layers["country_land"].empty, (
        "validation viewport must include the neutral country-land background"
    )
    drawn_types = set(view.layers["boundary_lines"]["boundary_type"])
    assert {"coastline", "international_boundary"} <= drawn_types, (
        f"validation viewport {REAL_VIEWPORT} should contain coastline and "
        f"international boundary lines, got {drawn_types}"
    )

    output = tmp_path / "b3_offline.png"
    render_basemap_png(view, output)

    image = mimg.imread(output)  # decode with the local matplotlib/Pillow stack
    assert image.shape == (700, 1000, 4), f"unexpected PNG shape {image.shape}"
    assert float(image[..., :3].std()) > 0.02, "rendered PNG is blank"
    non_white = float((image[..., :3].min(axis=-1) < 0.95).mean())
    assert non_white > 0.01, "rendered PNG has no visible content"

    # F-001 objective guard: the opaque matplotlib default-blue (C0) wedge fills
    # that the previous line styles produced must be gone from the real render.
    default_blue = np.array([31.0, 119.0, 180.0]) / 255.0
    color_distance = np.linalg.norm(image[..., :3] - default_blue, axis=-1)
    assert float((color_distance < 0.10).mean()) < 0.001, (
        "opaque default-blue fill regions present in the rendered basemap"
    )


def test_b3_missing_cache_with_env_set_fails(monkeypatch):
    monkeypatch.setenv("NIB_BASEMAP_PATH", "/nonexistent/global.gpkg")
    with pytest.raises(BasemapError) as excinfo:
        load_basemap(Path(os.environ["NIB_BASEMAP_PATH"]), SOURCE_RECORD)
    assert excinfo.value.code == BASEMAP_UNAVAILABLE
