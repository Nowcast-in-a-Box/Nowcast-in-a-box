"""Field access: the contract the platform will implement, plus mocks."""

from .base import (
    Capabilities,
    Coords,
    Field,
    FieldAccess,
    FieldUnavailable,
    coords_from_extent,
)
from .factory import PROVIDERS, build_access
from .probe import AccessLog, CountingAccess
from .synthetic import SyntheticAccess

__all__ = [
    "AccessLog",
    "Capabilities",
    "CountingAccess",
    "Coords",
    "Field",
    "FieldAccess",
    "FieldUnavailable",
    "PROVIDERS",
    "SyntheticAccess",
    "build_access",
    "coords_from_extent",
]
