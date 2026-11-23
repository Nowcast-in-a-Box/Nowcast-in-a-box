"""WADEPre cross-domain CREF demo using the local SEVIR ONNX weights."""

from datetime import timedelta
from pathlib import Path

import numpy as np

from adapters.sevir.wadepre.adapter import WADEPreAdapter as SEVIRWADEPre
from interfaces.errors import ConstraintError, WeightsNotReady
from interfaces.fields import Forecast
from utils.model_contract import utc, validate_forecast
from utils.weights import file_sha256

NOTICE = (
    "SEVIR pretrained WADEPre cross-domain CREF demo; 5-minute outputs are "
    "resampled to 6-minute leads and forecast skill is not validated."
)

_SOURCE_LEADS = np.arange(5, 101, 5, dtype=np.float32)
_TARGET_LEADS = np.arange(6, 121, 6, dtype=np.float32)


def _resample_leads(values):
    """Linearly map 20 five-minute frames onto the CMA six-minute leads."""
    values = np.asarray(values, dtype=np.float32)
    if values.ndim < 1 or values.shape[0] != len(_SOURCE_LEADS):
        raise ConstraintError(f"unexpected WADEPre lead axis: {values.shape}")
    flat = values.reshape(len(_SOURCE_LEADS), -1)
    resampled = np.empty((len(_TARGET_LEADS), flat.shape[1]), dtype=np.float32)
    for index in range(flat.shape[1]):
        resampled[:, index] = np.interp(
            _TARGET_LEADS, _SOURCE_LEADS, flat[:, index]
        ).astype(np.float32)
    return resampled.reshape((len(_TARGET_LEADS),) + values.shape[1:])


class WADEPreAdapter(SEVIRWADEPre):
    declaration_path = (
        Path(__file__).resolve().parents[3]
        / "atlas/contracts/models/cma_radar/wadepre.json"
    )
    scale = 75.0

    def validate_input(self, observations):
        super().validate_input(observations)
        if (observations.values < 0).any():
            raise ConstraintError(
                "CMA input has negative pixels; choose another window/region"
            )

    def prepare(self, observations):
        self.validate_input(observations)
        batch = np.ascontiguousarray(
            np.clip(observations.values, 0, 75)[None] / self.scale,
            dtype=np.float32,
        )
        return batch, observations

    def predict(self, prepared, *, weights_path):
        batch, observations = prepared
        path = Path(weights_path)
        if not path.is_file() or file_sha256(path) != self.weights().sha256:
            raise WeightsNotReady(
                "offline weight checksum mismatch; prepare pinned weights first"
            )
        import onnxruntime as ort

        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        result = session.run(["refined_out"], {"input_sequence": batch})[0]
        output = self.output_specification()
        expected = (1, len(_SOURCE_LEADS), *output.spatial)
        if result.shape != expected:
            raise ConstraintError(f"unexpected backend output shape: {result.shape}")
        values = np.clip(_resample_leads(result[0]) * self.scale, 0, 75)
        values = np.ascontiguousarray(values, dtype=np.float32)
        initial = observations.times[-1]
        forecast = Forecast(
            output.variable_id,
            output.units,
            values,
            tuple(
                (utc(initial) + timedelta(minutes=v)).isoformat().replace("+00:00", "Z")
                for v in output.lead_minutes
            ),
            observations.grid,
            ~np.isfinite(values),
            initial,
            output.lead_minutes,
        )
        validate_forecast(forecast, output, observations)
        return forecast


ADAPTER = WADEPreAdapter()
