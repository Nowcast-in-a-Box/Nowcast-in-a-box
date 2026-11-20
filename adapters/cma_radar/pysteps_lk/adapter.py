"""NiB model adapter for the deterministic pySTEPS LK baseline."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from interfaces.errors import ConstraintError
from interfaces.model import (
    AxisSpec,
    Forecast,
    ForecastSpec,
    ModelAdapter,
    ModelCapabilities,
    PreparedInput,
    WeightSpec,
)

from .model import MODEL_ID, MODEL_VERSION, predict as pysteps_predict

_MANIFEST = Path(__file__).with_name("algorithm-manifest.json")
_MANIFEST_SHA256 = hashlib.sha256(_MANIFEST.read_bytes()).hexdigest()
_LEADS = list(range(6, 121, 6))


class PySTEPSLKAdapter(ModelAdapter):
    """Wrap the already validated local algorithm in the NiB model contract."""

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            id=MODEL_ID,
            version=MODEL_VERSION,
            data_adapter_id="cma_radar",
            task="reflectivity-nowcast.v1",
            status="baseline",
        )

    def weights(self) -> WeightSpec:
        """Expose the immutable algorithm manifest through the legacy artifact slot.

        The current Handler requires every model image to declare a checked
        artifact.  pySTEPS has no learned parameters, so this digest identifies
        the embedded algorithm configuration rather than neural-network weights.
        """
        return WeightSpec(
            status="not_required",
            download_url=None,
            sha256=_MANIFEST_SHA256,
            filename=_MANIFEST.name,
            hf=None,
            artifacts_relpath=None,
        )

    def output_spec(self) -> ForecastSpec:
        return ForecastSpec(
            dims=("batch", "lead", "y", "x"),
            dtype="float32",
            variable_id="CREF",
            units="dBZ",
            description="pySTEPS 1.21.5 Lucas-Kanade motion plus deterministic extrapolation",
            constraints_status="CMA CREF dBZ, finite pixels, at least three frames",
            axes=(
                AxisSpec("batch", "one deterministic member", 1),
                AxisSpec("lead", "forecast lead", len(_LEADS), "frames"),
                AxisSpec("y", "grid row", None),
                AxisSpec("x", "grid column", None),
            ),
        )

    def validate_input(self, bundle, data_adapter) -> None:
        if (
            data_adapter.capabilities().id != "cma_radar"
            or bundle.get("source") != "cma_radar"
        ):
            raise ConstraintError("pySTEPS LK requires CMA Radar observations")

    def prepare(self, bundle, data_adapter) -> PreparedInput:
        self.validate_input(bundle, data_adapter)
        fields = bundle.get("data", {})
        frames = [fields[key]["CREF"] for key in sorted(fields)]
        values = np.ascontiguousarray(np.stack(frames), dtype=np.float32)
        return PreparedInput(
            "CREF",
            "dBZ",
            "float32",
            tuple(values.shape),
            values,
            {"times": sorted(fields)},
        )

    def predict(self, prepared: PreparedInput, *, weights_path: str) -> Forecast:
        values = np.asarray(prepared.values, dtype=np.float32)
        if prepared.variable_id != "CREF" or prepared.units != "dBZ":
            raise ConstraintError("pySTEPS LK requires CREF reflectivity in dBZ")
        if values.ndim == 4 and values.shape[0] == 1:
            values = values[0]
        if values.ndim != 3 or values.shape[0] < 3:
            raise ConstraintError("pySTEPS LK requires at least three context frames")
        try:
            forecast, _ = pysteps_predict(values, lead_steps=len(_LEADS), flow_frames=3)
        except ValueError as exc:
            raise ConstraintError(str(exc)) from exc
        return Forecast(
            spec=self.output_spec(),
            shape=(1, *forecast.shape),
            values=np.ascontiguousarray(forecast[None], dtype=np.float32),
            coords={"lead_minutes": _LEADS, "algorithm_manifest": weights_path},
        )


ADAPTER = PySTEPSLKAdapter()
