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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import numpy as np

from .base import Capabilities, Coords, Field, FieldUnavailable, coords_from_extent

UTC = timezone.utc

DEFAULT_T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

DEFAULT_EXTENT = (110.0, 30.0, 120.0, 42.0)

#: The domain the generator's fractional parameters are measured on.
#:
#: ``sigma``, ``drift`` and the background are fractions of *this*, not of whatever
#: extent the caller asks for. Otherwise a storm would be a thing in whatever box
#: you happened to sample it in: the same instant observed on a wider grid would
#: be a bigger, faster storm, and two products of one storm -- a radar's footprint
#: and a satellite's -- would stop being the same storm. That shows up as skill
#: collapsing for no reason in the weather, which is the worst kind of wrong
#: answer to debug.
REFERENCE_SPAN = (DEFAULT_EXTENT[2] - DEFAULT_EXTENT[0],
                  DEFAULT_EXTENT[3] - DEFAULT_EXTENT[1])

#: How a provider can be missing data. ``random`` is speckle, the way an
#: individual bad gate looks; ``range`` is the shape coverage leaves behind.
MISSING_KINDS = ("none", "random", "range")


#: The three ends of the aging curve were chosen by measuring what they produce,
#: not by taste: at a lead of ten minutes the threat score is 0.6-0.75, bias sits
#: at 1.0 and FSS is 0.98; at three hours the threat score is 0.2-0.3, bias reaches
#: 2.0 for a low threshold at a long lead -- the classic over-forecast -- and FSS
#: is 0.8. Those are the numbers a precipitation nowcast actually gets, and a
#: demonstration whose scores are all 0.99 shows a reader nothing.
#:
#: The two scales are the same one: the field is generated in the truth's units,
#: so a noise of twenty is twenty of those, which the product's own scale turns
#: into about five dBZ. Written in the units of the thing being noised, once.
#:
#: How fast a nowcast ages, in minutes. At a lead of ``tau`` it is halfway to
#: its worst; at three times ``tau`` the difference is small. Forty-five is a
#: convective cell's worth of "still nearly right at ten minutes, mostly wrong at
#: an hour", which is the span a nowcast is actually used over.
SKILL_TAU_MINUTES = 45.0

#: Noise relative to what the scenario asked for, young and aged.
YOUNG_NOISE = 0.35
AGED_NOISE = 20.0

#: Position error, same two ends of the same scale.
YOUNG_LAG = 0.35
AGED_LAG = 12.0

#: How much the echoes grow as the forecast ages, as a fraction. A gain rather
#: than an offset, because an offset rains on the clear sky as well -- see
#: ``_make_field``.
AGED_GAIN = 0.45

#: And a little of the offset too, in truth units, for the spread at the edges.
AGED_SPREAD = 4.0


@dataclass(frozen=True)
class Source:
    """One observation product, as the platform serves it.

    Several products of one storm are not several storms. They carry the same
    weather on a different scale, with their own instrument noise and their own
    coverage, and that is exactly what a multi-source evaluation has to cope
    with.

    The *unit* is the part a config cares about: every member of a config has to
    be in the ruler's unit, so two products in different units are two different
    rulers, and therefore two different configs.

    ``scale`` is one number rather than a second field generator. A rain rate, a
    reflectivity and a VIL of the same cell are related by a curve, and one
    multiplier is enough to make them different numbers on different scales
    without pretending the curve is known.
    """

    name: str
    variable: str
    unit: str
    scale: float = 1.0
    #: Added after the scale, so a quantity whose range does not start at zero can
    #: be expressed at all. Brightness temperature is one: the field carries
    #: intensity, and intensity runs the other way -- a deep convective top is
    #: *colder* than the clear sky around it -- so it needs a negative scale and a
    #: warm offset rather than one multiplier.
    offset: float = 0.0
    vmin: float | None = None
    vmax: float | None = None
    noise: float = 0.0
    missing_kind: str = "random"
    missing_points: int = 0
    extent: tuple[float, float, float, float] | None = None
    shape: tuple[int, int] | None = None


