"""A provider wrapper that counts calls.

Used by the acceptance test to prove that re-querying a stored result set never
touches the field provider — which is what "changing a CSI threshold does not
start ONNX again" means once inference is already out of the picture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .base import Capabilities, Field, FieldAccess


@dataclass
class AccessLog:
    """Call counters, grouped by method."""

    calls: dict[str, int] = field(default_factory=dict)
    prediction_seconds: float = 0.0

    def hit(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def total(self) -> int:
        return sum(self.calls.values())

    def reset(self) -> None:
        self.calls.clear()

    def __repr__(self) -> str:
        return f"AccessLog({self.calls})"


class CountingAccess:
    """Delegates to ``inner`` and records every call."""

    def __init__(self, inner: FieldAccess, log: AccessLog | None = None) -> None:
        self.inner = inner
        self.log = log if log is not None else AccessLog()

    def capabilities(self) -> Capabilities:
        self.log.hit("capabilities")
        return self.inner.capabilities()

    def list_observations(self, name: str, t0: datetime, t1: datetime):
        self.log.hit("list_observations")
        return self.inner.list_observations(name, t0, t1)

    def get_observation(self, name: str, valid_time: datetime) -> Field:
        self.log.hit("get_observation")
        return self.inner.get_observation(name, valid_time)

    def list_predictions(self, model_id: str, init_time: datetime) -> list[int]:
        self.log.hit("list_predictions")
        return self.inner.list_predictions(model_id, init_time)

    def get_prediction(
        self, model_id: str, init_time: datetime, lead_minutes: int
    ) -> Field:
        self.log.hit("get_prediction")
        return self.inner.get_prediction(model_id, init_time, lead_minutes)

    # convenience for tests / assertions

    @property
    def prediction_reads(self) -> int:
        """How many prediction fields have been pulled."""
        return self.log.calls.get("get_prediction", 0)

    @property
    def observation_reads(self) -> int:
        return self.log.calls.get("get_observation", 0)

    @property
    def field_reads(self) -> int:
        return self.prediction_reads + self.observation_reads
