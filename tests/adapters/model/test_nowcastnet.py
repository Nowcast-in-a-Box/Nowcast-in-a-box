"""NowcastNet preparation rejects incompatible fields without resizing or padding."""

import numpy as np
import pytest

from adapters.sevir.nowcastnet.adapter import ADAPTER
from interfaces.errors import ConstraintError, WeightsNotReady
from tests.field_fixtures import observations


def test_output_specification():
    spec = ADAPTER.output_specification()
    assert spec.spatial == (128, 128)
    assert spec.lead_minutes == tuple(range(5, 101, 5))
    assert (spec.variable_id, spec.units, spec.dtype) == ("vil", "raw", "float32")


def test_prepare_preserves_observations_and_scales_private_batch():
    obs = observations()
    before = obs.values.copy()
    batch, physical = ADAPTER.prepare(obs)
    assert physical is obs
    assert batch.shape == (1, 5, 128, 128)
    assert batch.dtype == np.float32 and batch.flags.c_contiguous
    np.testing.assert_array_equal(batch, 0.5)
    np.testing.assert_array_equal(obs.values, before)


@pytest.mark.parametrize("shape", [(4, 128, 128), (6, 128, 128), (5, 64, 128)])
def test_prepare_does_not_pad_trim_or_resize(shape):
    with pytest.raises(ConstraintError, match="history length or grid shape"):
        ADAPTER.prepare(observations(shape=shape))


def test_six_minute_observations_are_rejected():
    with pytest.raises(ConstraintError, match="cadence"):
        ADAPTER.prepare(observations(cadence=6))


def test_predict_without_weights(tmp_path):
    with pytest.raises(WeightsNotReady, match="offline weight checksum"):
        ADAPTER.predict(
            ADAPTER.prepare(observations()), weights_path=str(tmp_path / "missing.onnx")
        )
