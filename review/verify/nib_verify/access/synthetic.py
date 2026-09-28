"""Synthetic field provider.

Deterministic and self-contained: no files, no network, no GPU.  This is the
default provider while the platform's real one does not exist, and it is what
makes the acceptance test (``tests/test_done_when.py``) runnable today.

The field is a Gaussian blob that drifts across the domain, so

* thresholds between 15 and 45 dBZ all select a meaningful area,
* the forecast can be made arbitrarily close to (or far from) the truth,
* the whole thing depends only on a seed.

The observation and the model deliberately use *different* native grids, so the
regridding path is always exercised.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from .base import Capabilities, Coords, Field, FieldUnavailable, coords_from_extent

UTC = timezone.utc

DEFAULT_T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


class SyntheticAccess:
    """A deterministic stand-in for the platform's field provider."""

    def __init__(
        self,
        *,
        t_start: datetime = DEFAULT_T0,
        cadence_minutes: int = 10,
        horizon_minutes: int = 120,
        observation_names: tuple[str, ...] = ("radar-composite",),
        model_ids: tuple[str, ...] = ("mlcast-det",),
        obs_shape: tuple[int, int] = (48, 64),
        obs_extent: tuple[float, float, float, float] = (110.0, 30.0, 120.0, 42.0),
        model_shape: tuple[int, int] = (40, 52),
        model_extent: tuple[float, float, float, float] = (110.0, 30.0, 120.0, 42.0),
        variable: str = "reflectivity",
        unit: str = "dBZ",
        peak: float = 52.0,
        sigma: float = 0.12,
        truth_noise: float = 0.0,
        forecast_noise: float = 0.0,
        forecast_bias: float = 0.0,
        vmin: float | None = 0.0,
        vmax: float | None = None,
        missing_points: int = 0,
        unavailable_times: tuple[datetime, ...] = (),
        seed: int = 20260924,
        retrospective: bool = True,
    ) -> None:
        if cadence_minutes <= 0:
            raise ValueError("cadence_minutes must be positive")
        if not observation_names:
            raise ValueError("at least one observation name is required")
        if not model_ids:
            raise ValueError("at least one model id is required")

        self.t_start = _as_utc(t_start)
        self.cadence_minutes = int(cadence_minutes)
        self.horizon_minutes = int(horizon_minutes)
        self.observation_names = tuple(observation_names)
        self.model_ids = tuple(model_ids)
        self.variable = variable
        self.unit = unit
        self.peak = float(peak)
        self.sigma = float(sigma)
        self.truth_noise = float(truth_noise)
        self.forecast_noise = float(forecast_noise)
        self.forecast_bias = float(forecast_bias)
        # Nothing is clipped unless asked: a synthetic provider that silently
        # caps the field would make high thresholds unreachable, and a test that
        # passes for the wrong reason is worse than no test.
        self.vmin = vmin
        self.vmax = vmax
        self.missing_points = int(missing_points)
        self.seed = int(seed)
        self.retrospective = bool(retrospective)

        self.obs_coords = coords_from_extent(obs_extent, obs_shape)
        self.model_coords = coords_from_extent(model_extent, model_shape)
        self._unavailable = {_as_utc(t) for t in unavailable_times}

        self._obs_lead = None
        self._model_lead = None

    # ------------------------------------------------------------------
    # capabilities
    # ------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        units = {}
        for name in self.observation_names:
            units[name] = self.unit
        for mid in self.model_ids:
            units[mid] = self.unit
        return Capabilities(
            cadence_minutes=self.cadence_minutes,
            observation_names=self.observation_names,
            model_ids=self.model_ids,
            retrospective=self.retrospective,
            units=units,
        )

    # ------------------------------------------------------------------
    # observations
    # ------------------------------------------------------------------
    def list_observations(
        self, name: str, t0: datetime, t1: datetime
    ) -> list[datetime]:
        self._require_observation(name)
        t0, t1 = _as_utc(t0), _as_utc(t1)
        out = []
        t = self._align_up(t0)
        while t <= t1:
            if t not in self._unavailable:
                out.append(t)
            t = t + timedelta(minutes=self.cadence_minutes)
        return out

    def get_observation(self, name: str, valid_time: datetime) -> Field:
        self._require_observation(name)
        valid_time = _as_utc(valid_time)
        self._require_aligned(valid_time)
        if valid_time in self._unavailable:
            raise FieldUnavailable(
                f"observation {name!r} was not recorded at {valid_time.isoformat()}"
            )
        return self._make_field(
            self.obs_coords, valid_time, name=name, noise=self.truth_noise,
            stream=1, bias=0.0,
        )

    # ------------------------------------------------------------------
    # predictions
    # ------------------------------------------------------------------
    def list_predictions(self, model_id: str, init_time: datetime) -> list[int]:
        self._require_model(model_id)
        self._require_aligned(_as_utc(init_time))
        return list(range(self.cadence_minutes, self.horizon_minutes + 1,
                          self.cadence_minutes))

    def get_prediction(
        self, model_id: str, init_time: datetime, lead_minutes: int
    ) -> Field:
        self._require_model(model_id)
        init_time = _as_utc(init_time)
        self._require_aligned(init_time)
        if int(lead_minutes) not in self.list_predictions(model_id, init_time):
            raise FieldUnavailable(
                f"model {model_id!r} did not produce lead {lead_minutes} "
                f"from {init_time.isoformat()}"
            )
        valid_time = init_time + timedelta(minutes=int(lead_minutes))
        return self._make_field(
            self.model_coords, valid_time, name=model_id,
            noise=self.forecast_noise, stream=2, bias=self.forecast_bias,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _make_field(
        self,
        coords: Coords,
        valid_time: datetime,
        *,
        name: str,
        noise: float,
        stream: int,
        bias: float,
    ) -> Field:
        data = self.blob(coords, valid_time)
        if noise:
            data = data + self._noise(coords.shape, valid_time, stream, noise)
        if bias:
            data = data + bias
        if self.vmin is not None or self.vmax is not None:
            data = np.clip(
                data,
                self.vmin if self.vmin is not None else -np.inf,
                self.vmax if self.vmax is not None else np.inf,
            )
        mask = self._missing_mask(coords.shape, valid_time)
        return Field(
            data=data,
            coords=coords,
            valid_time=valid_time,
            unit=self.unit,
            variable=self.variable,
            name=name,
            mask=mask,
        )

    def blob(self, coords: Coords, valid_time: datetime) -> np.ndarray:
        """The noiseless truth at ``valid_time``, on ``coords``.

        Exposed so tests can assert against a known answer instead of a
        hard-coded number.
        """
        lon = coords.lon
        lat = coords.lat
        lon0, lat0, lon1, lat1 = coords.extent
        grid_lon, grid_lat = np.meshgrid(lon, lat)

        phase = (self._minutes(valid_time) % 360.0) / 360.0
        cx = lon0 + (0.20 + 0.60 * phase) * (lon1 - lon0)
        cy = lat0 + (0.35 + 0.30 * math.sin(2.0 * math.pi * phase)) * (lat1 - lat0)
        sx = max(self.sigma * (lon1 - lon0), 1e-9)
        sy = max(self.sigma * (lat1 - lat0), 1e-9)

        d2 = ((grid_lon - cx) / sx) ** 2 + ((grid_lat - cy) / sy) ** 2
        return self.peak * np.exp(-0.5 * d2)

    def _noise(
        self, shape: tuple[int, int], valid_time: datetime, stream: int, amplitude: float
    ) -> np.ndarray:
        rng = np.random.default_rng([self.seed, self._minutes(valid_time), stream])
        return rng.normal(0.0, amplitude, size=shape)

    def _missing_mask(
        self, shape: tuple[int, int], valid_time: datetime
    ) -> np.ndarray | None:
        if self.missing_points <= 0:
            return None
        n = min(self.missing_points, shape[0] * shape[1])
        rng = np.random.default_rng([self.seed, self._minutes(valid_time), 99])
        flat = rng.choice(shape[0] * shape[1], size=n, replace=False)
        mask = np.zeros(shape, dtype=bool)
        mask.flat[flat] = True
        return mask

    def _minutes(self, t: datetime) -> int:
        return int(round((_as_utc(t) - self.t_start).total_seconds() / 60.0))

    def _align_up(self, t: datetime) -> datetime:
        step = timedelta(minutes=self.cadence_minutes)
        delta = _as_utc(t) - self.t_start
        if delta.total_seconds() < 0:
            return self.t_start
        steps = math.ceil(delta.total_seconds() / step.total_seconds())
        return self.t_start + steps * step

    def _require_aligned(self, t: datetime) -> None:
        delta = (t - self.t_start).total_seconds()
        step = self.cadence_minutes * 60.0
        if abs(delta / step - round(delta / step)) > 1e-6:
            raise FieldUnavailable(
                f"{t.isoformat()} is not on the {self.cadence_minutes}-minute "
                "cadence; the ruler forbids temporal interpolation"
            )

    def _require_observation(self, name: str) -> None:
        if name not in self.observation_names:
            raise FieldUnavailable(f"unknown observation {name!r}")

    def _require_model(self, model_id: str) -> None:
        if model_id not in self.model_ids:
            raise FieldUnavailable(f"unknown model {model_id!r}")


def _as_utc(t: datetime) -> datetime:
    if t.tzinfo is None:
        return t.replace(tzinfo=UTC)
    return t.astimezone(UTC)
