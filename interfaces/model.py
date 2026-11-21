"""Backend-independent model contribution contract. Standard library only."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from interfaces.errors import ConstraintError
from interfaces.fields import Forecast, Observations


@dataclass(frozen=True)
class InputRequirements:
    variable_id: str
    units: str
    context_frames: int
    cadence_minutes: int
    spatial: tuple[int, int]
    dtype: str
    allow_missing: bool


@dataclass(frozen=True)
class OutputSpecification:
    variable_id: str
    units: str
    lead_minutes: tuple[int, ...]
    spatial: tuple[int, int]
    dtype: str
    allow_missing: bool
    grid: str


@dataclass(frozen=True)
class WeightSpec:
    status: str
    download_url: str | None
    sha256: str | None
    filename: str | None
    hf: dict[str, str] | None
    artifacts_relpath: str | None


@dataclass(frozen=True)
class ModelCapabilities:
    id: str
    version: str
    data_adapter_id: str
    task: str
    status: str


class ModelAdapter(ABC):
    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        """Return identity without loading a backend or weights."""

    @abstractmethod
    def weights(self) -> WeightSpec:
        """Describe weights without downloading them."""

    @abstractmethod
    def input_requirements(self) -> InputRequirements:
        """Describe the physical observations required by this version."""

    @abstractmethod
    def output_specification(self) -> OutputSpecification:
        """Describe physical forecast fields, excluding backend batch axes."""

    def validate_input(self, observations: Observations) -> None:
        from utils.model_contract import validate_input

        validate_input(observations, self.input_requirements(), self.capabilities())

    @abstractmethod
    def prepare(self, observations: Observations) -> Any:
        """Convert valid observations into a backend-private input."""

    @abstractmethod
    def predict(self, prepared: Any, *, weights_path: str) -> Forecast:
        """Infer offline using already prepared, verified local weights."""


class DeclaredModelAdapter(ModelAdapter):
    """Common queries over the declaration shipped inside a model image."""

    declaration_path: Path

    def __init__(self):
        with self.declaration_path.open(encoding="utf-8") as stream:
            self._declaration = json.load(stream)
        if self._declaration.get("schema") != "nib.model.v1":
            raise ConstraintError("unsupported model declaration schema")
        # Validate declarations even when callers only query identity.
        self.input_requirements()
        self.output_specification()

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            **{
                key: self._declaration[key]
                for key in ModelCapabilities.__dataclass_fields__
            }
        )

    def weights(self) -> WeightSpec:
        return WeightSpec(**self._declaration["weights"])

    def input_requirements(self) -> InputRequirements:
        value = dict(self._declaration["input"])
        value["spatial"] = tuple(value["spatial"])
        result = InputRequirements(**value)
        _field_requirements(result)
        if any(
            type(v) is not int or v < 1
            for v in (result.context_frames, result.cadence_minutes)
        ):
            raise ConstraintError("history and cadence must be positive integers")
        return result

    def output_specification(self) -> OutputSpecification:
        value = dict(self._declaration["output"])
        value["spatial"] = tuple(value["spatial"])
        value["lead_minutes"] = tuple(value["lead_minutes"])
        result = OutputSpecification(**value)
        _field_requirements(result)
        leads = result.lead_minutes
        if (
            not leads
            or any(type(v) is not int or v <= 0 for v in leads)
            or any(b <= a for a, b in zip(leads, leads[1:]))
        ):
            raise ConstraintError(
                "forecast leads must be increasing positive integer minutes"
            )
        if result.grid not in ("input", "explicit"):
            raise ConstraintError("output grid must be input or explicit")
        return result


def _field_requirements(value):
    if not value.variable_id or not value.units or value.dtype != "float32":
        raise ConstraintError("fields require variable, units and float32 dtype")
    if len(value.spatial) != 2 or any(
        type(v) is not int or v < 2 for v in value.spatial
    ):
        raise ConstraintError(
            "spatial must contain two dimensions of at least two cells"
        )
    if type(value.allow_missing) is not bool:
        raise ConstraintError("allow_missing must be boolean")
