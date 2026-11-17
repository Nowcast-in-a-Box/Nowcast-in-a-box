"""Checks at the physical-field boundary, independent of inference backends."""

from datetime import datetime, timedelta

import numpy as np

from interfaces.errors import ConstraintError
from interfaces.fields import Forecast


def utc(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ConstraintError("field times must be explicit UTC") from exc
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ConstraintError("field times must be explicit UTC")
    return result


def validate_field(field):
    values, mask = np.asarray(field.values), np.asarray(field.mask)
    if values.dtype != np.float32 or values.ndim != 3:
        raise ConstraintError("field must be float32 with dimensions (time, y, x)")
    if mask.dtype != bool or mask.shape != values.shape:
        raise ConstraintError("field mask must be boolean and match values")
    if np.any(~np.isfinite(values) & ~mask):
        raise ConstraintError("field has undeclared non-finite samples")
    if not field.variable_id or not field.units:
        raise ConstraintError("field requires variable and units")
    if not field.grid.get("crs") or not field.grid.get("axis_units"):
        raise ConstraintError("field grid requires CRS and axis units")
    for name, length in (("y", values.shape[1]), ("x", values.shape[2])):
        axis = np.asarray(field.grid.get(name))
        if (
            axis.ndim != 1
            or axis.size != length
            or length < 2
            or not np.isfinite(axis).all()
            or not np.all(np.diff(axis) > 0)
        ):
            raise ConstraintError(
                "field grid axes must match values and increase strictly"
            )
    times = tuple(utc(t) for t in field.times)
    if (
        not times
        or len(times) != values.shape[0]
        or any(b <= a for a, b in zip(times, times[1:]))
    ):
        raise ConstraintError("field times must match values and increase strictly")
    return times


def validate_input(observations, requirements, capabilities):
    times = validate_field(observations)
    if observations.source != capabilities.data_adapter_id:
        raise ConstraintError("observations do not match the paired data source")
    if (observations.variable_id, observations.units) != (
        requirements.variable_id,
        requirements.units,
    ):
        raise ConstraintError("input variable or units disagree with declaration")
    if observations.values.shape != (
        requirements.context_frames,
        *requirements.spatial,
    ):
        raise ConstraintError(
            "input history length or grid shape disagrees with declaration"
        )
    if any(
        (b - a).total_seconds() != 60 * requirements.cadence_minutes
        for a, b in zip(times, times[1:])
    ):
        raise ConstraintError("input cadence disagrees with declaration")
    if not requirements.allow_missing and np.any(observations.mask):
        raise ConstraintError("model does not accept missing input samples")


def validate_forecast(forecast, specification, observations=None):
    if not isinstance(forecast, Forecast):
        raise ConstraintError("predict must return a Forecast")
    times = validate_field(forecast)
    if (forecast.variable_id, forecast.units) != (
        specification.variable_id,
        specification.units,
    ):
        raise ConstraintError("forecast variable or units disagree with declaration")
    if forecast.values.shape != (
        len(specification.lead_minutes),
        *specification.spatial,
    ):
        raise ConstraintError("forecast shape disagrees with declaration")
    if tuple(forecast.lead_minutes) != specification.lead_minutes or any(
        type(v) is not int or v <= 0 for v in forecast.lead_minutes
    ):
        raise ConstraintError("forecast leads disagree with declaration")
    initial = utc(forecast.init_time)
    if any(
        valid != initial + timedelta(minutes=lead)
        for valid, lead in zip(times, forecast.lead_minutes)
    ):
        raise ConstraintError(
            "forecast valid times must equal initialization plus leads"
        )
    if not specification.allow_missing and np.any(forecast.mask):
        raise ConstraintError("model does not declare missing forecast samples")
    if observations is not None:
        if initial != utc(observations.times[-1]):
            raise ConstraintError("forecast initialization must equal last observation")
        if specification.grid == "input":
            if any(
                forecast.grid.get(key) != observations.grid.get(key)
                for key in ("crs", "axis_units")
            ) or any(
                not np.array_equal(forecast.grid[key], observations.grid[key])
                for key in ("x", "y")
            ):
                raise ConstraintError("forecast grid must match input grid")
