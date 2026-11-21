"""Official pySTEPS 1.21.5 LK motion + semi-Lagrangian extrapolation.

This module is deliberately independent of NiB Handler/Hub/Docker. It consumes
already decoded (time, y, x) arrays and returns a real pySTEPS forecast.
There is no local reimplementation of the motion or advection algorithms.
"""

from __future__ import annotations

import numpy as np

MODEL_ID = "pysteps_lk"
MODEL_VERSION = "0.1.0"


def predict(
    context: np.ndarray,
    *,
    lead_steps: int = 20,
    flow_frames: int = 3,
) -> tuple[np.ndarray, np.ndarray]:
    """Return forecast (lead,y,x) and motion (2,y,x), both in pixel units.

    Parameters
    ----------
    context:
        Continuous, equally spaced *finite* radar reflectivity fields (dBZ).
        Their original pixel ordering is preserved. Pixel time step is given
        by the input cadence (6 minutes for CMA CREF), not hard-coded here.
    lead_steps:
        Number of future input time steps to extrapolate.
    flow_frames:
        Number of most recent images supplied to pySTEPS LK (>= 2).

    Notes
    -----
    This is an advection-only CREF (dBZ) baseline, not a rainfall-rate (mm/h)
    retrieval. Input must be cleaned for CMA no-echo/missing sentinel values
    before calling this function.

    The pinned pySTEPS v1.21.5 interface takes numpy arrays. The development
    branch has different xarray-based signatures; do not mix them.
    """
    values = np.asarray(context, dtype=np.float32)
    if values.ndim != 3 or min(values.shape[1:]) < 3:
        raise ValueError("context must have shape (time, y, x) with a 2-D grid")
    if flow_frames < 2 or values.shape[0] < flow_frames:
        raise ValueError("flow_frames must be >=2 and <= the context length")
    if lead_steps < 1:
        raise ValueError("lead_steps must be >=1")
    if not np.isfinite(values).all():
        raise ValueError(
            "context contains radar missing-coverage pixels (NaN/Inf). "
            "Choose a fully covered region or input window; never convert "
            "missing coverage into 'no precipitation' silently."
        )

    try:
        from pysteps import motion, nowcasts
    except ImportError as exc:
        raise RuntimeError(
            "Official pySTEPS is not installed in the current Python environment. "
            "Install 'pysteps=1.21.5' and its dependencies first."
        ) from exc

    # This code intentionally calls the official implementations directly.
    velocity = motion.get_method("LK")(
        np.ascontiguousarray(values[-flow_frames:], dtype=np.float32)
    )
    velocity = np.asarray(velocity, dtype=np.float32)
    expected_motion = (2, *values.shape[-2:])
    if velocity.shape != expected_motion or not np.isfinite(velocity).all():
        raise ValueError(
            f"pySTEPS LK returned an invalid motion field: {velocity.shape}; "
            f"expected {expected_motion} with finite velocities"
        )

    forecast = nowcasts.get_method("extrapolation")(
        np.ascontiguousarray(values[-1], dtype=np.float32),
        velocity,
        lead_steps,
        extrap_kwargs={"outval": "min"},
    )
    forecast = np.asarray(forecast, dtype=np.float32)
    expected_forecast = (lead_steps, *values.shape[-2:])
    if forecast.shape != expected_forecast:
        raise ValueError(
            f"pySTEPS forecast shape {forecast.shape} does not match "
            f"{expected_forecast}"
        )
    if not np.isfinite(forecast).all():
        raise ValueError("pySTEPS extrapolation produced non-finite forecasts")
    return forecast, velocity
