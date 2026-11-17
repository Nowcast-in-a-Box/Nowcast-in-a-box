"""Physical fields exchanged by ingestion, models and diagnostics."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Observations:
    source: str
    variable_id: str
    units: str
    values: Any
    times: tuple[str, ...]
    grid: dict[str, Any]
    mask: Any


@dataclass(frozen=True)
class Forecast:
    variable_id: str
    units: str
    values: Any
    times: tuple[str, ...]
    grid: dict[str, Any]
    mask: Any
    init_time: str
    lead_minutes: tuple[int, ...]
