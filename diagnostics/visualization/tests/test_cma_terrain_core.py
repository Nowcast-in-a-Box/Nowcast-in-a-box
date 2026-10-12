"""Observation-derived mocks and georeferenced relief use the public Core path."""

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import netCDF4
import numpy as np
import pytest
from matplotlib.colors import ListedColormap
from PIL import Image
from pyproj import CRS
from shapely.geometry import box

from nib_visualization import (
    CompositeBasemap,
    RenderConfig,
    load_basemap,
    prepare_scene,
    render_frame,
    write_animation,
    write_manifest,
)
from nib_visualization.adapters.cma_mock import read_cma_mock
from nib_visualization.artifacts import axes_pixel_box, inspect_outputs
from nib_visualization.terrain import load_etopo


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mock_npz(directory, minutes):
    directory.mkdir(exist_ok=True)
    paths = []
    for index, minute in enumerate(minutes):
        values = np.arange(48, dtype="f4").reshape(6, 8) + index * 5
        values[0, :2] = (0, -128)
        time = np.datetime64("2024-07-05T00:00") + np.timedelta64(minute, "m")
        path = directory / f"reverse-{len(minutes) - index}.npz"
        np.savez_compressed(
            path, reflectivity=values,
            latitude=np.arange(15.5, 9.5, -1), longitude=np.arange(70.5, 78.5),
            bbox=np.array([70.5, 9.5, 78.5, 15.5]), resolution_deg=1.0,
            data_time=np.datetime_as_string(time, unit="s") + "Z",
            generation_time="2024-07-05T01:00:00Z", source_file=f"source-{index}.bin",
            source="cma_radar", product="CREF", unit="dBZ",
        )
        paths.append(path)
    return paths


@pytest.fixture
def terrain(tmp_path):
    path = tmp_path / "terrain.nc"
    values = np.arange(48, dtype="f4").reshape(6, 8) * 100 - 2000
    with netCDF4.Dataset(path, "w") as ds:
        for name, centres, units in (
            ("lat", np.arange(10.5, 16.5), "degrees_north"),
            ("lon", np.arange(70.5, 78.5), "degrees_east"),
        ):
            ds.createDimension(name, len(centres))
            variable = ds.createVariable(name, "f8", (name,))
            variable.units = units
            variable[:] = centres
        crs = ds.createVariable("crs", "S1")
        crs.spatial_ref = CRS.from_epsg(4326).to_wkt()
        z = ds.createVariable("z", "f4", ("lat", "lon"))
        z.grid_mapping, z.units = "crs", "meters"
        z[:] = values
    record = tmp_path / "terrain-source.json"
    record.write_text(json.dumps({
        "source_id": "test-etopo", "horizontal_crs": "EPSG:4326",
        "vertical_crs": "EPSG:3855", "citation": "Test terrain attribution",
        "disclaimer": "Test terrain disclaimer",
    }))
    return path, record, values


@pytest.mark.parametrize("minutes", [[0], [0, 6, 21]])
def test_mock_times_are_dynamic_raw_values_retained(tmp_path, minutes):
    paths = mock_npz(tmp_path / "input", minutes)
    before = [digest(p) for p in paths]
    field = read_cma_mock(tmp_path / "input", crs="EPSG:4326", display_width=8)
    assert field.values.shape == (len(minutes), 6, 8)
    np.testing.assert_array_equal(
        field.values.lead_time, np.array(minutes, dtype="timedelta64[m]")
    )
    np.testing.assert_array_equal(
        field.valid_time,
        np.datetime64("2024-07-05T00:00") + np.array(minutes, dtype="timedelta64[m]"),
    )
    for index, path in enumerate(paths):
        with np.load(path) as source:
            np.testing.assert_array_equal(field.values[index], source["reflectivity"])
    assert field.values[0, 0, 0] == 0
    assert field.values[0, 0, 1] == -128
    assert field.provenance["mock"] and not field.provenance["is_real_forecast"]
    assert [digest(p) for p in paths] == before


def test_mock_display_sampling_preserves_footprint_and_records_transformation(tmp_path):
    paths = mock_npz(tmp_path / "input", [0, 12])
    field = read_cma_mock(tmp_path / "input", crs="EPSG:4326", display_width=4)
    assert field.values.shape == (2, 3, 4)
    with np.load(paths[0]) as source:
        expected = source["reflectivity"][np.ix_([1, 3, 5], [1, 3, 5, 7])]
    np.testing.assert_array_equal(field.values[0], expected)
    np.testing.assert_array_equal(field.values.x, [71, 73, 75, 77])
    np.testing.assert_array_equal(field.values.y, [15, 13, 11])
    assert field.provenance["native_shape"] == [6, 8]
    assert field.provenance["display_shape"] == [3, 4]
    assert "nearest" in field.provenance["spatial_sampling"]


