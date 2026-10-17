"""The four-method handoff preserves native CMA values and spatial identity."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr
from pyproj import CRS, Transformer

from diagnostics.run_source import RunSource, RunSourceError
from utils.stored_fields import StoredFields, add_forecast, create_run, save_manifest

INIT = datetime(2026, 7, 2, 1, tzinfo=UTC)
METRICS = [
    {"family": "continuous", "items": ["mae", "rmse"]},
    {"family": "yes_or_no", "items": ["ts"], "levels": [10, 30]},
]


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def make_run(root, *, run_id="different-run", geographic=False):
    start = INIT - timedelta(minutes=12)
    times = [stamp(start + timedelta(minutes=6 * i)) for i in range(7)]
    grid = {
        "x": np.arange(5, dtype=float) * (0.1 if geographic else 1700)
        + (115 if geographic else -10000),
        "y": np.arange(3, dtype=float) * (0.2 if geographic else 2300)
        + (30 if geographic else -20000),
        "crs": (
            "EPSG:4326"
            if geographic
            else "+proj=laea +lat_0=29 +lon_0=115 +datum=WGS84 +units=m"
        ),
        "axis_units": "degrees" if geographic else "m",
    }
    values = np.arange(4 * 3 * 5, dtype=np.float32).reshape(4, 3, 5)
    values[0, 1, 2] = np.nan
    values[-1] = np.nan
    window = {
        "data_id": "cma_radar",
        "variable": "CREF",
        "unit": "dBZ",
        "cadence_minutes": 6,
        "context": values[:3],
        "target": values,
        "context_times": times[:3],
        "target_times": times[3:],
        "sample_id": "not-the-production-sample",
        "event_id": "a-day",
        "row": 2,
        "context_start_time": times[0],
        "init_time": times[2],
    }
    directory = create_run(root, run_id, window, grid, atlas_revision="test")
    for model in ("algorithm-one", "algorithm-two"):
        add_forecast(directory, model, "test", "a" * 64, values + 2, [6, 12, 18, 24])
    return directory


@pytest.fixture
def directory(tmp_path):
    return make_run(tmp_path)


def source(directory, **kwargs):
    return RunSource(directory, metrics=deepcopy(METRICS), version=3, **kwargs)


def group(access):
    return access.get_group(access.list_groups()[0])


def test_metadata_and_names_come_from_run_not_demo_constants(directory):
    access = source(directory)
    spec = group(access)
    assert spec["version"] == 3 and spec["status"] == "active"
    assert spec["time"] == {
        "timezone": "UTC",
        "init_from": stamp(INIT),
        "init_to": stamp(INIT),
    }
    assert spec["truth"]["variable"] == "CREF"
    assert spec["truth"]["cadence_minutes"] == 6
    assert spec["space"]["coordinate_units"] == "m"
    assert spec["space"]["resolution"] == [1700, 2300]
    assert spec["space"]["extent"] == [-10000, -20000, -3200, -15400]
    assert "bbox" not in spec["space"]
    assert [f["model_id"] for f in spec["forecasts"]] == [
        "algorithm-one",
        "algorithm-two",
    ]
    assert all(
        f["variable"] == "CREF" and f["leads_minutes"] == [6, 12, 18, 24]
        for f in spec["forecasts"]
    )
    assert all("different-run" in f["name"] for f in spec["forecasts"])
    assert spec["truth"]["name"] not in [f["name"] for f in spec["forecasts"]]
    assert spec["metrics"] == METRICS
    json.dumps(spec, allow_nan=False)


def test_xarray_contains_native_grid_geographic_coordinates_and_crs(directory):
    access = source(directory)
    spec = group(access)
    valid = INIT + timedelta(minutes=6)
    truth = access.read_truth(spec["truth"]["name"], stamp(valid))
    forecast = access.read_forecast(spec["forecasts"][0]["name"], stamp(INIT), 6)
    assert isinstance(truth, xr.DataArray) and isinstance(truth.values, np.ndarray)
    assert truth.dims == ("y", "x") and truth.shape == (3, 5)
    assert "time" not in truth.dims
    assert truth.attrs["units"] == forecast.attrs["units"] == "dBZ"
    assert truth.attrs["variable"] == forecast.attrs["variable"] == "CREF"
    assert truth.attrs["valid_time"] == forecast.attrs["valid_time"] == stamp(valid)
    assert truth.attrs["grid_mapping"] == "spatial_ref"
    crs = CRS.from_wkt(truth.spatial_ref.attrs["crs_wkt"])
    assert crs.equals(CRS.from_user_input(spec["space"]["projection"]))
    np.testing.assert_allclose(
        forecast.values - truth.values, np.where(np.isnan(truth.values), np.nan, 2)
    )
    assert np.isnan(truth.values[1, 2])
    expected_lon, expected_lat = Transformer.from_crs(
        crs, "EPSG:4326", always_xy=True
    ).transform(*np.meshgrid(truth.x.values, truth.y.values))
    np.testing.assert_allclose(truth.lon.values, expected_lon)
    np.testing.assert_allclose(truth.lat.values, expected_lat)
    assert truth.lon.dims == truth.lat.dims == ("y", "x")
    assert truth.lon.attrs["units"] == "degrees_east"
    assert truth.x.attrs["units"] == truth.y.attrs["units"] == "m"
    assert truth.x.attrs["standard_name"] == "projection_x_coordinate"
    np.testing.assert_array_equal(truth.x.values, np.arange(5) * 1700 - 10000)


def test_geographic_cma_grid_keeps_one_dimensional_coordinates(tmp_path):
    access = source(make_run(tmp_path, geographic=True))
    spec = group(access)
    data = access.read_truth(spec["truth"]["name"], stamp(INIT))
    assert data.dims == ("lat", "lon")
    assert data.lat.ndim == data.lon.ndim == 1
    assert spec["space"]["projection"] == "EPSG:4326"
    assert spec["space"]["coordinate_units"] == "degrees"
    assert "bbox" in spec["space"] and "extent" not in spec["space"]


def test_cached_cubes_and_return_values_are_independent(directory):
    access = source(directory)
    spec = group(access)
    # Two observed cubes and two selected forecast cubes, once each.
    assert access.file_reads == 4
    name = spec["forecasts"][0]["name"]
    first = access.read_forecast(name, stamp(INIT), 6)
    expected = first.copy(deep=True)
    first.values[:] = -999
    first.coords["x"] = first.x.values + 100
    first.lon.values[:] = 0
    first.spatial_ref.attrs["crs_wkt"] = "changed"
    spec["metrics"][0]["items"].clear()
    spec["forecasts"].clear()
    xr.testing.assert_identical(access.read_forecast(name, stamp(INIT), 6), expected)
    for lead in (6, 12, 18):
        access.read_forecast(name, stamp(INIT), lead)
    assert access.file_reads == 4
    assert group(access)["metrics"] == METRICS
    assert json.loads((directory / "manifest.json").read_text())["id"] == directory.name


def test_selected_models_and_namespaced_runs(directory, tmp_path):
    access = source(directory, models=["algorithm-two"])
    spec = group(access)
    assert [f["model_id"] for f in spec["forecasts"]] == ["algorithm-two"]
    assert access.file_reads == 3
    other = source(make_run(tmp_path, run_id="another-run"))
    assert group(other)["truth"]["name"] != spec["truth"]["name"]
    assert group(other)["forecasts"][1]["name"] != spec["forecasts"][0]["name"]


def test_offset_times_are_normalized_and_exact(directory):
    access = source(directory)
    spec = group(access)
    offset = INIT.astimezone(timezone(timedelta(hours=8))).isoformat()
    first = access.read_forecast(spec["forecasts"][0]["name"], offset, 12)
    assert first.attrs["valid_time"] == stamp(INIT + timedelta(minutes=12))


@pytest.mark.parametrize("value", ["2026-07-02T01:00:00", "not-a-time", None, INIT])
def test_bad_times_use_one_exception(directory, value):
    access = source(directory)
    spec = group(access)
    with pytest.raises(RunSourceError, match="Invalid time"):
        access.read_truth(spec["truth"]["name"], value)
    with pytest.raises(RunSourceError, match="Invalid time"):
        access.read_forecast(spec["forecasts"][0]["name"], value, 6)


@pytest.mark.parametrize("lead", [True, 6.0, "6", None, 0, -6, 7])
def test_invalid_or_absent_leads(directory, lead):
    access = source(directory)
    with pytest.raises(RunSourceError):
        access.read_forecast(group(access)["forecasts"][0]["name"], stamp(INIT), lead)


def test_unknown_names_initializations_and_all_missing_fields(directory):
    access = source(directory)
    spec = group(access)
    with pytest.raises(RunSourceError, match="Unknown verification group"):
        access.get_group("missing")
    with pytest.raises(RunSourceError, match="Unknown truth"):
        access.read_truth("cma_radar", stamp(INIT))
    with pytest.raises(RunSourceError, match="Unknown forecast"):
        access.read_forecast("algorithm-one", stamp(INIT), 6)
    with pytest.raises(RunSourceError, match="no field"):
        access.read_truth(spec["truth"]["name"], stamp(INIT + timedelta(days=1)))
    with pytest.raises(RunSourceError, match="no initialization"):
        access.read_forecast(
            spec["forecasts"][0]["name"], stamp(INIT + timedelta(minutes=6)), 6
        )
    for read in (
        lambda: access.read_truth(
            spec["truth"]["name"], stamp(INIT + timedelta(minutes=24))
        ),
        lambda: access.read_forecast(spec["forecasts"][0]["name"], stamp(INIT), 24),
    ):
        with pytest.raises(RunSourceError, match="entirely missing"):
            read()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": True},
        {"version": 0},
        {"group_id": " "},
        {"models": []},
        {"models": ["unknown"]},
        {"models": ["algorithm-one"] * 2},
        {"metrics": []},
        {"metrics": [{"family": "unknown", "items": ["mae"]}]},
        {"metrics": [{"family": "continuous", "items": ["corr"]}]},
        {"metrics": [{"family": "yes_or_no", "items": ["ts"]}]},
        {
            "metrics": [
                {"family": "continuous", "items": ["mae"], "levels": [float("nan")]}
            ]
        },
        {
            "metrics": [
                {
                    "family": "spatial",
                    "items": ["fss"],
                    "levels": [10],
                    "half_windows": [1.5],
                }
            ]
        },
        {"metrics": [{"family": "continuous", "items": ["mae"], "unit": "mm"}]},
    ],
)
def test_invalid_configuration_is_rejected(directory, kwargs):
    options = {"metrics": deepcopy(METRICS), "version": 3, **kwargs}
    with pytest.raises(RunSourceError):
        RunSource(directory, **options)


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda spec: spec.update(path="../outside.npz"), "contained"),
        (lambda spec: spec.update(sha256="0" * 64), "checksum"),
        (lambda spec: spec["grid"].update(crs="EPSG:3857"), "share grid"),
        (lambda spec: spec.update(lead_minutes=[6, 12, 18, True]), "positive integers"),
    ],
)
def test_corrupt_artifacts_and_mismatched_grids_are_not_silently_fixed(
    directory, change, reason
):
    fields = StoredFields(directory)
    change(fields.manifest["forecasts"]["algorithm-one"])
    save_manifest(directory, fields.manifest)
    with pytest.raises(RunSourceError, match=reason):
        source(directory)


def test_sevir_new_interface_is_explicitly_deferred(directory):
    fields = StoredFields(directory)
    fields.manifest["data"].update(id="sevir", cadence_minutes=5)
    save_manifest(directory, fields.manifest)
    with pytest.raises(RunSourceError, match="CMA Radar runs only"):
        source(directory)


@pytest.mark.parametrize("kind", ["target", "forecast"])
def test_invalid_crs_uses_uniform_error(directory, kind):
    fields = StoredFields(directory)
    descriptor = (
        fields.manifest["observations"]["target"]
        if kind == "target"
        else fields.manifest["forecasts"]["algorithm-one"]
    )
    descriptor["grid"]["crs"] = "not-a-crs"
    save_manifest(directory, fields.manifest)
    with pytest.raises(RunSourceError, match="Cannot open diagnostic run") as error:
        source(directory)
    assert error.value.__cause__ is not None


@pytest.mark.parametrize("bad_field", ["sha256", "times"])
def test_forecast_cannot_alias_cached_truth_with_conflicting_descriptor(
    directory, bad_field
):
    fields = StoredFields(directory)
    truth = fields.manifest["observations"]["target"]
    forecast = fields.manifest["forecasts"]["algorithm-one"]
    forecast["path"] = truth["path"]
    forecast["sha256"] = truth["sha256"]
    if bad_field == "sha256":
        forecast["sha256"] = "0" * 64
    else:
        forecast["times"][0] = "2030-01-01T00:00:00Z"
    save_manifest(directory, fields.manifest)
    with pytest.raises(RunSourceError, match="conflicting.*descriptor"):
        source(directory)


def test_missing_directory_has_actionable_uniform_error(tmp_path):
    with pytest.raises(RunSourceError, match="Cannot open diagnostic run"):
        source(tmp_path / "missing")
