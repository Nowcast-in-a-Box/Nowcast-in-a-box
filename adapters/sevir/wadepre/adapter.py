"""WADEPre inference for SEVIR VIL. This file owns the ONNX call."""

from __future__ import annotations

from pathlib import Path

from interfaces.data import DataAdapter, DataBundle, request_from_mapping
from interfaces.errors import ConstraintError, WeightsNotReady
from interfaces.model import (
    AxisSpec,
    Forecast,
    ForecastSpec,
    ModelAdapter,
    ModelCapabilities,
    PreparedInput,
    WeightSpec,
)
from utils.weights import ensure_weights, file_sha256, huggingface_url

_CONTEXT_FRAMES = 5
_LEAD_FRAMES = 20
_HEIGHT = 128
_WIDTH = 128
_SCALE = 255.0
_INPUT_NAME = "input_sequence"
_OUTPUT_NAME = "refined_out"
_FILENAME = "WADEPre.onnx"
_SHA256 = "55c396165f620934b1262eeac19997f14b00fae5f3a150015a5a84c9cbd6ee3b"
_HF_REPO = "sonderlau/nib-wadepre"
_REVISION = "main"
_ARTIFACTS_RELPATH = "sevir/wadepre/WADEPre.onnx"


class WadePreAdapter(ModelAdapter):
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            id="wadepre",
            version="0.1.0",
            data_adapter_id="sevir",
            task="precip-nowcast.v1",
            status="reference",
        )

    def weights(self) -> WeightSpec:
        return WeightSpec(
            status="pinned",
            download_url=huggingface_url(_HF_REPO, _REVISION, _FILENAME),
            sha256=_SHA256,
            filename=_FILENAME,
            hf={"repo": _HF_REPO, "revision": _REVISION, "filename": _FILENAME},
            artifacts_relpath=_ARTIFACTS_RELPATH,
        )

    def output_spec(self) -> ForecastSpec:
        return ForecastSpec(
            dims=("batch", "lead", "y", "x"),
            dtype="float32",
            variable_id=None,
            units=None,
            description="WADEPre forecast restored to raw VIL by scale 255.",
            constraints_status="stable",
            axes=(
                AxisSpec("batch", "ONNX batch dimension", 1, None),
                AxisSpec("lead", "Forecast frames", _LEAD_FRAMES, "frames"),
                AxisSpec("y", "Rows", _HEIGHT, None),
                AxisSpec("x", "Columns", _WIDTH, None),
            ),
        )

    def validate_input(self, bundle: DataBundle, data_adapter: DataAdapter) -> None:
        caps = data_adapter.capabilities()
        if caps.id != "sevir" or bundle.get("source") != "sevir":
            raise ConstraintError("WADEPre in this directory is paired with sevir")
        data_adapter.validate(
            request_from_mapping(bundle["request"], bundle["request"].get("data_root"))
        )

    def prepare(self, bundle: DataBundle, data_adapter: DataAdapter) -> PreparedInput:
        import numpy as np

        variable = _variable(data_adapter)
        frames = _frames(bundle, variable.id)
        selected = _latest(frames, _CONTEXT_FRAMES)
        stacked = np.stack([_fit(frame, _HEIGHT, _WIDTH) for frame in selected], axis=0)
        values = np.ascontiguousarray(stacked[None, ...], dtype=np.float32)
        return PreparedInput(
            variable_id=variable.id,
            units=variable.units,
            dtype="float32",
            shape=tuple(values.shape),
            values=values,
            coords={"times": sorted(bundle["data"])[-_CONTEXT_FRAMES:]},
        )

    def predict(self, prepared: PreparedInput, *, weights_path: str) -> Forecast:
        import numpy as np

        spec = self.weights()
        ready = _ready(spec, weights_path)
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise WeightsNotReady(
                f"onnxruntime is not installed; download_url={spec.download_url!r}"
            ) from exc
        session = ort.InferenceSession(str(ready), providers=["CPUExecutionProvider"])
        batch = np.ascontiguousarray(prepared.values / _SCALE, dtype=np.float32)
        try:
            result = session.run([_OUTPUT_NAME], {_INPUT_NAME: batch})[0]
        except Exception as exc:
            raise WeightsNotReady(f"ONNX Runtime inference failed: {exc}") from exc
        values = np.ascontiguousarray(result * _SCALE, dtype=np.float32)
        if tuple(values.shape) != (1, _LEAD_FRAMES, _HEIGHT, _WIDTH):
            raise ConstraintError(f"ONNX output shape {tuple(values.shape)} is wrong")
        out = self.output_spec()
        return Forecast(
            spec=ForecastSpec(
                dims=out.dims,
                dtype=out.dtype,
                variable_id=prepared.variable_id,
                units=prepared.units,
                description=out.description,
                constraints_status=out.constraints_status,
                axes=out.axes,
            ),
            shape=tuple(values.shape),
            values=values,
            coords={"lead_minutes": [5 * (index + 1) for index in range(_LEAD_FRAMES)]},
        )


def _variable(data_adapter: DataAdapter):
    channels = data_adapter.capabilities().channels
    if channels.mode != "closed" or len(channels.variables) != 1:
        raise ConstraintError(
            "WADEPre expects one closed variable from the data reader"
        )
    return channels.variables[0]


def _frames(bundle: DataBundle, variable_id: str) -> list:
    import numpy as np

    frames = []
    for time_key in sorted(bundle["data"]):
        if variable_id not in bundle["data"][time_key]:
            raise ConstraintError(f"missing {variable_id!r} at {time_key}")
        frame = np.asarray(bundle["data"][time_key][variable_id], dtype=np.float32)
        if frame.ndim != 2:
            raise ConstraintError("frames must be 2-D")
        frames.append(frame)
    if not frames:
        raise ConstraintError("bundle has no frames")
    return frames


def _latest(frames: list, count: int) -> list:
    padded = list(frames)
    while len(padded) < count:
        padded.append(padded[-1])
    return padded[-count:]


def _fit(frame, height: int, width: int):
    import numpy as np

    if frame.shape == (height, width):
        return frame
    rows = np.linspace(0, frame.shape[0] - 1, height).astype(int)
    cols = np.linspace(0, frame.shape[1] - 1, width).astype(int)
    return np.asarray(frame[rows][:, cols], dtype=np.float32)


def _ready(spec: WeightSpec, weights_path: str) -> Path:
    given = Path(weights_path) if weights_path else None
    if given is not None and given.is_file() and file_sha256(given) == spec.sha256:
        return given
    return ensure_weights(spec)


ADAPTER = WadePreAdapter()
