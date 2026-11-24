"""pySTEPS LK baseline. Public field requirements come from the shipped declaration."""

from datetime import timedelta
from pathlib import Path

import numpy as np

from interfaces.errors import ConstraintError, WeightsNotReady
from interfaces.fields import Forecast
from interfaces.model import DeclaredModelAdapter
from utils.model_contract import utc, validate_forecast
from utils.weights import file_sha256

from .model import predict as pysteps_predict


class PySTEPSLKAdapter(DeclaredModelAdapter):
    declaration_path = (
        Path(__file__).resolve().parents[3]
        / "atlas/contracts/models/cma_radar/pysteps_lk.json"
    )

    def prepare(self, observations):
        self.validate_input(observations)
        values = np.ascontiguousarray(observations.values, dtype=np.float32)
        return values, observations

    def predict(self, prepared, *, weights_path):
        values, observations = prepared
        path = Path(weights_path)
        if not path.is_file() or file_sha256(path) != self.weights().sha256:
            raise WeightsNotReady(
                "offline weight checksum mismatch; prepare pinned weights first"
            )
        output = self.output_specification()
        try:
            forecast, _ = pysteps_predict(
                values, lead_steps=len(output.lead_minutes), flow_frames=3
            )
        except ValueError as exc:
            raise ConstraintError(str(exc)) from exc
        values = np.ascontiguousarray(forecast, dtype=np.float32)
        if values.shape != (len(output.lead_minutes), *output.spatial):
            raise ConstraintError(f"unexpected backend output shape: {values.shape}")
        initial = observations.times[-1]
        result = Forecast(
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
        validate_forecast(result, output, observations)
        return result


ADAPTER = PySTEPSLKAdapter()
