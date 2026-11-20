import json
import sys
from datetime import datetime, timedelta

import numpy as np
import pytest
from test_stored_fields import fixture_forecast, fixture_window
from test_stored_fields import run as run

from diagnostics.__main__ import main
from tests.field_fixtures import forecast
from utils.stored_fields import StoredFields, add_forecast, create_run


@pytest.mark.parametrize(
    "missing,metric,expected", [(True, "rmse", "partial"), (False, "unknown", "failed")]
)
def test_diagnostics_retains_incomplete_attempt_and_forecasts(
    run, tmp_path, monkeypatch, missing, metric, expected
):
    values = np.full((20, 4, 4), np.nan if missing else 1.0, np.float32)
    add_forecast(run, "wadepre", "0.1.0", "a" * 64, fixture_forecast(values))
    request = tmp_path / "diagnostics.json"
    request.write_text(
        json.dumps(
            {
                "version": 2,
                "run": str(run),
                "metrics": [{"family": "continuous", "items": [metric]}],
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["diagnostics", "--config", str(request)])
    assert main() == 1
    manifest = StoredFields(run).manifest
    assert manifest["status"] == "partial"
    assert "wadepre" in manifest["forecasts"]
    attempt = next(
        item for item in manifest["diagnostics"] if item["kind"] == "verification"
    )
    report = manifest["diagnostics"][-1]
    assert report["kind"] == "report" and report["status"] == "partial"
    assert report["attempt_ids"] == [attempt["id"]]
    assert not (run / report["entry"]).exists()
    assert attempt["status"] == expected
    summary = json.loads((run / attempt["path"] / "summary.json").read_text())
    assert summary["status"] == expected
    if missing:
        assert summary["unavailable"] == 20
        assert summary["ran"] == 0


def test_diagnostics_accepts_nib_run_v2_batch(tmp_path, monkeypatch):
    batch = tmp_path / "batch"
    batch.mkdir()
    samples_root = batch / "samples"
    samples_root.mkdir()
    grid = {
        "x": np.arange(4) * 3000.0,
        "y": np.arange(4) * 3000.0,
        "crs": "EPSG:3857",
        "axis_units": "m",
    }
    samples = []
    first_manifest = None
    for index in range(2):
        window = fixture_window()
        shift = timedelta(minutes=6 * index)

        def offset(value):
            return (
                (datetime.fromisoformat(value.replace("Z", "+00:00")) + shift)
                .isoformat()
                .replace("+00:00", "Z")
            )

        window["context_times"] = [offset(value) for value in window["context_times"]]
        window["target_times"] = [offset(value) for value in window["target_times"]]
        window["context_start_time"] = window["context_times"][0]
        window["init_time"] = window["context_times"][-1]
        window["sample_id"] = f"{window['event_id']}@{window['context_start_time']}"
        child = create_run(
            samples_root, f"{index:03d}", window, grid, atlas_revision="test"
        )
        add_forecast(
            child,
            "wadepre",
            "0.1.0",
            "a" * 64,
            forecast(
                np.zeros((20, 4, 4), np.float32),
                grid=grid,
                init_time=window["init_time"],
                leads=tuple(range(5, 101, 5)),
                variable="vil",
                units="raw",
            ),
        )
        child_manifest = StoredFields(child).manifest
        first_manifest = first_manifest or child_manifest
        samples.append(
            {
                "sample_id": window["sample_id"],
                "event_id": window["event_id"],
                "init_time": window["init_time"],
                "path": f"samples/{index:03d}",
            }
        )

    (batch / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "nib.run.v2",
                "id": "batch-run",
                "batch": True,
                "status": "ingested",
                "sample_count": len(samples),
                "samples": samples,
                "sample": first_manifest["sample"],
                "data": first_manifest["data"],
                "observations": first_manifest["observations"],
                "forecasts": first_manifest["forecasts"],
                "diagnostics": [],
            }
        )
    )
    request = tmp_path / "diagnostics.json"
    request.write_text(
        json.dumps(
            {
                "version": 2,
                "run": str(batch),
                "metrics": [{"family": "continuous", "items": ["mae"]}],
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["diagnostics", "--config", str(request)])

    assert main() == 0
    manifest = StoredFields(batch).manifest
    assert manifest["schema"] == "nib.run.v2"
    assert manifest["status"] == "succeeded"
    attempt = next(
        item for item in manifest["diagnostics"] if item["kind"] == "verification"
    )
    report = manifest["diagnostics"][-1]
    assert attempt["status"] == "succeeded"
    assert report["kind"] == "report" and report["status"] == "succeeded"
    assert report["attempt_ids"] == [attempt["id"]]
    summary = json.loads((batch / attempt["path"] / "summary.json").read_text())
    assert summary["status"] == "succeeded"
    assert summary["ran"] == 40  # Two samples, 20 leads each.
