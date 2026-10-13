"""Field access contract for the verification module.

The contract is deliberately small: five methods and three data classes.  The
platform is expected to provide the real implementation later; until it exists,
``nib_verify.access.synthetic`` and ``nib_verify.access.replay`` satisfy it.

See ``spec/field-access.md`` for the prose specification and the five
guarantees a provider has to meet.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

import numpy as np


class FieldUnavailable(Exception):
    """The provider cannot serve the requested field.

    Raised for a time that is off the declared cadence, a lead the model does
    not produce, or an observation that was never recorded.  It is never a
    reason to interpolate in time — the cadence is part of the ruler.
    """


class PredictionUnavailable(FieldUnavailable):
    """A forecast gap, distinct from an unavailable observation."""


@dataclass(frozen=True)
class Coords:
    """Native grid coordinates.  Both axes are 1-D and strictly increasing."""

    lon: np.ndarray
    lat: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "lon", np.asarray(self.lon, dtype=float))
        object.__setattr__(self, "lat", np.asarray(self.lat, dtype=float))
        if self.lon.ndim != 1 or self.lat.ndim != 1:
            raise ValueError("Coords axes must be 1-D")
        if self.lon.size < 2 or self.lat.size < 2:
            raise ValueError("Coords axes need at least two points")
        if np.any(np.diff(self.lon) <= 0) or np.any(np.diff(self.lat) <= 0):
            raise ValueError("Coords axes must be strictly increasing")

    @property
    def crs(self) -> str:
        return "EPSG:4326"

    @property
    def x(self) -> np.ndarray:
        return self.lon

    @property
    def y(self) -> np.ndarray:
        return self.lat

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.lat.size), int(self.lon.size))

    @property
    def extent(self) -> tuple[float, float, float, float]:
        """``(lon_min, lat_min, lon_max, lat_max)``."""
        return (
            float(self.lon[0]),
            float(self.lat[0]),
            float(self.lon[-1]),
            float(self.lat[-1]),
        )


@dataclass(frozen=True)
class ProjectedCoords:
    """Regular projected axes with an explicit CRS; never labelled lon/lat."""

    x: np.ndarray
    y: np.ndarray
    crs: str

    def __post_init__(self) -> None:
        if not self.crs or self.crs == "EPSG:4326":
            raise ValueError("projected grid requires a projected CRS")
        for name in ("x", "y"):
            axis = np.asarray(getattr(self, name), dtype=float)
            if (
                axis.ndim != 1
                or axis.size < 2
                or not np.isfinite(axis).all()
                or not np.all(np.diff(axis) > 0)
            ):
                raise ValueError(
                    "projected axes must be finite and strictly increasing"
                )
            object.__setattr__(self, name, axis)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.y.size, self.x.size)

    @property
    def extent(self) -> tuple[float, float, float, float]:
        return (
            float(self.x[0]),
            float(self.y[0]),
            float(self.x[-1]),
            float(self.y[-1]),
        )


@dataclass
class Field:
    """One 2-D field on its own native grid.

    ``mask`` marks missing points with ``True``.  Missing is never encoded as
    zero: a provider that has no value must set the mask instead.
    """

    data: np.ndarray
    coords: Coords | ProjectedCoords
    valid_time: datetime
    unit: str
    variable: str
    name: str
    mask: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.data = np.asarray(self.data, dtype=float)
        if self.data.ndim != 2:
            raise ValueError(f"Field data must be 2-D, got shape {self.data.shape}")
        if self.data.shape != self.coords.shape:
            raise ValueError(
                f"Field data {self.data.shape} does not match coords {self.coords.shape}"
            )
        if self.mask is not None:
            self.mask = np.asarray(self.mask, dtype=bool)
            if self.mask.shape != self.data.shape:
                raise ValueError("mask must have the same shape as data")

    @property
    def missing(self) -> np.ndarray:
        if self.mask is None:
            return np.zeros(self.data.shape, dtype=bool)
        return self.mask

    @property
    def valid(self) -> np.ndarray:
        return ~self.missing

    def values(self) -> np.ndarray:
        """Numeric values with missing points set to NaN — never to zero."""
        out = self.data.astype(float, copy=True)
        out[self.missing] = np.nan
        return out

    def describe(self) -> str:
        return (
            f"{self.name}@{self.valid_time.isoformat()} "
            f"{self.variable}[{self.unit}] {self.coords.shape}"
        )


@dataclass(frozen=True)
class Capabilities:
    """What a provider can serve.  Configuration validation reads this."""

    cadence_minutes: int
    observation_names: tuple[str, ...]
    model_ids: tuple[str, ...]
    retrospective: bool = True
    units: dict[str, str] = dataclasses.field(default_factory=dict)

    def unit_of(self, name: str) -> str | None:
        return self.units.get(name)


@runtime_checkable
class FieldAccess(Protocol):
    """The contract the platform will implement.

    Times are timezone-aware UTC ``datetime`` objects.  Every returned
    :class:`Field` carries its own coordinates, so the consumer never has to
    know where the bytes came from.
    """

    def capabilities(self) -> Capabilities:
        """Declare cadence, available names, and whether history is served."""
        ...

    def list_observations(
        self, name: str, t0: datetime, t1: datetime
    ) -> list[datetime]:
        """Cadence-aligned times in ``[t0, t1]`` for which ``name`` has data."""
        ...

    def get_observation(self, name: str, valid_time: datetime) -> Field:
        """One observation field at an exact ``valid_time``."""
        ...

    def list_predictions(self, model_id: str, init_time: datetime) -> list[int]:
        """Lead times, in minutes, that this run actually produced."""
        ...

    def get_prediction(
        self, model_id: str, init_time: datetime, lead_minutes: int
    ) -> Field:
        """One prediction field for ``init_time`` + ``lead_minutes``."""
        ...


def coords_from_extent(
    extent: tuple[float, float, float, float], shape: tuple[int, int]
) -> Coords:
    """Build a regular grid from ``(lon0, lat0, lon1, lat1)`` and ``(ny, nx)``."""
    lon0, lat0, lon1, lat1 = extent
    ny, nx = shape
    return Coords(lon=np.linspace(lon0, lon1, nx), lat=np.linspace(lat0, lat1, ny))


def describe_capabilities(caps: Capabilities) -> str:
    return (
        f"cadence={caps.cadence_minutes}min "
        f"obs={list(caps.observation_names)} "
        f"models={list(caps.model_ids)} "
        f"retrospective={caps.retrospective}"
    )
