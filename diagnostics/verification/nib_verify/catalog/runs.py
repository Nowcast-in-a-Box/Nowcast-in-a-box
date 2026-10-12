"""Run records.

A run config is what the Hub writes and the handler validates before starting a
container — `contracts/README.md` documents it as a closed YAML v1:

```yaml
version: 1
data:  { id: sevir-vil }
model: { id: wadepre }
run:   { start_time: "2017-06-13T15:05:00Z", forecast_horizon: 20 }
```

Three things shape this module.

* **Which file is a run config is decided by content, not by location.** No
  layout is defined for run output, and `AGENTS.md` forbids inventing one, so we
  parse candidate files and keep the ones that have the run-config shape.
* **Unknown keys are errors**, as the contract says. A file we would have
  misread is worse than a file we refuse.
* **`forecast_horizon` is a frame count, not a duration.** Turning it into
  minutes needs the cadence, which the package manifest supplies. This module
  keeps the frame count and stays out of that conversion.

A run config says nothing about where the output went, and nothing about whether
the run succeeded. Both questions are answered by the field provider — it either
can serve a lead or it cannot. That is why this module is small.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .packages import _int, _mapping, _text  # shared tolerant readers

UTC = timezone.utc

#: the closed set `contracts/README.md` defines
TOP_LEVEL_KEYS = frozenset({"version", "data", "model", "run"})
DATA_KEYS = frozenset({"id"})
MODEL_KEYS = frozenset({"id"})
RUN_KEYS = frozenset({"start_time", "sample_id", "forecast_horizon"})

CANDIDATE_SUFFIXES = (".yaml", ".yml", ".json")


class RunConfigError(ValueError):
    """A run config that cannot be read."""


@dataclass(frozen=True)
class RunConfig:
    """One run, as the Hub declared it."""

    version: int
    data_id: str
    model_id: str
    path: Path | None = None
    start_time: datetime | None = None
    sample_id: str | None = None
    forecast_horizon: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def directory(self) -> Path | None:
        """The directory the run config sits in.

        Not a promise about where output lives — just the only path the
        platform actually gave us.
        """
        return self.path.parent if self.path else None

    @property
    def when(self) -> str:
        if self.start_time is not None:
            return self.start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"sample {self.sample_id}" if self.sample_id else "(no time)"

    def leads_within_horizon(self, cadence_minutes: int) -> list[int]:
        """Lead times in minutes the run could have produced.

        ``forecast_horizon`` is a frame count, so this is the only place the
        frame-to-minute conversion happens. Whether the leads were *actually*
        produced is the provider's answer, not ours.
        """
        if not self.forecast_horizon or cadence_minutes <= 0:
            return []
        return [cadence_minutes * n for n in range(1, int(self.forecast_horizon) + 1)]

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "data": {"id": self.data_id},
            "model": {"id": self.model_id},
            "run": {
                "start_time": self.start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                if self.start_time else None,
                "sample_id": self.sample_id,
                "forecast_horizon": self.forecast_horizon,
            },
            "path": str(self.path) if self.path else None,
        }

    def describe(self) -> str:
        return (f"{self.when}  {self.model_id} <- {self.data_id}"
                f"  horizon={self.forecast_horizon}")

    # -- parsing ---------------------------------------------------------
    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any], path: Path | None = None, *, strict: bool = True
    ) -> "RunConfig":
        if not isinstance(raw, Mapping):
            raise RunConfigError("run config must be a mapping")

        if strict:
            unknown = sorted(set(raw) - TOP_LEVEL_KEYS)
            if unknown:
                raise RunConfigError(
                    f"unknown top-level key(s): {unknown}; "
                    f"allowed: {sorted(TOP_LEVEL_KEYS)}"
                )

        data = _mapping(raw.get("data"))
        model = _mapping(raw.get("model"))
        run = _mapping(raw.get("run"))

        if strict:
            for section, allowed in ((data, DATA_KEYS), (model, MODEL_KEYS), (run, RUN_KEYS)):
                bad = sorted(set(section) - allowed)
                if bad:
                    raise RunConfigError(f"unknown key(s) {bad} in a run config section")

        data_id = _text(data.get("id"))
        model_id = _text(model.get("id"))
        if not data_id:
            raise RunConfigError("run config has no data.id")
        if not model_id:
            raise RunConfigError("run config has no model.id")

        start_time = _parse_time(run.get("start_time"))
        sample_id = _text(run.get("sample_id"))
        if start_time is None and not sample_id:
            raise RunConfigError("run config needs run.start_time or run.sample_id")

        horizon = _int(run.get("forecast_horizon"))
        if horizon is not None and horizon <= 0:
            raise RunConfigError("run.forecast_horizon must be positive")

        return cls(
            version=_int(raw.get("version")) or 1,
            data_id=data_id,
            model_id=model_id,
            path=path,
            start_time=start_time,
            sample_id=sample_id,
            forecast_horizon=horizon,
            raw=dict(raw),
        )


# ----------------------------------------------------------------------
# discovery
# ----------------------------------------------------------------------
def looks_like_run_config(raw: Any) -> bool:
    """True when a parsed document has the run-config shape.

    Deliberately loose: this decides *whether to try*, not whether the file is
    valid. A file that passes this and then fails parsing is reported, because a
    broken run config is something the operator wants to know about.
    """
    if not isinstance(raw, Mapping):
        return False
    if not all(k in raw for k in ("data", "model", "run")):
        return False
    data = raw.get("data")
    model = raw.get("model")
    return isinstance(data, Mapping) and isinstance(model, Mapping)


def find_candidate_files(root: str | Path) -> list[Path]:
    base = Path(root)
    if not base.exists():
        return []
    if base.is_file():
        return [base] if base.suffix.lower() in CANDIDATE_SUFFIXES else []
    return sorted(
        p for p in base.rglob("*")
        if p.is_file() and p.suffix.lower() in CANDIDATE_SUFFIXES
    )


def load_run_config(path: str | Path, *, strict: bool = True) -> RunConfig:
    p = Path(path)
    raw = _read_document(p)
    if not looks_like_run_config(raw):
        raise RunConfigError(f"{p}: does not look like a run config")
    return RunConfig.from_mapping(raw, path=p, strict=strict)


def _read_document(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RunConfigError(f"{path}: {exc}") from None
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RunConfigError("reading YAML run configs needs PyYAML") from exc
    try:
        return yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001
        raise RunConfigError(f"{path}: {exc}") from None


@dataclass
class RunBook:
    """The run configs we found."""

    runs: tuple[RunConfig, ...] = ()
    roots: tuple[Path, ...] = ()
    skipped: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[RunConfig]:
        return iter(self.runs)

    def __len__(self) -> int:
        return len(self.runs)

    def __bool__(self) -> bool:
        return bool(self.runs)

    # -- views ----------------------------------------------------------
    def for_model(self, model_id: str) -> list[RunConfig]:
        return [r for r in self.runs if r.model_id == model_id]

    def for_data(self, data_id: str) -> list[RunConfig]:
        return [r for r in self.runs if r.data_id == data_id]

    @property
    def model_ids(self) -> set[str]:
        return {r.model_id for r in self.runs}

    @property
    def data_ids(self) -> set[str]:
        return {r.data_id for r in self.runs}

    def span(self) -> tuple[datetime | None, datetime | None]:
        times = [r.start_time for r in self.runs if r.start_time]
        return (min(times), max(times)) if times else (None, None)

    def as_dict(self) -> dict:
        first, last = self.span()
        return {
            "count": len(self.runs),
            "span": {
                "from": first.strftime("%Y-%m-%dT%H:%M:%SZ") if first else None,
                "to": last.strftime("%Y-%m-%dT%H:%M:%SZ") if last else None,
            },
            "models": sorted(self.model_ids),
            "data": sorted(self.data_ids),
            "runs": [r.as_dict() for r in self.runs],
            "skipped": list(self.skipped),
        }


def discover(roots: Sequence[str | Path], *, strict: bool = True) -> RunBook:
    """Walk ``roots`` for run configs, identified by content."""
    resolved: list[Path] = []
    runs: list[RunConfig] = []
    skipped: list[str] = []
    seen: set[Path] = set()

    for root in roots:
        base = Path(root)
        if not base.exists():
            skipped.append(f"root does not exist: {base}")
            continue
        resolved.append(base)
        for path in find_candidate_files(base):
            real = path.resolve()
            if real in seen:
                continue
            seen.add(real)
            try:
                raw = _read_document(path)
            except RunConfigError:
                continue  # not YAML/JSON; nothing to report
            if not looks_like_run_config(raw):
                continue
            try:
                runs.append(RunConfig.from_mapping(raw, path=path, strict=strict))
            except RunConfigError as exc:
                skipped.append(str(exc))

    runs.sort(key=lambda r: (r.start_time or datetime.min.replace(tzinfo=UTC),
                             r.model_id, str(r.path)))
    return RunBook(runs=tuple(runs), roots=tuple(resolved), skipped=tuple(skipped))


# ----------------------------------------------------------------------
def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise RunConfigError(
                f"run.start_time is not ISO-8601: {value!r}"
            ) from None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