#: what a per-source override may say. Anything else is a typo, and a typo that
#: silently did nothing would serve the wrong field.
SOURCE_FIELDS = frozenset(
    {"variable", "unit", "scale", "offset", "vmin", "vmax", "noise", "missing_kind",
     "missing_points", "extent", "shape"}
)


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
        sources: Mapping[str, Mapping[str, Any]] = {},
        model_sources: Mapping[str, str] = {},
        obs_shape: tuple[int, int] = (48, 64),
        obs_extent: tuple[float, float, float, float] = DEFAULT_EXTENT,
        model_shape: tuple[int, int] = (40, 52),
        model_extent: tuple[float, float, float, float] = DEFAULT_EXTENT,
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
        self.obs_shape = tuple(obs_shape)
        self.obs_extent = tuple(obs_extent)
        self._unavailable = {_as_utc(t) for t in unavailable_times}
        self.sources = self._resolve_sources(sources)
        self.model_sources = self._resolve_model_sources(model_sources)

        self._obs_lead = None
        self._model_lead = None

    def _resolve_sources(
        self, overrides: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, Source]:
        """One fully-resolved :class:`Source` per observation name.

        Resolved rather than looked up on demand, so a name that does not exist,
        or a setting that is not a setting, is refused when the provider is
        built — not when a field is asked for and quietly comes back as the
        first source's.
        """
        stray = sorted(set(overrides) - set(self.observation_names))
        if stray:
            raise ValueError(
                f"source settings for {stray}, which are not observation names; "
                f"the provider serves {list(self.observation_names)}"
            )
        out: dict[str, Source] = {}
        for name in self.observation_names:
            given = dict(overrides.get(name) or {})
            unknown = sorted(set(given) - SOURCE_FIELDS)
            if unknown:
                raise ValueError(
                    f"source {name!r} has settings that do not exist: {unknown}; "
                    f"expected any of {sorted(SOURCE_FIELDS)}"
                )
            out[name] = Source(
                name=name,
                variable=str(given.get("variable", self.variable)),
                unit=str(given.get("unit", self.unit)),
                scale=float(given.get("scale", 1.0)),
                offset=float(given.get("offset", 0.0)),
                vmin=given.get("vmin", self.vmin),
                vmax=given.get("vmax", self.vmax),
                noise=float(given.get("noise", self.truth_noise)),
                missing_kind=str(given.get("missing_kind", self.missing_kind)),
                missing_points=int(given.get("missing_points", self.missing_points)),
                extent=given.get("extent"),
                shape=(tuple(given["shape"]) if given.get("shape") else None),
            )
        return out

    def _resolve_model_sources(self, given: Mapping[str, str]) -> dict[str, Source]:
        """Which product each model's output is, by the source it copies.

        A model predicting reflectivity is a reflectivity product: same variable,
        same unit, same scale as the observations it will be compared with. A
        model whose product does not say which falls back to the provider's own
        variable and unit.
        """
        stray = sorted(set(given) - set(self.model_ids))
        if stray:
            raise ValueError(
                f"model_sources names {stray}, which are not model ids; the "
                f"provider serves {list(self.model_ids)}"
            )
        out: dict[str, Source] = {}
        for model_id, source_name in given.items():
            if source_name not in self.sources:
                raise ValueError(
                    f"model {model_id!r} is said to produce {source_name!r}, "
                    f"which is not an observation the provider serves: "
                    f"{list(self.sources)}"
                )
            out[model_id] = self.sources[source_name]
        return out

    # ------------------------------------------------------------------
    # capabilities
    # ------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        units = {name: self.sources[name].unit for name in self.observation_names}
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
            self._observation_coords(name), valid_time, name=name,
            noise=self.sources[name].noise, stream=1, bias=0.0,
            source=self.sources[name],
        )

    def _observation_coords(self, name: str) -> Coords:
        """The grid this product comes on.

        Products do not share a footprint. An infrared satellite covers a whole
        hemisphere and a radar composite covers the range of one radar, so an
        observation arrives on whatever grid its own instrument has and the
        regridding is ours to do.
        """
        extent = self.sources[name].extent
        shape = self.sources[name].shape or self.obs_shape
        if extent is None and shape == self.obs_shape:
            return self.obs_coords
        return coords_from_extent(extent or self.obs_extent, shape)

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
        # A nowcast gets worse with lead time, and it gets worse in three ways at
        # once: the field it started from is older, it is noisier, and it has
        # spread. One number for all three would move them together in a way real
        # forecasts do not -- it is the *position* error that empties a high
        # threshold, and the *spread* that makes a low one over-forecast, and a
        # mesh whose rows all look alike is a mesh with nothing to read.
        #
        # ``lead / (lead + tau)`` rather than ``lead / horizon``: it saturates, so
        # it needs no upper bound and no knowledge of how far anybody asked, and a
        # forecast does not stop getting worse because a configuration stopped
        # asking for it.
        lead = int(lead_minutes)
        aging = lead / (lead + SKILL_TAU_MINUTES)
        return self._make_field(
            self.model_coords, valid_time, name=model_id,
            noise=self.forecast_noise * rank * (
                YOUNG_NOISE + (AGED_NOISE - YOUNG_NOISE) * aging),
            stream=2,
            gain=1.0 + AGED_GAIN * aging,
            bias=self.forecast_bias + AGED_SPREAD * aging,
            lag_minutes=self.forecast_lag_minutes * rank * (
                YOUNG_LAG + (AGED_LAG - YOUNG_LAG) * aging),
            source=self.model_sources.get(model_id),
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
        gain: float = 1.0,
        lag_minutes: float = 0.0,
        source: Source | None = None,
    ) -> Field:
        # A lagging forecast is the field from earlier, not a blurred one. It is
        # the honest way to be wrong: the structure is right and the position is
        # not, so a high threshold loses it entirely while a low one barely
        # notices — which is exactly what thresholds are for.
        data = self.blob(coords, valid_time - timedelta(minutes=lag_minutes))
        if noise:
            data = data + self._noise(coords.shape, valid_time, stream, noise)
        # A gain, not an offset. Both make the forecast cover too much ground, and
        # they are not the same mistake: a gain enlarges the echoes that are
        # already there, while an offset lifts the whole domain until the places
        # with no weather in them are raining too. The second is not a forecast
        # anybody makes, and at a low threshold it produced a frequency bias of
        # six -- a number no evaluation should show, because it is not about the
        # forecast, it is about the way the wrong thing was added.
        if gain != 1.0:
            data = data * gain
        if bias:
            data = data + bias
        # The product's unit change comes last, and it takes the noise and the
        # bias with it. Those numbers describe how wrong a forecast is, which is a
        # fraction of the field rather than a quantity of anything: a model that
        # is 2% wrong about a 255-scale field is 2% wrong about a 40-scale one,
        # and adding 5 to both would make the second prediction useless while the
        # first one looked fine. Measured in the truth's units and converted once.
        if source is not None and (source.scale != 1.0 or source.offset):
            data = data * source.scale
            if source.offset:
                data = data + source.offset
        vmin = self.vmin if source is None else source.vmin
        vmax = self.vmax if source is None else source.vmax
        if vmin is not None or vmax is not None:
            data = np.clip(
                data,
                vmin if vmin is not None else -np.inf,
                vmax if vmax is not None else np.inf,
            )
        mask = self._missing_mask(coords, valid_time, source=source)
        return Field(
            data=data,
            coords=coords,
            valid_time=valid_time,
            unit=self.unit if source is None else source.unit,
            variable=self.variable if source is None else source.variable,
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
        centre_lon = 0.5 * (lon0 + lon1)
        centre_lat = 0.5 * (lat0 + lat1)
        ref_lon, ref_lat = REFERENCE_SPAN
        grid_lon, grid_lat = np.meshgrid(coords.lon, coords.lat)
        minutes = self._minutes(valid_time)

        if index == 0 and self.drift == 0.0:
            phase = (minutes % 360.0) / 360.0
            cx = centre_lon + (0.20 + 0.60 * phase - 0.5) * ref_lon
            cy = centre_lat + (0.35 + 0.30 * math.sin(2.0 * math.pi * phase) - 0.5) * ref_lat
            amplitude = self.peak
            size = self.sigma
        else:
            home_lon, home_lat, amplitude, size = self._cell_shape(index)
            hours = minutes / 60.0
            # A steady flow, and no wrapping. A forecast that lags the truth is
            # then displaced the way a real one is, and a cell that leaves the
            # domain has genuinely left it.
            cx = centre_lon + (home_lon + self.drift * hours - 0.5) * ref_lon
            cy = centre_lat + (home_lat + 0.25 * self.drift * hours - 0.5) * ref_lat

        sx = max(size * ref_lon, 1e-9)
        sy = max(size * ref_lat, 1e-9)
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
        grid_lon, grid_lat = np.meshgrid(coords.lon, coords.lat)
        dx = (grid_lon - 0.5 * (lon0 + lon1)) / REFERENCE_SPAN[0]
        dy = (grid_lat - 0.5 * (lat0 + lat1)) / REFERENCE_SPAN[1]
        return self.background * np.exp(-2.0 * (dx * dx + dy * dy))

    def _noise(
        self, shape: tuple[int, int], valid_time: datetime, stream: int, amplitude: float
    ) -> np.ndarray:
        rng = np.random.default_rng([self.seed, self._minutes(valid_time), stream])
        return rng.normal(0.0, amplitude, size=shape)

    def _missing_mask(
        self, coords: Coords, valid_time: datetime, *, source: Source | None = None
    ) -> np.ndarray | None:
        shape = coords.shape
        kind = self.missing_kind if source is None else source.missing_kind
        points = self.missing_points if source is None else source.missing_points
        parts = []
        if kind == "range":
            parts.append(self._range_mask(coords))
        if kind != "none" and points > 0:
            # Speckle on top of a coverage gap is what a real composite looks
            # like, and the two exercise different code: a large contiguous hole
            # catches a resampling step that fills, scattered points catch one
            # that smears.
            rng = np.random.default_rng([self.seed, self._minutes(valid_time), 99])
            n = min(points, shape[0] * shape[1])
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
        # 0.58 of a span leaves about an eighth missing on a 10x12 degree domain:
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
