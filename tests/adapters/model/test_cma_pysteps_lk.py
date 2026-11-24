"""The CMA pySTEPS baseline follows its Atlas declaration end to end."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from adapters.cma_radar.pysteps_lk import adapter as module
from interfaces.errors import ConstraintError, WeightsNotReady
from interfaces.fields import Observations

MANIFEST = Path(module.__file__).with_name("algorithm-manifest.json")


def observations(shape=(3, 128, 128), cadence=6):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times = tuple(
        (start + timedelta(minutes=cadence * index)).isoformat().replace("+00:00", "Z")
        for index in range(shape[0])
    )
    return Observations(
        source="cma_radar",
        variable_id="CREF",
        units="dBZ",
        values=np.full(shape, 20.0, dtype=np.float32),
        times=times,
        grid={
            "crs": "+proj=laea",
            "axis_units": "m",
            "y": np.arange(shape[1], dtype=np.float64),
            "x": np.arange(shape[2], dtype=np.float64),
        },
        mask=np.zeros(shape, dtype=bool),
    )


def test_declaration_matches_the_contract():
    capabilities = module.ADAPTER.capabilities()
    assert (capabilities.id, capabilities.version) == ("pysteps_lk", "0.1.0")
    assert capabilities.data_adapter_id == "cma_radar"
    requirements = module.ADAPTER.input_requirements()
    assert requirements.context_frames == 3
    assert requirements.spatial == (128, 128)
    output = module.ADAPTER.output_specification()
    assert output.lead_minutes == tuple(range(6, 121, 6))
    assert output.grid == "input"


def test_prepare_rejects_wrong_cadence_and_grid():
    with pytest.raises(ConstraintError, match="cadence"):
        module.ADAPTER.prepare(observations(cadence=5))
    with pytest.raises(ConstraintError, match="history length or grid shape"):
        module.ADAPTER.prepare(observations(shape=(3, 64, 128)))


def test_predict_requires_the_pinned_manifest(tmp_path):
    with pytest.raises(WeightsNotReady, match="checksum mismatch"):
        module.ADAPTER.predict(
            module.ADAPTER.prepare(observations()),
            weights_path=str(tmp_path / "missing.json"),
        )


def test_predict_returns_declared_leads(monkeypatch):
    def fake_predict(values, *, lead_steps, flow_frames):
        assert flow_frames == 3
        return np.zeros((lead_steps, 128, 128), dtype=np.float32), np.zeros(
            (2, 128, 128), dtype=np.float32
        )

    monkeypatch.setattr(module, "pysteps_predict", fake_predict)
    forecast = module.ADAPTER.predict(
        module.ADAPTER.prepare(observations()), weights_path=str(MANIFEST)
    )
    assert forecast.values.shape == (20, 128, 128)
    assert forecast.lead_minutes == tuple(range(6, 121, 6))
    assert forecast.init_time == "2026-01-01T00:12:00Z"
    assert forecast.times[-1] == "2026-01-01T02:12:00Z"
