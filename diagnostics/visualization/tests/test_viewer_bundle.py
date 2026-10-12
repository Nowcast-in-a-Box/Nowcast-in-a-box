"""COG output must preserve coordinates, values and forecast time semantics."""

import json

import numpy as np
import pytest
import xarray as xr

rasterio = pytest.importorskip("rasterio")

from nib_visualization.field import VisualizationField  # noqa: E402
from nib_visualization.viewer_bundle import export_field_cogs  # noqa: E402


@pytest.mark.parametrize("reverse_x,reverse_y", [(False, False), (True, True)])
def test_cog_values_centres_and_irregular_times(tmp_path, reverse_x, reverse_y):
    x, y = np.arange(768) * .05 + 70, np.arange(512) * .05 + 15
    data = np.arange(3 * 512 * 768, dtype="f4").reshape(3, 512, 768)
    data[:, 1, 2] = np.nan
    data[:, 2, 2] = 0
    if reverse_x:
        x, data = x[::-1], data[:, :, ::-1]
    if reverse_y:
        y, data = y[::-1], data[:, ::-1]
    original = data.copy()
    field = VisualizationField(xr.DataArray(
        data, dims=("lead_time", "y", "x"),
        coords={"lead_time": np.array([0, 43, 661], dtype="timedelta64[s]"), "x": x, "y": y},
        name="Reflectivity", attrs={"units": "dBZ"},
    ), "EPSG:4326", np.datetime64("2024-07-05T00:00:00"), "instantaneous")
    manifest = export_field_cogs(field, tmp_path)
    assert [f["lead_seconds"] for f in manifest["frames"]] == [0, 43, 661]
    assert manifest["frames"][2]["valid_time"] == "2024-07-05T00:11:01Z"
    for i, frame in enumerate(manifest["frames"]):
        with rasterio.open(tmp_path / frame["url"]) as ds:
            assert ds.crs.to_epsg() == 4326
            assert ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
            assert ds.overviews(1)
            assert np.isnan(ds.nodata)
            for row, col in [(0, 0), (1, 2), (2, 2), (256, 350), (511, 767)]:
                px, py = float(x[col]), float(y[row])
                rr, cc = ds.index(px, py)
                np.testing.assert_allclose(ds.xy(rr, cc), (px, py), atol=1e-9)
                np.testing.assert_equal(ds.read(1)[rr, cc], data[i, row, col])
    np.testing.assert_array_equal(data, original)
    json.dumps(manifest, allow_nan=False)


def test_single_frame_accumulation_retains_interval(tmp_path):
    init = np.datetime64("2024-07-05T00:00:00")
    field = VisualizationField(xr.DataArray(
        np.ones((1, 2, 3), dtype="f4"), dims=("lead_time", "y", "x"),
        coords={"lead_time": np.array([60], dtype="timedelta64[s]"),
                "x": [70., 71., 72.], "y": [15., 16.]},
        name="Rain", attrs={"units": "mm"},
    ), "EPSG:4326", init, "accumulation", interval_start=np.array([init]),
        interval_end=np.array([init + np.timedelta64(60, "s")]))
    manifest = export_field_cogs(field, tmp_path)
    assert len(manifest["frames"]) == 1
    assert manifest["frames"][0]["interval_start"] == "2024-07-05T00:00:00Z"
    assert manifest["frames"][0]["interval_end"] == "2024-07-05T00:01:00Z"


def test_global_terrain_edges_orientation_and_nodata(tmp_path):
    from nib_visualization.basemap import RasterBasemapView
    from nib_visualization.viewer_bundle import export_terrain_cog

    values = np.arange(18 * 36, dtype="f4").reshape(18, 36)
    values[1, 2] = np.nan
    view = RasterBasemapView(values, (-180., 180., -90., 90.), (), .56)
    original = values.copy()
    info = export_terrain_cog(view, tmp_path / "world.tif")
    assert info["bounds"] == [-180., -90., 180., 90.]
    assert info["shape"] == [18, 36]
    with rasterio.open(tmp_path / "world.tif") as ds:
        assert ds.crs.to_epsg() == 4326
        assert ds.xy(0, 0) == (-175., 85.)
        assert ds.xy(17, 35) == (175., -85.)
        np.testing.assert_array_equal(ds.read(1), values[::-1])
    np.testing.assert_array_equal(values, original)