def test_mock_rejects_duplicate_times_and_grid_change(tmp_path):
    paths = mock_npz(tmp_path / "input", [0, 6])
    with np.load(paths[1]) as data:
        contents = dict(data)
    contents["data_time"] = np.array("2024-07-05T00:00:00Z")
    np.savez_compressed(paths[1], **contents)
    with pytest.raises(ValueError, match="duplicate"):
        read_cma_mock(tmp_path / "input", crs="EPSG:4326")
    contents["data_time"] = np.array("2024-07-05T00:06:00Z")
    contents["longitude"] = contents["longitude"] + 1
    np.savez_compressed(paths[1], **contents)
    with pytest.raises(ValueError, match="grid|Grid|bbox"):
        read_cma_mock(tmp_path / "input", crs="EPSG:4326")


def test_relief_crop_has_exact_source_samples_and_edges(terrain):
    path, record, values = terrain
    source = load_etopo(path, record, expected_sha256=digest(path), max_width=3)
    view = source.crop((71, 77, 11, 15))
    np.testing.assert_array_equal(view.values, values[np.ix_([2, 4], [2, 4, 6])])
    assert view.extent_wgs84 == (71, 77, 11, 15)
    assert source.metadata["sha256"] == digest(path)
    with pytest.raises(ValueError, match="checksum"):
        load_etopo(path, record, expected_sha256="0" * 64)


@pytest.mark.parametrize("minutes", [[0], [0, 6, 21]])
def test_mock_relief_uses_core_with_dynamic_frames(tmp_path, terrain, monkeypatch, minutes):
    mock_npz(tmp_path / "input", minutes)
    field = read_cma_mock(tmp_path / "input", crs="EPSG:4326", display_width=8)
    original = field.values.values.copy()
    path, record, _values = terrain
    relief = load_etopo(path, record, expected_sha256=digest(path))

    # Self-built vector data: same public loader/manifest path as a local provider.
    gpkg = tmp_path / "base.gpkg"
    gpd.GeoDataFrame(geometry=[box(70, 10, 78, 16)], crs="EPSG:4326").to_file(
        gpkg, layer="country_land", driver="GPKG"
    )
    vector_record = tmp_path / "vector-source.json"
    vector_record.write_text(json.dumps({
        "source_id": "test-vector", "attribution": "Test boundaries",
        "disclaimer": "Test vector disclaimer", "layers": {"country_land": {}},
    }))
    acquisition = tmp_path / "acquisition.json"
    acquisition.write_text("{}")
    basemap = CompositeBasemap(load_basemap(gpkg, vector_record), relief)
    calls = []
    original_crop = type(relief).crop

    def record_crop(self, extent):
        calls.append(extent)
        return original_crop(self, extent)

    monkeypatch.setattr(type(relief), "crop", record_crop)
    config = RenderConfig(
        cmap=ListedColormap(["cyan", "green", "yellow", "red"], name="test-ref"),
        vmin=10, vmax=75, buffer_fraction=0, display_min=10,
        notice="MOCK FORECAST — observations, not a real forecast",
    )
    scene = prepare_scene(field, config, basemap)
    try:
        assert len(calls) == 1
        assert len(scene.axes.images) == 1
        # The known viewport is centred on 74 E, hence local display x is -4..4.
        # A raster left in 70..78 longitude coordinates is entirely off-screen.
        np.testing.assert_allclose(scene.axes.images[0].get_extent(), [-4, 4, 10, 16])
        assert scene.axes.images[0].get_zorder() < scene.mesh.get_zorder()
        assert scene.mesh.get_array().mask[0, 0]
        assert scene.mesh.get_array().mask[0, 1]
        map_path, gif_path = tmp_path / "map.png", tmp_path / "animation.gif"
        render_frame(scene, field, 0).savefig(map_path)
        write_animation(scene, field, gif_path)
        assert len(calls) == 1
        np.testing.assert_array_equal(field.values.values, original)
        checks = inspect_outputs(
            map_path, gif_path, len(minutes), expected_duration_ms=500,
            colorbar_box=axes_pixel_box(scene.figure, scene.colorbar.ax),
        )
        manifest = write_manifest(
            field, scene, provider="ocha", basemap=basemap,
            source_record_path=vector_record, acquisition_record_path=acquisition,
            outputs={"map": map_path, "animation": gif_path, "manifest": tmp_path / "run.json"},
            command=["test"], fps=2, checks=checks,
        )
    finally:
        plt.close(scene.figure)
    with Image.open(gif_path) as gif:
        assert gif.n_frames == len(minutes)
    assert manifest["input"]["mock"] and not manifest["input"]["synthetic"]
    assert manifest["input"]["fixture"] is None
    assert manifest["basemap"]["raster"]["sha256"] == digest(path)
    assert manifest["render"]["display_min"] == 10
    assert len(manifest["time"]["frames"]) == len(minutes)
    assert any(c["name"] == "static_colorbar_across_gif" for c in checks)
