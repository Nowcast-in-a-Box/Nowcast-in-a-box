"""Reading what the platform already has: its settings, and its packages.

Nothing in here invents a directory. The host's paths come from its own
``config.yaml`` (:mod:`hostconfig`); packages and runs are found by walking a
root the caller names and recognising files by what they contain
(:mod:`packages`, :mod:`runs`), because `AGENTS.md` forbids assuming a layout
that has not been designed.
"""

from .hostconfig import HostConfig, HostConfigError, find_config_file, load as load_host
from .matching import MatchResult, check_pair, compatible_pairs
from .packages import (
    DATA,
    MODEL,
    UNKNOWN,
    Catalog,
    FieldSpec,
    ManifestError,
    PackageManifest,
    discover as discover_packages,
    load_manifest,
)
from .runs import (
    RunBook,
    RunConfig,
    RunConfigError,
    discover as discover_runs,
    load_run_config,
)

__all__ = [
    "Catalog",
    "DATA",
    "FieldSpec",
    "HostConfig",
    "HostConfigError",
    "MODEL",
    "ManifestError",
    "MatchResult",
    "PackageManifest",
    "RunBook",
    "RunConfig",
    "RunConfigError",
    "UNKNOWN",
    "check_pair",
    "compatible_pairs",
    "discover_packages",
    "discover_runs",
    "find_config_file",
    "load_host",
    "load_manifest",
    "load_run_config",
]
