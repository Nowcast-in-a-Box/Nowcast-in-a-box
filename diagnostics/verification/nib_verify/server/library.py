"""A directory of verification configs, with the mutability rules enforced.

The RFC's rules are not advice. They exist because changing the ruler changes the
denominator and the mask, so numbers computed before and after are not
comparable — and a file that looks like an edit would silently make them look
like they are. So :meth:`ConfigLibrary.save` compares against what is already on
disk and **refuses** a ruler change rather than writing it.

The files are plain JSON named after the group id, so a config is still a file
that goes into git, exactly as the RFC says. Nothing about this class is
web-specific; the API is a thin wrapper over it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Iterator

from ..config.schema import ConfigError, Member, MetricGroup, VerificationConfig

FILE_SUFFIX = ".json"


class ConfigNotFound(ConfigError):
    """A config that is not there, as opposed to one that is wrong.

    Kept separate so the web service can answer 404 for the first and 400 for the
    second; conflating them tells a user to fix a file that does not exist.
    """


class ConfigLibrary:
    """The configs in one directory, named ``<group_id>.json``."""

    def __init__(self, directory: str | Path, *, create: bool = True) -> None:
        self.directory = Path(directory)
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)

    # -- reading --------------------------------------------------------
    def paths(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(self.directory.glob(f"*{FILE_SUFFIX}"))

    def list(self) -> list[VerificationConfig]:
        """Every readable config, sorted by id.  Unreadable ones are left out."""
        out = []
        for path in self.paths():
            try:
                out.append(self._read(path))
            except ConfigError:
                continue
        return sorted(out, key=lambda c: c.group_id)

    def entries(self) -> list[dict]:
        """One summary row per config, for a list view."""
        return [self.summary(cfg) for cfg in self.list()]

    def broken(self) -> list[dict]:
        """Files in the directory that are not readable configs, with the reason."""
        out = []
        for path in self.paths():
            try:
                self._read(path)
            except ConfigError as exc:
                out.append({"path": str(path), "name": path.name, "error": str(exc)})
        return out

    def path_for(self, group_id: str) -> Path:
        safe = _safe_name(group_id)
        return self.directory / f"{safe}{FILE_SUFFIX}"

    def exists(self, group_id: str) -> bool:
        return self.path_for(group_id).is_file()

    def get(self, group_id: str) -> VerificationConfig:
        path = self.path_for(group_id)
        if not path.is_file():
            raise ConfigNotFound(f"no config named {group_id!r} in {self.directory}")
        return self._read(path)

    # -- writing --------------------------------------------------------
    def save(
        self, cfg: VerificationConfig, *, replace_existing: bool = False
    ) -> VerificationConfig:
        """Write a config after checking it against the one already there.

        Raises :class:`ConfigError` when the change touches the ruler or removes
        a metric, and when the file exists but ``replace_existing`` is false.
        """
        path = self.path_for(cfg.group_id)

        if path.is_file():
            current = self._read(path)
            problems = cfg.allows_modification_of(current)
            if problems:
                raise ConfigError(
                    f"this change would rewrite {', '.join(problems)} in place. "
                    "Those fields decide what the numbers mean, so numbers "
                    "computed before and after would not be comparable. "
                    "Create a new config instead."
                )
            if cfg == current and not replace_existing:
                return current
        elif not replace_existing:
            raise ConfigError(
                f"no config named {cfg.group_id!r} here; pass replace_existing "
                "to create it"
            )

        self._write(path, cfg)
        return cfg

    def create(self, cfg: VerificationConfig, *, overwrite: bool = False) -> VerificationConfig:
        """Write a config that does not exist yet.

        With ``overwrite`` and a file already there, this delegates to
        :meth:`save` rather than writing directly — otherwise ``overwrite`` would
        be a way around the ruler check, which is the one thing the check exists
        to prevent.
        """
        path = self.path_for(cfg.group_id)
        if path.is_file():
            if not overwrite:
                raise ConfigError(f"a config named {cfg.group_id!r} already exists")
            return self.save(cfg)
        self._write(path, cfg)
        return cfg

    def delete(self, group_id: str) -> Path:
        path = self.path_for(group_id)
        if not path.is_file():
            raise ConfigNotFound(f"no config named {group_id!r} in {self.directory}")
        path.unlink()
        return path

    # -- the edits the RFC allows ---------------------------------------
    def set_member(self, group_id: str, member: Member, *, retire_on: date | None = None):
        """Add a member, or change one already on the roster."""
        current = self.get(group_id)
        return self.save(current.with_member(member, retire_on=retire_on))

    def retire_member(self, group_id: str, name: str, on: date):
        """Record the last day a member was on the roster."""
        current = self.get(group_id)
        existing = current.member(name)  # KeyError if it is not there
        if existing.active_to == on:
            return current
        if existing.active_to is not None and existing.active_to < on:
            raise ConfigError(
                f"{name!r} is already retired on {existing.active_to}; "
                "a roster cannot be reopened, only extended with a new config"
            )
        return self.save(
            current.with_member(replace(existing, active_to=on), retire_on=on)
        )

    def add_metrics(self, group_id: str, group: MetricGroup):
        """Append a metric group.  Metrics are only ever added."""
        current = self.get(group_id)
        return self.save(current.with_metric_group(group))

    # -- helpers --------------------------------------------------------
    def summary(self, cfg: VerificationConfig) -> dict:
        # ``member_names``, not ``members``: a config's own ``members`` are records,
        # and a summary that overwrote them would be a trap for any caller that
        # merged the two dictionaries.
        return {
            "group_id": cfg.group_id,
            "name": cfg.name,
            "ruler_fingerprint": cfg.ruler_fingerprint,
            "ground_truth": cfg.ruler.ground_truth,
            "time_step_minutes": cfg.ruler.time_step_minutes,
            "member_names": [m.name for m in cfg.members],
            "n_members": len(cfg.members),
            "n_metrics": sum(len(g.items) for g in cfg.metrics),
            "leads": list(cfg.leads()),
            "path": str(self.path_for(cfg.group_id)),
        }

    def _read(self, path: Path) -> VerificationConfig:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path.name}: {exc}") from None
        try:
            return VerificationConfig.from_dict(raw)
        except ConfigError as exc:
            raise ConfigError(f"{path.name}: {exc}") from None

    def _write(self, path: Path, cfg: VerificationConfig) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # write-then-rename so a reader never sees half a config
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(cfg.to_json(), encoding="utf-8")
        tmp.replace(path)

    def __iter__(self) -> Iterator[VerificationConfig]:
        return iter(self.list())

    def __len__(self) -> int:
        return len(self.paths())


def _safe_name(group_id: str) -> str:
    """A group id is used as a file name, so it must not be a path."""
    if not group_id or group_id.strip() != group_id:
        raise ConfigError(f"invalid group id: {group_id!r}")
    bad = set('\\/:*?"<>|')
    if bad & set(group_id) or group_id in (".", "..") or group_id.startswith("."):
        raise ConfigError(
            f"group id {group_id!r} cannot be used as a file name; "
            "use letters, digits, dashes and underscores"
        )
    return group_id
