"""Stored-run verification rejects incompatible physical fields before scoring."""

import json

import numpy as np
import pytest
from nib_verify.access.stored import RunAccess
from test_stored_fields import fixture_forecast, fixture_window
from test_stored_fields import run as run

from diagnostics.verification_run import verification, verification_batch
from tests.field_fixtures import forecast
from utils.model_contract import utc
from utils.stored_fields import StoredFields, add_forecast, create_run, save_manifest

METRICS = [{"family": "continuous", "items": ["mae"]}]


def make_run(root, name, mismatch=None):
    window = fixture_window()
    window.update(
        context=np.zeros((6, 3, 3), np.float32),
        target=np.ones((2, 3, 3), np.float32),
        target_times=window["target_times"][:2],
    )
    grid = {
        "x": np.arange(3, dtype=float),
        "y": np.arange(3, dtype=float),
        "crs": "EPSG:4326",
        "axis_units": "degrees",
    }
    directory = create_run(root, name, window, grid, atlas_revision="test")
    output_grid = dict(grid)
    shape = (2, 3, 3)
    if mismatch == "grid":
        output_grid.update(x=np.array([0.0, 2.0]), y=np.array([0.0, 2.0]))
        shape = (2, 2, 2)
    elif mismatch == "crs":
        output_grid["crs"] = "EPSG:3857"
    elif mismatch == "axis_units":
        output_grid["axis_units"] = "m"
    result = forecast(
        np.ones(shape, np.float32),
        grid=output_grid,
        init_time=window["init_time"],
        leads=(5, 10),
        variable="rain" if mismatch == "variable" else "vil",
        units="mm/h" if mismatch == "unit" else "raw",
    )
    add_forecast(directory, "model", "1", "a" * 64, result)
    return directory


@pytest.mark.parametrize("mismatch", ["grid", "crs", "axis_units", "variable", "unit"])
@pytest.mark.parametrize("batch", [False, True])
def test_incompatible_pair_is_rejected_before_report(tmp_path, mismatch, batch):
    directory = make_run(tmp_path, "bad", mismatch)
    if batch:
        make_run(tmp_path, "good")
        save_manifest(tmp_path, {"id": "parent"})

        def operation():
            return verification_batch(
                tmp_path, [{"path": "good"}, {"path": "bad"}], METRICS
            )

        report_root = tmp_path
    else:

        def operation():
            return verification(directory, METRICS)

        report_root = directory
    with pytest.raises(ValueError, match="stored verification requires"):
        operation()
    assert not (report_root / "diagnostics").exists()
    access = RunAccess(directory)
    with pytest.raises(ValueError, match="stored verification requires"):
        access.get_prediction(
            "model", utc(access.fields.manifest["sample"]["init_time"]), 5
        )


def test_capabilities_report_forecasts_own_units(tmp_path):
    directory = make_run(tmp_path, "different-units", "unit")
    assert RunAccess(directory).capabilities().units == {
        "sevir": "raw",
        "model": "mm/h",
    }


def test_matching_geographic_pair_scores_without_resampling(tmp_path):
    directory = make_run(tmp_path, "matching")
    attempt = verification(directory, METRICS)
    assert attempt["status"] == "succeeded"
    config = json.loads((directory / attempt["path"] / "config.json").read_text())
    assert config["members"][0]["variable"] == "vil"
    assert config["members"][0]["unit"] == "raw"


def test_legacy_forecasts_read_but_new_writes_are_rejected(run):
    add_forecast(
        run,
        "old",
        "1",
        "a" * 64,
        fixture_forecast(np.ones((2, 4, 4), np.float32), (5, 10)),
    )
    manifest = StoredFields(run).manifest
    manifest["schema"] = "nib.run.v1"
    manifest["data"].pop("cadence_minutes")
    save_manifest(run, manifest)
    before = (run / "manifest.json").read_bytes()
    access = StoredFields(run)
    assert access.get_prediction("old", utc(manifest["sample"]["init_time"]), 5)[
        "data"
    ].shape == (4, 4)
    with pytest.raises(ValueError, match="legacy runs are read-only"):
        add_forecast(
            run,
            "new",
            "1",
            "a" * 64,
            fixture_forecast(np.ones((2, 4, 4), np.float32), (3, 11)),
        )
    assert (run / "manifest.json").read_bytes() == before
    assert not (run / "forecasts/new").exists()
