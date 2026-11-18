"""NowcastNet cross-domain CREF demo using the local SEVIR ONNX weights."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from adapters.sevir.nowcastnet.adapter import NowcastNetAdapter as SEVIRNowcastNet
from interfaces.errors import ConstraintError

NOTICE = "SEVIR pretrained cross-domain CREF demo; forecast skill is not validated."


class NowcastNetAdapter(SEVIRNowcastNet):
    declaration_path = (
        Path(__file__).resolve().parents[3]
        / "atlas/contracts/models/cma_radar/nowcastnet.json"
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
        # Preserve the demo's 0..75 dBZ mapping into the pretrained tensor range.
        return super().prepare(
            replace(observations, values=np.clip(observations.values, 0, 75))
        )

    def predict(self, prepared, *, weights_path):
        forecast = super().predict(prepared, weights_path=weights_path)
        return replace(forecast, values=np.clip(forecast.values, 0, 75))


ADAPTER = NowcastNetAdapter()
