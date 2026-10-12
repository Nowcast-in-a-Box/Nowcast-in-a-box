"""Field access: the contract, the reference service, and the implementations.

``spec/field-access.md`` is the contract in prose. This package is where it is
built, in three layers:

* :mod:`~nib_verify.access.base` — the five methods and three data classes. The
  platform implements these.
* :mod:`~nib_verify.access.wire` — how a field is encoded. **Imported by both
  sides**, so the client and the service cannot disagree about the format.
* :mod:`~nib_verify.access.http` and :mod:`~nib_verify.access.mockfeed` — the
  two halves of talking to a service. ``mockfeed`` wraps *any* provider, so it is
  both our test double and the reference the platform can copy.

What we ask the platform to provide is written out in
``spec/data-access-we-ask-for.md``: the same five methods, why the batch loader
underneath them should stay, and the one performance requirement that is easy to
get backwards -- one read per (source, init time), cached inside the
implementation rather than demanded of the caller. :mod:`~nib_verify.access.probe`
counts the reads, so that requirement can be checked instead of promised.

Alongside: :mod:`~nib_verify.access.synthetic` and
:mod:`~nib_verify.access.replay` are in-process providers, for when a socket is
not worth the trouble; :mod:`~nib_verify.access.probe` counts reads;
:mod:`~nib_verify.access.conformance` runs the contract's checklist against a
provider, whichever of these it is.
"""

from .base import (
    Capabilities,
    Coords,
    Field,
    FieldAccess,
    FieldUnavailable,
    coords_from_extent,
    describe_capabilities,
)
from .conformance import Check, Report, check_provider
from .factory import PROVIDERS, build_access
from .http import HttpAccess, HttpAccessError
from . import scenario
from .probe import AccessLog, CountingAccess
from .synthetic import SyntheticAccess
from .wire import PROTOCOL, WireError

__all__ = [
    "PROTOCOL",
    "PROVIDERS",
    "AccessLog",
    "Capabilities",
    "Check",
    "CountingAccess",
    "Coords",
    "Field",
    "FieldAccess",
    "FieldUnavailable",
    "HttpAccess",
    "HttpAccessError",
    "Report",
    "SyntheticAccess",
    "WireError",
    "build_access",
    "check_provider",
    "coords_from_extent",
    "describe_capabilities",
    "scenario",
]


def __getattr__(name: str):
    """``create_data_app`` and ``ReplayAccess`` are imported on demand.

    The first needs FastAPI and the second needs a directory layout; neither
    should be a cost of importing the contract itself.
    """
    if name == "create_data_app":
        from .mockfeed import create_data_app
        return create_data_app
    if name == "ReplayAccess":
        from .replay import ReplayAccess
        return ReplayAccess
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
