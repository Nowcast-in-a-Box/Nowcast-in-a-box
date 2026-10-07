"""Model adapter contract. Standard library only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from interfaces.data import DataAdapter, DataBundle


@dataclass(frozen=True)
class AxisSpec:
    name: str
    description: str
    size: int | None
    units: str | None = None


@dataclass(frozen=True)
class ForecastSpec:
    dims: tuple[str, ...]
    dtype: str
    variable_id: str | None
    units: str | None
    description: str
    constraints_status: str
    axes: tuple[AxisSpec, ...]


@dataclass(frozen=True)
class Forecast:
    spec: ForecastSpec
    shape: tuple[int, ...]
    values: Any
    coords: dict[str, Any]


@dataclass(frozen=True)
class PreparedInput:
    variable_id: str
    units: str | None
    dtype: str
    shape: tuple[int, ...]
    values: Any
    coords: dict[str, Any]


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
    """Inference contract for one data-model pair."""

    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        """Return facts declared on the class."""

    @abstractmethod
    def weights(self) -> WeightSpec:
        """Return the pinned weight pointer. Do not download."""

    @abstractmethod
    def output_spec(self) -> ForecastSpec:
        """Return the forecast shape declared for this model."""

    @abstractmethod
    def validate_input(self, bundle: DataBundle, data_adapter: DataAdapter) -> None:
        """Check the bundle against the paired data adapter and this model."""

    @abstractmethod
    def prepare(self, bundle: DataBundle, data_adapter: DataAdapter) -> PreparedInput:
        """Stack the paired variable into the tensor this model runs on."""

    @abstractmethod
    def predict(self, prepared: PreparedInput, *, weights_path: str) -> Forecast:
        """Run inference. Fail if the weight file is not ready."""
