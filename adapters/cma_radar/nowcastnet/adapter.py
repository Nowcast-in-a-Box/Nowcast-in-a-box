"""NowcastNet cross-domain CREF demo using the local SEVIR ONNX weights."""

from dataclasses import replace

import numpy as np

from adapters.sevir.nowcastnet.adapter import (
    NowcastNetAdapter as SEVIRNowcastNet,
    _frames,
)
from interfaces.errors import ConstraintError
from interfaces.model import ModelCapabilities, PreparedInput

NOTICE = "SEVIR pretrained cross-domain CREF demo; forecast skill is not validated."


class NowcastNetAdapter(SEVIRNowcastNet):
    def capabilities(self):
        return ModelCapabilities(
            id="nowcastnet",
            version="0.1.0",
            data_adapter_id="cma_radar",
            task="precip-nowcast.v1",
            status="reference",
        )

    def weights(self):
        return replace(
            super().weights(), artifacts_relpath="cma_radar/nowcastnet/NowcastNet.onnx"
        )

    def output_spec(self):
        return replace(
            super().output_spec(),
            variable_id="CREF",
            units="dBZ",
            description=NOTICE
            + " Linear inverse mapping 0..255 to 0..75 dBZ; 6 min frame spacing.",
        )

    def validate_input(self, bundle, data_adapter):
        if (
            data_adapter.capabilities().id != "cma_radar"
            or bundle.get("source") != "cma_radar"
        ):
            raise ConstraintError("CMA NowcastNet requires CMA Radar observations")

    def prepare(self, bundle, data_adapter):
        self.validate_input(bundle, data_adapter)
        frames = _frames(bundle, "CREF")
        if len(frames) < 5:
            raise ConstraintError(
                "CMA NowcastNet requires five consecutive context frames"
            )
        values = np.ascontiguousarray(np.stack(frames[-5:])[None], dtype=np.float32)
        if values.shape != (1, 5, 128, 128):
            raise ConstraintError(
                "Regrid the CMA region to 128x128 at 3 km before inference"
            )
        return PreparedInput(
            "CREF",
            "dBZ",
            "float32",
            tuple(values.shape),
            values,
            {"times": sorted(bundle["data"])[-5:]},
        )

    def predict(self, prepared, *, weights_path):
        if (
            prepared.variable_id != "CREF"
            or prepared.units != "dBZ"
            or tuple(prepared.values.shape) != (1, 5, 128, 128)
        ):
            raise ConstraintError("CMA model requires five 128x128 CREF/dBZ frames")
        if not np.isfinite(prepared.values).all() or (prepared.values < 0).any():
            raise ConstraintError(
                "CMA input has missing or negative pixels; choose another window/region"
            )
        encoded = np.ascontiguousarray(
            np.clip(prepared.values, 0, 75) * (255.0 / 75.0), dtype=np.float32
        )
        forecast = super().predict(
            replace(prepared, values=encoded), weights_path=weights_path
        )
        if not np.isfinite(forecast.values).all():
            raise ConstraintError("NowcastNet produced non-finite values")
        return replace(
            forecast,
            values=np.ascontiguousarray(
                np.clip(forecast.values * (75.0 / 255.0), 0, 75), dtype=np.float32
            ),
            coords={"lead_minutes": list(range(6, 121, 6))},
        )


ADAPTER = NowcastNetAdapter()
