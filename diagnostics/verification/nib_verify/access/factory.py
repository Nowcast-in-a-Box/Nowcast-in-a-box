"""Provider selection.

Four names, and only one of them is a provider anybody chose:

``mock``    the synthetic generator. The default, because until the platform
            ships a field service there is nothing else to point at, and a
            module that refuses to run at all is a module nobody can review.
``replay``  ``.npy`` files on disk. For integration runs against real samples.
``http``    a service speaking ``spec/field-access-http.md``. This is the one the
            platform will provide.
``real``    an alias for ``http``.

Why ``real`` is an alias and not a hole that raises: it used to raise, and the
reason was good — a build that silently verifies against made-up numbers is worse
than one that refuses to start. That reason is still honoured, just differently.
``http`` cannot fall back to anything: it needs an address, and this module will
not invent one. ``--access real`` without ``--data-url`` fails, loudly, with the
same words the missing-data-root error uses.

That leaves exactly one way to verify against generated data, and it is to type
``--access mock``.
"""

from __future__ import annotations

from .base import FieldAccess

PROVIDERS = ("mock", "replay", "http", "real")

#: Keys accepted for the service address, most specific first.
URL_KEYS = ("base_url", "data_url", "url")

NO_ADDRESS = (
    "the field provider needs an address and this module does not guess one. "
    "Pass --data-url http://host:port (or --access mock for generated data)."
)


def build_access(kind: str = "mock", **kwargs) -> FieldAccess:
    """Return a provider instance for ``kind``."""
    kind = (kind or "mock").lower()

    if kind == "mock":
        from .scenario import settings as scenario_settings
        from .synthetic import SyntheticAccess

        # A scenario is a starting point, not a fixed menu: explicit settings win
        # over the bundle, so --scenario convective --mock peak=300 works.
        name = kwargs.pop("scenario", None)
        base = scenario_settings(name) if name else {}
        return SyntheticAccess(**{**base, **kwargs})

    if kind == "replay":
        from .replay import ReplayAccess
        return ReplayAccess(**kwargs)

    if kind in ("http", "real"):
        from .http import HttpAccess

        url = None
        for key in URL_KEYS:
            if kwargs.get(key):
                url = kwargs.pop(key)
                break
            kwargs.pop(key, None)
        if not url:
            raise ValueError(NO_ADDRESS)
        return HttpAccess(url, **kwargs)

    raise ValueError(f"unknown field access {kind!r}; expected one of {PROVIDERS}")
