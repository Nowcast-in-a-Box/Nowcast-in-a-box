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

#: How a provider can be missing data. ``random`` is speckle, the way an
#: individual bad gate looks; ``range`` is the shape coverage leaves behind.
MISSING_KINDS = ("none", "random", "range")


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
        # --- shape of the field ---------------------------------------
        # All of these default to the single-blob generator that was here
        # before, so every existing expectation still holds exactly. A named
        # bundle of them is in nib_verify/access/scenario.py.
        cells: int = 1,
        drift: float = 0.0,
        background: float = 0.0,
        # --- how good the forecast is ---------------------------------
        forecast_lag_minutes: float = 0.0,
        skill_spread: float = 0.0,
        # --- what is missing ------------------------------------------
        missing_kind: str = "random",
        drop_leads: tuple[int, ...] = (),
        missing_runs_every: int = 0,
    ) -> None:
        if cadence_minutes <= 0:
            raise ValueError("cadence_minutes must be positive")
        if not observation_names:
            raise ValueError("at least one observation name is required")
        if not model_ids:
            raise ValueError("at least one model id is required")
        if cells < 1:
            raise ValueError("cells must be at least 1")
        if missing_kind not in MISSING_KINDS:
            raise ValueError(
                f"unknown missing_kind {missing_kind!r}; expected one of {MISSING_KINDS}"
            )
        if missing_runs_every < 0:
            raise ValueError("missing_runs_every cannot be negative")

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

        self.cells = int(cells)
        self.drift = float(drift)
        self.background = float(background)
        self.forecast_lag_minutes = float(forecast_lag_minutes)
        self.skill_spread = float(skill_spread)
        self.missing_kind = missing_kind
        # int(...) rather than trusting the type: --mock drop_leads=20,30 arrives
        # as strings, and an empty tuple default gives the CLI nothing to infer
        # from.
        self.drop_leads = tuple(int(x) for x in drop_leads)
        self.missing_runs_every = int(missing_runs_every)

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
        init_time = _as_utc(init_time)
        self._require_aligned(init_time)
        if self._run_missing(init_time):
            return []
        return [
            lead
            for lead in range(
                self.cadence_minutes, self.horizon_minutes + 1, self.cadence_minutes
            )
            if lead not in self.drop_leads
        ]

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
        # Models in the order they were declared, getting worse. One number
        # rather than a table of per-model skill, because a comparison needs two
        # models that differ and not five that differ in five ways.
        rank = 1.0 + self.model_ids.index(model_id) * self.skill_spread
        return self._make_field(
            self.model_coords, valid_time, name=model_id,
            noise=self.forecast_noise * rank, stream=2, bias=self.forecast_bias,
            lag_minutes=self.forecast_lag_minutes * rank,
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
        lag_minutes: float = 0.0,
    ) -> Field:
        # A lagging forecast is the field from earlier, not a blurred one. It is
        # the honest way to be wrong: the structure is right and the position is
        # not, so a high threshold loses it entirely while a low one barely
        # notices — which is exactly what thresholds are for.
        data = self.blob(coords, valid_time - timedelta(minutes=lag_minutes))
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
        mask = self._missing_mask(coords, valid_time)
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

        Deliberately **independent of which model asked**: the same instant
        always produces the same field, and a model's error is applied on top
        in :meth:`_make_field`. If this knew about models, a perfect forecast
        could not be expressed at all, and the acceptance tests depend on one.
        """
        total = self._cell(coords, valid_time, 0)
        for index in range(1, self.cells):
            total = total + self._cell(coords, valid_time, index)
        if self.background:
            total = total + self._background_field(coords)
        return total

    def _cell(self, coords: Coords, valid_time: datetime, index: int) -> np.ndarray:
        """One precipitating cell.

        Cell 0 with ``drift == 0`` is the original single blob, expression for
        expression. That is not sentimentality: a great many tests assert that a
        noiseless forecast equals the truth, and they would all have to be
        rewritten if the default field moved by a bit.
        """
        lon0, lat0, lon1, lat1 = coords.extent
        lon_span = max(lon1 - lon0, 1e-9)
        lat_span = max(lat1 - lat0, 1e-9)
        grid_lon, grid_lat = np.meshgrid(coords.lon, coords.lat)
        minutes = self._minutes(valid_time)

        if index == 0 and self.drift == 0.0:
            phase = (minutes % 360.0) / 360.0
            cx = lon0 + (0.20 + 0.60 * phase) * lon_span
            cy = lat0 + (0.35 + 0.30 * math.sin(2.0 * math.pi * phase)) * lat_span
            amplitude = self.peak
            size = self.sigma
        else:
            home_lon, home_lat, amplitude, size = self._cell_shape(index)
            hours = minutes / 60.0
            # A steady flow, and no wrapping. A forecast that lags the truth is
            # then displaced the way a real one is, and a cell that leaves the
            # domain has genuinely left it.
            cx = lon0 + (home_lon + self.drift * hours) * lon_span
            cy = lat0 + (home_lat + 0.25 * self.drift * hours) * lat_span

        sx = max(size * lon_span, 1e-9)
        sy = max(size * lat_span, 1e-9)
        d2 = ((grid_lon - cx) / sx) ** 2 + ((grid_lat - cy) / sy) ** 2
        return amplitude * np.exp(-0.5 * d2)

    def _cell_shape(self, index: int) -> tuple[float, float, float, float]:
        """Where cell ``index`` starts, how big it is, how hard it rains.

        Drawn once from the seed rather than stepped, so a cell is a thing that
        moves rather than a thing that is different every frame.
        """
        if index == 0:
            return 0.35, 0.45, self.peak, self.sigma
        rng = np.random.default_rng([self.seed, index, 31])
        return (
            float(rng.uniform(0.05, 0.85)),
            float(rng.uniform(0.10, 0.90)),
            float(self.peak * rng.uniform(0.30, 0.95)),
            float(rng.uniform(0.05, 0.10)),
        )

    def _background_field(self, coords: Coords) -> np.ndarray:
        """A wide, low dome: light rain over most of the domain.

        Without it every threshold above the noise sits over the same few cells,
        and a low threshold has no area to be right or wrong about.
        """
        lon0, lat0, lon1, lat1 = coords.extent
        lon_span = max(lon1 - lon0, 1e-9)
        lat_span = max(lat1 - lat0, 1e-9)
        grid_lon, grid_lat = np.meshgrid(coords.lon, coords.lat)
        dx = (grid_lon - (lon0 + 0.5 * lon_span)) / lon_span
        dy = (grid_lat - (lat0 + 0.5 * lat_span)) / lat_span
        return self.background * np.exp(-2.0 * (dx * dx + dy * dy))

    def _noise(
        self, shape: tuple[int, int], valid_time: datetime, stream: int, amplitude: float
    ) -> np.ndarray:
        rng = np.random.default_rng([self.seed, self._minutes(valid_time), stream])
        return rng.normal(0.0, amplitude, size=shape)

    def _missing_mask(self, coords: Coords, valid_time: datetime) -> np.ndarray | None:
        shape = coords.shape
        parts = []
        if self.missing_kind == "range":
            parts.append(self._range_mask(coords))
        if self.missing_kind != "none" and self.missing_points > 0:
            # Speckle on top of a coverage gap is what a real composite looks
            # like, and the two exercise different code: a large contiguous hole
            # catches a resampling step that fills, scattered points catch one
            # that smears.
            rng = np.random.default_rng([self.seed, self._minutes(valid_time), 99])
            n = min(self.missing_points, shape[0] * shape[1])
            speckle = np.zeros(shape, dtype=bool)
            speckle.flat[rng.choice(shape[0] * shape[1], size=n, replace=False)] = True
            parts.append(speckle)
        if not parts:
            return None
        mask = parts[0]
        for extra in parts[1:]:
            mask = mask | extra
        return mask if mask.any() else None

    def _range_mask(self, coords: Coords) -> np.ndarray:
        """The shape a single radar leaves: nothing far away, and one shadow.

        Speckle is easy to be right about — it lands everywhere, so the mask
        never lines up with anything. This is the case that catches a resampling
        step that fills a gap with a value, because the gap is in one place and
        it is large.
        """
        lon0, lat0, lon1, lat1 = coords.extent
        lon_span = max(lon1 - lon0, 1e-9)
        lat_span = max(lat1 - lat0, 1e-9)
        grid_lon, grid_lat = np.meshgrid(coords.lon, coords.lat)
        dx = (grid_lon - (lon0 + 0.5 * lon_span)) / lon_span
        dy = (grid_lat - (lat0 + 0.55 * lat_span)) / lat_span
        # 0.58 of a span leaves roughly a fifth missing on a 10x12 degree domain:
        # a gap worth resampling around, not a field that is mostly hole.
        outside = np.sqrt(dx * dx + dy * dy) > 0.58
        shadow = (dy < -0.28) & (np.abs(dx) < 0.07)
        return outside | shadow

    def _run_missing(self, init_time: datetime) -> bool:
        """Did this run produce nothing at all?

        Counting from one, not from zero. ``index % n == 0`` would make the run
        at ``t_start`` the first casualty, and that is the run every test and
        every first look begins with — a scenario whose opening run produces
        nothing teaches the wrong lesson about the scenario.
        """
        if not self.missing_runs_every:
            return False
        index = self._minutes(init_time) // self.cadence_minutes
        return (index + 1) % self.missing_runs_every == 0

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
