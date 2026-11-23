"""CMA WADEPre demo: declaration, dBZ scaling and 5-to-6 minute resampling."""

import numpy as np

from adapters.cma_radar.wadepre.adapter import ADAPTER, _resample_leads
from tests.field_fixtures import mock_onnx, observations


def test_prepare_scales_dbz_and_resample_preserves_order():
    source = observations(
        source="cma_radar", variable="CREF", units="dBZ", cadence=6, value=37.5
    )
    batch, _ = ADAPTER.prepare(source)
    assert batch.shape == (1, 5, 128, 128)
    assert np.allclose(batch, 0.5)

    values = np.broadcast_to(
        np.arange(5, 101, 5, dtype=np.float32)[:, None, None], (20, 2, 2)
    )
    resampled = _resample_leads(values)
    assert resampled.shape == (20, 2, 2)
    assert np.allclose(
        resampled[:, 0, 0],
        [
            6,
            12,
            18,
            24,
            30,
            36,
            42,
            48,
            54,
            60,
            66,
            72,
            78,
            84,
            90,
            96,
            100,
            100,
            100,
            100,
        ],
    )
    assert resampled[-1, 0, 0] == 100


def test_predict_returns_twenty_six_minute_leads(monkeypatch, tmp_path):
    source = observations(
        source="cma_radar", variable="CREF", units="dBZ", cadence=6, value=37.5
    )
    batch, prepared = ADAPTER.prepare(source)
    output = np.full((1, 20, 128, 128), 0.5, dtype=np.float32)
    path, _, session = mock_onnx(monkeypatch, tmp_path, ADAPTER, output)

    forecast = ADAPTER.predict((batch, prepared), weights_path=str(path))

    session.run.assert_called_once()
    assert forecast.values.shape == (20, 128, 128)
    assert forecast.lead_minutes == tuple(range(6, 121, 6))
    assert forecast.times[0] == "2026-07-01T00:30:00Z"
    assert np.allclose(forecast.values, 37.5)
