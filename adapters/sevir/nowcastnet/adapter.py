"""nowcastnet backend. Public field requirements come from the shipped declaration."""

from datetime import timedelta
from pathlib import Path

import numpy as np

from interfaces.errors import ConstraintError, WeightsNotReady
from interfaces.fields import Forecast
from interfaces.model import DeclaredModelAdapter
from utils.model_contract import utc, validate_forecast
from utils.weights import file_sha256


class NowcastNetAdapter(DeclaredModelAdapter):
    scale = 255.0
    declaration_path = (
        Path(__file__).resolve().parents[3]
        / "atlas/contracts/models/sevir/nowcastnet.json"
    )

    def prepare(self, observations):
        self.validate_input(observations)
        batch = np.ascontiguousarray(
            observations.values[None] / self.scale, dtype=np.float32
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
        result = session.run(["forecast"], {"input_sequence": batch})[0]
        output = self.output_specification()
        if result.shape != (1, len(output.lead_minutes), *output.spatial):
            raise ConstraintError(f"unexpected backend output shape: {result.shape}")
        values = np.ascontiguousarray(result[0] * self.scale, dtype=np.float32)
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


ADAPTER = NowcastNetAdapter()
