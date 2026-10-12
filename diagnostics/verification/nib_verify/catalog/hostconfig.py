"""The host's own settings.

The platform keeps bind addresses and paths in the repository-root
``config.yaml``, and `AGENTS.md` is explicit about what a contributor must do
with them:

    Read bind addresses and paths from ``config.yaml``. Flags override the file.
    Fail if the data root is missing. Never invent a host path.

So this module does exactly two things: find that file the way the Go handler
finds it, and hand back the values. It never guesses a directory, and
:meth:`HostConfig.require_data_root` refuses rather than substituting a default.

The layering is deliberate. :func:`load` reads the *host's* file and is the only
place that knows about it; everything else in ``nib_verify`` works from the
values it returns.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CONFIG_FILE_NAME = "config.yaml"

DEFAULT_HANDLER_HOST = "127.0.0.1"
DEFAULT_HANDLER_PORT = 8765
DEFAULT_REVIEW_HOST = "127.0.0.1"
DEFAULT_REVIEW_PORT = 8766
DEFAULT_ARTIFACTS = "artifacts"


class HostConfigError(RuntimeError):
    """The host's configuration is missing or unusable."""


@dataclass(frozen=True)
class HostConfig:
    """Resolved host settings.  Paths are absolute when they are set."""

    source: Path | None = None
    handler_host: str = DEFAULT_HANDLER_HOST
    handler_port: int = DEFAULT_HANDLER_PORT
    review_host: str = DEFAULT_REVIEW_HOST
    review_port: int = DEFAULT_REVIEW_PORT
    artifacts: Path | None = None
    data_root: Path | None = None

    # -- accessors ------------------------------------------------------
    @property
    def review_address(self) -> tuple[str, int]:
        """Where the handler expects the review page to listen."""
        return (self.review_host, self.review_port)

    @property
    def handler_address(self) -> tuple[str, int]:
        return (self.handler_host, self.handler_port)

    def require_data_root(self) -> Path:
        """The observation root, or a refusal.

        ``Never invent a host path`` — so there is no fallback here on purpose.
        A caller that cannot proceed without a data root must say so loudly.
        """
        if self.data_root is None:
            raise HostConfigError(
                "no data root is configured. Set paths.data_root in config.yaml "
                "or the NIB_DATA_ROOT environment variable. This module does not "
                "guess a host path."
            )
        if not self.data_root.exists():
            raise HostConfigError(f"data root does not exist: {self.data_root}")
        return self.data_root

    def as_dict(self) -> dict:
        return {
            "source": str(self.source) if self.source else None,
            "handler": {"host": self.handler_host, "port": self.handler_port},
            "review": {"host": self.review_host, "port": self.review_port},
            "paths": {
                "artifacts": str(self.artifacts) if self.artifacts else None,
                "data_root": str(self.data_root) if self.data_root else None,
            },
        }


# ----------------------------------------------------------------------
def find_config_file(start: str | Path | None = None) -> Path | None:
    """``NIB_CONFIG``, else walk upwards from ``start`` or the cwd.

    Mirrors ``handler/config.go``: the same file the handler would read is the
    file we read, so the two cannot drift apart.
    """
    explicit = os.environ.get("NIB_CONFIG")
    if explicit:
        return Path(explicit)

    current = Path(start).resolve() if start else Path.cwd().resolve()
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        candidate = current / CONFIG_FILE_NAME
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def load(explicit: str | Path | None = None) -> HostConfig:
    """Read the host config, applying the handler's own defaults."""
    path = Path(explicit).resolve() if explicit else find_config_file()
    if path is None or not path.is_file():
        if explicit:
            raise HostConfigError(f"host config not found: {explicit}")
        return _apply_env(_with_defaults({}, None))

    raw = _read_yaml(path)
    if not isinstance(raw, Mapping):
        raise HostConfigError(f"{path}: expected a mapping at the top level")
    return _apply_env(_with_defaults(raw, path))


def _read_yaml(path: Path) -> Any:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - PyYAML is expected
        raise HostConfigError("reading config.yaml needs PyYAML") from exc
    try:
        # utf-8-sig because a Windows editor will happily add a BOM
        return yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - report, never crash on parse
        raise HostConfigError(f"{path}: {exc}") from exc


def _with_defaults(raw: Mapping[str, Any], path: Path | None) -> HostConfig:
    handler = _section(raw, "handler")
    review = _section(raw, "review")
    paths = _section(raw, "paths")

    base = path.parent if path is not None else Path.cwd()

    artifacts = _resolve(base, paths.get("artifacts") or DEFAULT_ARTIFACTS)
    data_root_raw = paths.get("data_root")
    data_root = _resolve(base, data_root_raw) if data_root_raw else None

    return HostConfig(
        source=path,
        handler_host=str(handler.get("host") or DEFAULT_HANDLER_HOST),
        handler_port=_port(handler.get("port"), DEFAULT_HANDLER_PORT, "handler.port"),
        review_host=str(review.get("host") or DEFAULT_REVIEW_HOST),
        review_port=_port(review.get("port"), DEFAULT_REVIEW_PORT, "review.port"),
        artifacts=artifacts,
        data_root=data_root,
    )


def _apply_env(cfg: HostConfig) -> HostConfig:
    """``NIB_DATA_ROOT`` wins, as it does in the handler."""
    env = os.environ.get("NIB_DATA_ROOT")
    if not env:
        return cfg
    from dataclasses import replace

    return replace(cfg, data_root=Path(env).resolve())


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise HostConfigError(f"config.yaml: {name!r} must be a mapping")
    return value


def _port(value: Any, default: int, label: str) -> int:
    if value in (None, ""):
        return default
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise HostConfigError(f"config.yaml: {label} is not a number: {value!r}") from None
    if not (0 < port <= 65535):
        raise HostConfigError(f"config.yaml: {label} out of range: {port}")
    return port


def _resolve(base: Path, value: Any) -> Path:
    p = Path(str(value))
    return p.resolve() if p.is_absolute() else (base / p).resolve()
