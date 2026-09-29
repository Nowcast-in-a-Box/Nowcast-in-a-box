"""The verification config — one config, one ruler, one set of tasks.

Three parts with three different mutability rules, and the rules are enforced
here rather than left to convention:

======================  ==============================================
ruler                   immutable; changing it means a new config
roster                  free to change, through effective intervals
metrics                 append-only
======================  ==============================================

The ruler is immutable because it decides the denominator and the mask of every
metric.  Change the truth source and the set of grid points changes; change the
extent and the CSI denominator changes; change the resolution and both the grid
and the regridded values change.  A reading taken under a different ruler is not
comparable with the old one, so it must not silently replace it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from ..core.metrics import REGISTRY, spec_for

STORAGE_FAMILIES = ("cat", "cont", "mom", "spatial")

#: metric family -> the storage family its rows live in
FAMILY_TABLE = {
    "categorical": "cat",
    "continuous": "cont",
    "moments": "mom",
    "spatial": "spatial",
}


class ConfigError(ValueError):
    """A config that must not be loaded."""


@dataclass(frozen=True)
class Ruler:
    """The immutable part.

    There is no ``variable`` here. The ground truth package already names the
    field it serves, so asking for it again was asking a person to retype what
    the platform had declared — and nothing ever compared the answer to anything.
    """

    ground_truth: str
    unit: str
    time_step_minutes: int
    extent: tuple[float, float, float, float]
    resolution: float

    def __post_init__(self) -> None:
        if self.time_step_minutes <= 0:
            raise ConfigError("time_step_minutes must be positive")
        if self.resolution <= 0:
            raise ConfigError("resolution must be positive")
        if len(self.extent) != 4:
            raise ConfigError("extent must be (lon0, lat0, lon1, lat1)")
        lon0, lat0, lon1, lat1 = self.extent
        if lon1 < lon0 or lat1 < lat0:
            raise ConfigError("extent must satisfy lon1 >= lon0 and lat1 >= lat0")
        if not self.unit:
            raise ConfigError("unit is required: thresholds are meaningless without it")

    @property
    def fingerprint(self) -> str:
        """Stable hash of the immutable fields only."""
        payload = json.dumps(
            {
                "ground_truth": self.ground_truth,
                "unit": self.unit,
                "time_step_minutes": self.time_step_minutes,
                "extent": list(self.extent),
                "resolution": self.resolution,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Member:
    """One forecast member on the roster."""

    name: str
    variable: str
    unit: str
    leads: tuple[int, ...]
    active_from: date | None = None
    active_to: date | None = None

    def __post_init__(self) -> None:
        if not self.leads:
            raise ConfigError(f"member {self.name!r} has no lead times")
        if any(l <= 0 for l in self.leads):
            raise ConfigError(f"member {self.name!r} has a non-positive lead")
        if self.active_from and self.active_to and self.active_to < self.active_from:
            raise ConfigError(f"member {self.name!r} ends before it starts")

    def active_on(self, day: date) -> bool:
        if self.active_from and day < self.active_from:
            return False
        if self.active_to and day > self.active_to:
            return False
        return True


@dataclass(frozen=True)
class MetricGroup:
    """A family of metrics sharing one set of parameters."""

    family: str
    items: tuple[str, ...]
    levels: tuple[float, ...] = ()
    half_windows: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.family not in FAMILY_TABLE:
            raise ConfigError(
                f"unknown metric family {self.family!r}; "
                f"expected one of {sorted(FAMILY_TABLE)}"
            )
        if not self.items:
            raise ConfigError(f"metric family {self.family!r} is empty")
        for item in self.items:
            try:
                spec = spec_for(item)
            except KeyError as exc:
                raise ConfigError(str(exc)) from None
            if spec.needs_level and not self.levels:
                raise ConfigError(f"metric {item!r} needs at least one level")
            if spec.needs_window and not self.half_windows:
                raise ConfigError(f"metric {item!r} needs at least one half_window")
        if any(h < 0 for h in self.half_windows):
            raise ConfigError("half_windows must be >= 0")

    @property
    def storage_family(self) -> str:
        return FAMILY_TABLE[self.family]


@dataclass(frozen=True)
class VerificationConfig:
    """One ruler, one roster, one metric list."""

    group_id: str
    name: str
    ruler: Ruler
    members: tuple[Member, ...]
    metrics: tuple[MetricGroup, ...]

    def __post_init__(self) -> None:
        if not self.group_id:
            raise ConfigError("group_id is required")
        if not self.members:
            raise ConfigError("at least one member is required")
        if not self.metrics:
            raise ConfigError("at least one metric group is required")
        seen = {m.name for m in self.members}
        if len(seen) != len(self.members):
            raise ConfigError("duplicate member names on the roster")
        for m in self.members:
            if m.unit != self.ruler.unit:
                raise ConfigError(
                    f"member {m.name!r} is in {m.unit!r} but the ruler is in "
                    f"{self.ruler.unit!r}; thresholds would be meaningless"
                )
        items = [item for g in self.metrics for item in g.items]
        duplicates = sorted({i for i in items if items.count(i) > 1})
        if duplicates:
            raise ConfigError(f"metric listed more than once: {duplicates}")

    # -- derived views --------------------------------------------------
    @property
    def ruler_fingerprint(self) -> str:
        return self.ruler.fingerprint

    def families(self) -> tuple[str, ...]:
        """Storage families this config writes, in a stable order."""
        return tuple(dict.fromkeys(g.storage_family for g in self.metrics))

    def levels(self) -> tuple[float, ...]:
        out: list[float] = []
        for g in self.metrics:
            out.extend(g.levels)
        return tuple(dict.fromkeys(out))

    def half_windows(self) -> tuple[int, ...]:
        out: list[int] = []
        for g in self.metrics:
            out.extend(g.half_windows)
        return tuple(dict.fromkeys(out))

    def levels_for(self, family: str) -> tuple[float, ...]:
        out: list[float] = []
        for g in self.metrics:
            if g.storage_family == family:
                out.extend(g.levels)
        return tuple(dict.fromkeys(out))

    def half_windows_for(self, family: str) -> tuple[int, ...]:
        out: list[int] = []
        for g in self.metrics:
            if g.storage_family == family:
                out.extend(g.half_windows)
        return tuple(dict.fromkeys(out))

    def metrics_for(self, method: str) -> bool:
        spec = spec_for(method)
        return any(spec.name in g.items for g in self.metrics)

    def member(self, name: str) -> Member:
        for m in self.members:
            if m.name == name:
                return m
        raise KeyError(name)

    def leads(self) -> tuple[int, ...]:
        out: list[int] = []
        for m in self.members:
            out.extend(m.leads)
        return tuple(sorted(set(out)))

    # -- change detection ----------------------------------------------
    def allows_modification_of(self, other: "VerificationConfig") -> tuple[str, ...]:
        """Which fields changed that force a *new* config rather than an edit."""
        problems: list[str] = []
        if self.ruler != other.ruler:
            problems.append("ruler")
        dropped = _dropped_metrics(other, self)
        if dropped:
            problems.append("metrics:" + ",".join(sorted(dropped)))
        return tuple(problems)

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "group": {"id": self.group_id, "name": self.name},
            "ruler": {
                "ground_truth": self.ruler.ground_truth,
                "unit": self.ruler.unit,
                "time_step_minutes": self.ruler.time_step_minutes,
                "extent": list(self.ruler.extent),
                "resolution": self.ruler.resolution,
                "fingerprint": self.ruler.fingerprint,
            },
            "members": [
                {
                    "name": m.name,
                    "variable": m.variable,
                    "unit": m.unit,
                    "leads": list(m.leads),
                    "active_from": m.active_from.isoformat() if m.active_from else None,
                    "active_to": m.active_to.isoformat() if m.active_to else None,
                }
                for m in self.members
            ],
            "metrics": [
                {
                    "family": g.family,
                    # the family a config names, and the family the store uses.
                    # Two vocabularies, because a config groups metrics by what
                    # they mean and the store groups them by how they accumulate.
                    "storage_family": g.storage_family,
                    "items": list(g.items),
                    "levels": list(g.levels),
                    "half_windows": list(g.half_windows),
                }
                for g in self.metrics
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VerificationConfig":
        if not isinstance(data, Mapping):
            raise ConfigError("config must be a mapping")
        group = data.get("group") or {}
        if not isinstance(group, Mapping):
            raise ConfigError("group must be a mapping")
        ruler_in = data.get("ruler")
        if not isinstance(ruler_in, Mapping):
            raise ConfigError("ruler is required (ground_truth, unit, "
                              "time_step_minutes, extent, resolution)")
        try:
            ruler = Ruler(
                ground_truth=str(ruler_in["ground_truth"]),
                unit=str(ruler_in["unit"]),
                time_step_minutes=int(ruler_in["time_step_minutes"]),
                extent=tuple(float(x) for x in ruler_in["extent"]),
                resolution=float(ruler_in["resolution"]),
            )
        except KeyError as exc:
            raise ConfigError(f"ruler is missing {exc.args[0]!r}") from None

        members = []
        for raw in data.get("members") or []:
            if not isinstance(raw, Mapping):
                raise ConfigError("each member must be a mapping")
            members.append(
                Member(
                    name=str(raw["name"]),
                    variable=str(raw.get("variable", "")),
                    unit=str(raw.get("unit", ruler.unit)),
                    leads=tuple(int(x) for x in raw["leads"]),
                    active_from=_parse_date(raw.get("active_from")),
                    active_to=_parse_date(raw.get("active_to")),
                )
            )

        metrics = []
        for raw in data.get("metrics") or []:
            if not isinstance(raw, Mapping):
                raise ConfigError("each metric group must be a mapping")
            metrics.append(
                MetricGroup(
                    family=str(raw["family"]),
                    items=tuple(str(x) for x in raw["items"]),
                    levels=tuple(float(x) for x in raw.get("levels", ())),
                    half_windows=tuple(int(x) for x in raw.get("half_windows", ())),
                )
            )

        return cls(
            group_id=str(group.get("id", "")),
            name=str(group.get("name", "")),
            ruler=ruler,
            members=tuple(members),
            metrics=tuple(metrics),
        )

    @classmethod
    def from_json(cls, text: str) -> "VerificationConfig":
        return cls.from_dict(json.loads(text))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    # -- roster edits ----------------------------------------------------
    def with_member(self, member: Member, *, retire_on: date | None = None):
        """Add a member, or stop an existing one with ``retire_on``."""
        members = list(self.members)
        if retire_on is not None:
            for i, m in enumerate(members):
                if m.name == member.name:
                    members[i] = replace(m, active_to=retire_on)
                    return replace(self, members=tuple(members))
            raise KeyError(member.name)
        for m in members:
            if m.name == member.name:
                raise ConfigError(f"member {member.name!r} is already on the roster")
        members.append(member)
        return replace(self, members=tuple(members))

    def with_metric_group(self, group: MetricGroup):
        """Append a metric group.

        Metrics are append-only: an existing entry is never edited and never
        removed, so a previously published number keeps its definition.  Adding
        a metric that is already there is refused rather than ignored.
        """
        have = {item for g in self.metrics for item in g.items}
        duplicates = sorted(set(group.items) & have)
        if duplicates:
            raise ConfigError(
                f"metrics already present, refused: {duplicates}. "
                "Metrics are append-only; retire the config instead of editing it."
            )
        return replace(self, metrics=self.metrics + (group,))


# ----------------------------------------------------------------------
def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def _dropped_metrics(before: "VerificationConfig", after: "VerificationConfig") -> set[str]:
    """Metrics present in ``before`` but gone in ``after``."""
    had = {item for g in before.metrics for item in g.items}
    have = {item for g in after.metrics for item in g.items}
    return had - have


def load(path: str | "Path") -> VerificationConfig:  # noqa: F821
    from pathlib import Path as _Path

    p = _Path(path)
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".json":
        return VerificationConfig.from_json(text)
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ConfigError(
            "YAML configs need PyYAML; install it or store the config as JSON"
        ) from exc
    return VerificationConfig.from_dict(yaml.safe_load(text))


def dump(cfg: VerificationConfig, path: str | "Path") -> None:  # noqa: F821
    from pathlib import Path as _Path

    p = _Path(path)
    text = cfg.to_json()
    try:
        import yaml  # type: ignore
        if p.suffix.lower() in (".yaml", ".yml"):
            text = yaml.safe_dump(cfg.to_dict(), sort_keys=False, allow_unicode=True)
    except ModuleNotFoundError:
        pass
    p.write_text(text, encoding="utf-8")
