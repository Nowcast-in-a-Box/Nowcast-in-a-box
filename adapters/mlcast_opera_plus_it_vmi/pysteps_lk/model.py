"""Official pySTEPS LK motion plus semi-Lagrangian extrapolation."""

from __future__ import annotations

import numpy as np

MODEL_ID = "pysteps_lk"
MODEL_VERSION = "0.1.0"


def predict(context: np.ndarray, *, lead_steps: int = 20, flow_frames: int = 3):
    """Forecast a finite ``(time, y, x)`` VMI/dBZ context with pySTEPS."""
    values = np.asarray(context, dtype=np.float32)
    if values.ndim != 3 or min(values.shape[1:]) < 3:
        raise ValueError("context must have shape (time, y, x) with a 2-D grid")
    if flow_frames < 2 or values.shape[0] < flow_frames:
        raise ValueError("flow_frames must be >=2 and <= the context length")
    if lead_steps < 1:
        raise ValueError("lead_steps must be >=1")
    if not np.isfinite(values).all():
        raise ValueError("context contains missing-coverage pixels")
    try:
        from pysteps import motion, nowcasts
    except ImportError as exc:
        raise RuntimeError("pysteps==1.21.5 is required by this model image") from exc
    velocity = np.asarray(
        motion.get_method("LK")(np.ascontiguousarray(values[-flow_frames:])),
        dtype=np.float32,
    )
    if velocity.shape != (2, *values.shape[-2:]) or not np.isfinite(velocity).all():
        raise ValueError("pySTEPS LK returned an invalid motion field")
    forecast = np.asarray(
        nowcasts.get_method("extrapolation")(
            np.ascontiguousarray(values[-1]),
            velocity,
            lead_steps,
            extrap_kwargs={"outval": "min"},
        ),
        dtype=np.float32,
    )
    if forecast.shape != (lead_steps, *values.shape[-2:]) or not np.isfinite(forecast).all():
        raise ValueError("pySTEPS extrapolation returned an invalid forecast")
    return forecast
