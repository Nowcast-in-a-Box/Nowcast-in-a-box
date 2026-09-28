"""Provider selection.

``--field-access mock`` (the default) builds the synthetic provider.
``--field-access real`` is reserved for the platform implementation; it raises
for now, deliberately, so nobody ships a build that silently uses mock data.
"""

from __future__ import annotations

from .base import FieldAccess

PROVIDERS = ("mock", "replay", "real")


def build_access(kind: str = "mock", **kwargs) -> FieldAccess:
    """Return a provider instance for ``kind``."""
    kind = (kind or "mock").lower()

    if kind == "mock":
        from .synthetic import SyntheticAccess
        return SyntheticAccess(**kwargs)

    if kind == "replay":
        from .replay import ReplayAccess
        return ReplayAccess(**kwargs)

    if kind == "real":
        raise NotImplementedError(
            "the platform field provider does not exist yet; see "
            "spec/field-access.md. Use --field-access mock until it does."
        )

    raise ValueError(f"unknown field access {kind!r}; expected one of {PROVIDERS}")
