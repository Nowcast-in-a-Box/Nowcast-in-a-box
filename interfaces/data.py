"""Data adapter contract. Standard library only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

from interfaces.errors import ConstraintError
from utils.paths import resolve_data_dir


@dataclass(frozen=True)
class VariableSpec:
    id: str
    long_name: str
    units: str


@dataclass(frozen=True)
class ChannelSpec:
    mode: str
    variables: tuple[VariableSpec, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"open", "closed"}:
            raise ConstraintError("channels.mode must be 'open' or 'closed'")
        if self.mode == "closed" and not self.variables:
            raise ConstraintError("closed channels require at least one variable")


@dataclass(frozen=True)
class SpatialSpec:
    crop: bool
    bbox_order: tuple[str, str, str, str] = ("west", "south", "east", "north")


@dataclass(frozen=True)
class DataCapabilities:
    id: str
    version: str
    title: str
    instrument: str
    channels: ChannelSpec
    spatial: SpatialSpec
    native_cadence_minutes: int
    formats: tuple[str, ...]


@dataclass(frozen=True)
class DataRequest:
    source: str
    data_dir: str
    data_root: str | None
    initial_time: str
    history_steps: int
    interval_minutes: int
    channels: tuple[str, ...]
    bbox: tuple[float, float, float, float]


class DataBundle(dict):
    """Common loader result.

    Keys are source, request, data, source_files, and metadata.
    Array values stay as whatever the loader produced; this module does
    not import xarray.
    """


class DataAdapter(ABC):
    """Packaging-time data source. Subclasses must not grow attributes at runtime."""

    @abstractmethod
    def capabilities(self) -> DataCapabilities:
        """Return facts declared on the class. Do not open files."""

    @abstractmethod
    def validate(self, request: DataRequest) -> None:
        """Reject a request this source cannot satisfy."""

    @abstractmethod
    def load(self, request: DataRequest) -> DataBundle:
        """Read local files and return a DataBundle. Do not download."""


class DeclaredDataAdapter(DataAdapter):
    """Data adapter whose capabilities are a class constant."""

    CAPABILITIES: DataCapabilities

    def capabilities(self) -> DataCapabilities:
        value = type(self).CAPABILITIES
        if not isinstance(value, DataCapabilities):
            raise ConstraintError(
                f"{type(self).__name__}.CAPABILITIES must be a DataCapabilities"
            )
        return value

    def validate(self, request: DataRequest) -> None:
        validate_request(request, self.capabilities())


def request_from_mapping(
    config: Mapping[str, Any],
    data_root: str | None = None,
) -> DataRequest:
    required = (
        "source",
        "data_dir",
        "initial_time",
        "history_steps",
        "interval_minutes",
        "channels",
        "region",
    )
    missing = [key for key in required if key not in config]
    if missing:
        raise ConstraintError(f"Missing common config fields: {missing}")

    region = config["region"]
    if not isinstance(region, Mapping) or "bbox" not in region:
        raise ConstraintError("region must contain bbox: [west, south, east, north]")
    bbox = region["bbox"]
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ConstraintError("region.bbox must be [west, south, east, north]")
    if not all(isinstance(value, (int, float)) for value in bbox):
        raise ConstraintError("region.bbox values must be numeric")

    channels = config["channels"]
    if not isinstance(channels, (list, tuple)):
        raise ConstraintError("channels must be a list")

    return DataRequest(
        source=str(config["source"]).lower(),
        data_dir=str(config["data_dir"]),
        data_root=data_root,
        initial_time=str(config["initial_time"]),
        history_steps=int(config["history_steps"]),
        interval_minutes=int(config["interval_minutes"]),
        channels=tuple(str(channel) for channel in channels),
        bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
    )


def validate_request(request: DataRequest, capabilities: DataCapabilities) -> None:
    if request.source != capabilities.id:
        raise ConstraintError(
            f"source {request.source!r} does not match adapter {capabilities.id!r}"
        )
    if request.history_steps < 1:
        raise ConstraintError("history_steps must be >= 1")
    if request.interval_minutes < 1:
        raise ConstraintError("interval_minutes must be >= 1")
    if not request.channels:
        raise ConstraintError("channels must be a non-empty list")

    west, south, east, north = request.bbox
    if west >= east or south >= north:
        raise ConstraintError(
            "Invalid region.bbox: require west < east and south < north"
        )

    if capabilities.channels.mode == "closed":
        allowed = {variable.id for variable in capabilities.channels.variables}
        unknown = [channel for channel in request.channels if channel not in allowed]
        if unknown:
            raise ConstraintError(
                f"unsupported channels {unknown}; allowed: {sorted(allowed)}"
            )

    resolve_data_dir(request.data_dir, request.data_root)


def loader_config(request: DataRequest) -> dict[str, Any]:
    """Absolute data_dir for an existing loader. Does not invent a path."""
    resolved = resolve_data_dir(request.data_dir, request.data_root)
    if not resolved.exists():
        raise FileNotFoundError(f"Data directory does not exist: {resolved}")
    return {
        "source": request.source,
        "data_dir": str(resolved),
        "initial_time": request.initial_time,
        "history_steps": request.history_steps,
        "interval_minutes": request.interval_minutes,
        "channels": list(request.channels),
        "region": {"bbox": list(request.bbox)},
    }


def bundle_from_parts(
    source: str,
    request: DataRequest,
    data: dict[str, dict[str, Any]],
    source_files: dict[str, dict[str, list[str]]],
    metadata: dict[str, Any],
) -> DataBundle:
    recorded = {
        "source": request.source,
        "data_dir": request.data_dir,
        "data_root": request.data_root,
        "initial_time": request.initial_time,
        "history_steps": request.history_steps,
        "interval_minutes": request.interval_minutes,
        "channels": list(request.channels),
        "region": {"bbox": list(request.bbox)},
    }
    return DataBundle(
        source=source,
        request=recorded,
        data=data,
        source_files=source_files,
        metadata=metadata,
    )
