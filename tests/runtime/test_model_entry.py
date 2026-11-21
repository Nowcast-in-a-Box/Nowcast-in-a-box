"""One runtime entry handles independently declared models without real weights."""

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from interfaces.model import DeclaredModelAdapter
from tests.field_fixtures import forecast, observations
from utils.stored_fields import StoredFields, create_run, sha256


class FakeModel(DeclaredModelAdapter):
    def prepare(self, observations):
        self.validate_input(observations)
        return observations

    def predict(self, prepared, *, weights_path):
        spec = self.output_specification()
        assert sha256(weights_path) == self.weights().sha256
        return forecast(
            np.full((len(spec.lead_minutes), *spec.spatial), 2.5, np.float32),
            grid=prepared.grid,
            init_time=prepared.times[-1],
            leads=spec.lead_minutes,
            variable=spec.variable_id,
            units=spec.units,
        )


@pytest.fixture(
    params=[(2, 5, (5, 10), (3, 5)), (3, 10, (10, 30, 60), (4, 6))],
    ids=["short-history", "long-history"],
)
def model_case(request, tmp_path, monkeypatch):
    history, cadence, leads, spatial = request.param
    model_id = f"fake_{history}"
    weight = tmp_path / "fixture.weights"
    weight.write_bytes(b"small fake model weights")
    declaration = {
        "schema": "nib.model.v1",
        "id": model_id,
        "version": "test-1",
        "data_adapter_id": "fixture_data",
        "task": "test",
        "status": "reference",
        "weights": {
            "status": "pinned",
            "download_url": None,
            "sha256": sha256(weight),
            "filename": weight.name,
            "hf": None,
            "artifacts_relpath": weight.name,
        },
        "input": {
            "variable_id": "rain",
            "units": "mm/h",
            "context_frames": history,
            "cadence_minutes": cadence,
            "spatial": spatial,
            "dtype": "float32",
            "allow_missing": False,
        },
        "output": {
            "variable_id": "rain",
            "units": "mm/h",
            "lead_minutes": leads,
            "spatial": spatial,
            "dtype": "float32",
            "allow_missing": False,
            "grid": "input",
        },
    }
    declaration_path = tmp_path / "model.json"
    declaration_path.write_text(json.dumps(declaration))
    monkeypatch.setattr(FakeModel, "declaration_path", declaration_path, raising=False)
    adapter = FakeModel()
    obs = observations(
        source="fixture_data",
        variable="rain",
        units="mm/h",
        cadence=cadence,
        shape=(history, *spatial),
    )
    target = forecast(
        np.zeros((len(leads), *spatial), np.float32),
        grid=obs.grid,
        init_time=obs.times[-1],
        leads=leads,
        variable="rain",
        units="mm/h",
    )
    window = {
        "data_id": obs.source,
        "cadence_minutes": cadence,
        "variable": obs.variable_id,
        "unit": obs.units,
        "context": obs.values,
        "context_times": obs.times,
        "target": target.values,
        "target_times": target.times,
        "sample_id": "fixture",
        "event_id": "fixture",
        "row": 0,
        "context_start_time": obs.times[0],
        "init_time": obs.times[-1],
    }
    directory = create_run(tmp_path, "run", window, obs.grid, atlas_revision="test")
    path = Path(__file__).resolve().parents[2] / "adapters/model_runner.py"
    spec = importlib.util.spec_from_file_location("model_entry", path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    monkeypatch.setitem(sys.modules, "fixture_model", SimpleNamespace(ADAPTER=adapter))
    monkeypatch.setenv("NIB_MODEL", model_id)
    monkeypatch.setenv("NIB_ADAPTER_MODULE", "fixture_model")
    monkeypatch.setattr(
        sys, "argv", ["model_run", "--run", str(directory), "--weights", str(weight)]
    )
    return entry, adapter, directory, weight, obs


def test_model_entry_persists_declared_forecast(model_case):
    entry, adapter, directory, weight, obs = model_case
    entry.main()
    access = StoredFields(directory)
    assert access.manifest["schema"] == "nib.run.v2"
    descriptor = access.manifest["forecasts"][adapter.capabilities().id]
    spec = adapter.output_specification()
    assert descriptor["lead_minutes"] == list(spec.lead_minutes)
    assert descriptor["shape"] == [len(spec.lead_minutes), *spec.spatial]
    assert (descriptor["variable"], descriptor["unit"]) == ("rain", "mm/h")
    assert descriptor["init_time"] == obs.times[-1]
    assert descriptor["model_version"] == "test-1"
    assert descriptor["weight_sha256"] == sha256(weight)
    expected = adapter.predict(obs, weights_path=str(weight))
    assert descriptor["times"] == list(expected.times)
    cube = access.cube(descriptor)
    assert cube["values"].dtype == np.float32
    np.testing.assert_array_equal(cube["values"], expected.values)
    np.testing.assert_array_equal(cube["mask"], expected.mask)
    for axis in ("x", "y"):
        np.testing.assert_array_equal(cube[axis], obs.grid[axis])


@pytest.mark.parametrize(
    "stage",
    ["declaration", "weights", "input", "prepare", "predict", "output", "store"],
)
def test_model_entry_records_failure_stage(model_case, monkeypatch, stage):
    entry, adapter, directory, weight, _ = model_case
    predict = Mock(wraps=adapter.predict)
    monkeypatch.setattr(adapter, "predict", predict)
    if stage == "declaration":
        monkeypatch.setenv("NIB_MODEL", "wrong-id")
        message = "identity"
    elif stage == "weights":
        weight.write_bytes(b"wrong checksum")
        message = "offline weight"
    elif stage == "input":
        requirements = replace(adapter.input_requirements(), context_frames=99)
        monkeypatch.setattr(adapter, "input_requirements", lambda: requirements)
        message = "insufficient observation history"
    elif stage in ("prepare", "predict"):
        monkeypatch.setattr(
            adapter, stage, Mock(side_effect=RuntimeError(f"{stage} failed"))
        )
        message = f"{stage} failed"
    elif stage == "output":
        predict.return_value = np.zeros((1, 2, 3), np.float32)
        message = "Forecast"
    else:
        monkeypatch.setattr(
            entry, "add_forecast", Mock(side_effect=OSError("store failed"))
        )
        message = "store failed"
    with pytest.raises((ValueError, RuntimeError, OSError), match=message):
        entry.main()
    manifest = StoredFields(directory).manifest
    assert manifest["status"] == "failed"
    assert manifest["failure_stage"] == stage
    assert message in manifest["error"]
    assert manifest["forecasts"] == {}
    assert (directory / manifest["observations"]["context"]["path"]).is_file()
    if stage in ("declaration", "weights", "input", "prepare"):
        predict.assert_not_called()
