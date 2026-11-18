"""Small physical-boundary checks; no model weights or inference backend."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
import yaml

from interfaces.errors import ConstraintError
from interfaces.fields import Forecast, Observations
from interfaces.model import (
    DeclaredModelAdapter,
    InputRequirements,
    ModelCapabilities,
    OutputSpecification,
)
from utils.model_contract import utc, validate_field, validate_forecast, validate_input

ROOT = Path(__file__).resolve().parents[2]


class Declaration(DeclaredModelAdapter):
    def prepare(self, observations):
        raise NotImplementedError

    def predict(self, prepared, *, weights_path):
        raise NotImplementedError


@pytest.mark.parametrize(
    "path",
    sorted((ROOT / "contracts/models").glob("*/*.json")),
    ids=lambda p: f"{p.parent.name}/{p.stem}",
)
def test_queries_and_packaged_metadata(path):
    adapter = type("Declared", (Declaration,), {"declaration_path": path})()
    identity, weights = adapter.capabilities(), adapter.weights()
    assert (identity.data_adapter_id, identity.id) == (path.parent.name, path.stem)
    adapter.input_requirements()
    adapter.output_specification()
    directory = ROOT / "adapters" / path.parent.name / path.stem
    runtime = yaml.safe_load((directory / "runtime.yaml").read_text())
    assert runtime["id"] == identity.id
    assert runtime["weights"]["sha256"] == weights.sha256
    assert runtime["weights"]["file"] == weights.filename
    assert str(path.relative_to(ROOT)) in (directory / "Dockerfile").read_text()


@pytest.fixture
def observations():
    values = np.zeros((2, 3, 4), dtype=np.float32)
    return Observations(
        "example",
        "rain",
        "mm/h",
        values,
        ("2026-01-01T00:00:00Z", "2026-01-01T00:07:00Z"),
        {
            "crs": "EPSG:4326",
            "axis_units": "degrees",
            "x": np.arange(4),
            "y": np.arange(3),
        },
        np.zeros_like(values, dtype=bool),
    )


def test_validate_input_rejects_incompatible_fields(observations):
    requirements = InputRequirements("rain", "mm/h", 2, 7, (3, 4), "float32", False)
    identity = ModelCapabilities(
        "example", "1.0.0", "example", "precip-nowcast.v1", "reference"
    )
    validate_input(observations, requirements, identity)
    for bad in (
        replace(observations, source="wrong"),
        replace(observations, units="dBZ"),
        replace(
            observations,
            values=observations.values[:1],
            mask=observations.mask[:1],
            times=observations.times[:1],
        ),
        replace(observations, times=(observations.times[0], "2026-01-01T00:08:00Z")),
        replace(observations, mask=np.ones_like(observations.mask)),
        replace(observations, values=observations.values.astype(np.float64)),
    ):
        with pytest.raises(ConstraintError):
            validate_input(bad, requirements, identity)


def test_masks_and_explicit_utc(observations):
    values = observations.values.copy()
    values[0, 0, 0] = np.nan
    with pytest.raises(ConstraintError, match="non-finite"):
        validate_field(replace(observations, values=values))
    validate_field(replace(observations, values=values, mask=~np.isfinite(values)))
    for stamp in ("2026-01-01T00:00:00", "2026-01-01T01:00:00+01:00"):
        with pytest.raises(ConstraintError, match="UTC"):
            utc(stamp)


def test_forecast_uses_own_leads_and_grid(observations):
    leads = (3, 11)
    initial = observations.times[-1]
    times = tuple((utc(initial) + timedelta(minutes=n)).isoformat() for n in leads)
    forecast = Forecast(
        "rain",
        "mm/h",
        observations.values.copy(),
        times,
        observations.grid,
        observations.mask.copy(),
        initial,
        leads,
    )
    spec = OutputSpecification("rain", "mm/h", leads, (3, 4), "float32", False, "input")
    validate_forecast(forecast, spec, observations)
    for bad in (
        replace(forecast, units="raw"),
        replace(forecast, lead_minutes=(3, 12)),
        replace(forecast, init_time=observations.times[0]),
        replace(forecast, grid={**forecast.grid, "x": np.arange(4) + 1}),
    ):
        with pytest.raises(ConstraintError):
            validate_forecast(bad, spec, observations)
    validate_forecast(
        replace(forecast, grid={**forecast.grid, "x": np.arange(4) + 1}),
        replace(spec, grid="explicit"),
        observations,
    )
