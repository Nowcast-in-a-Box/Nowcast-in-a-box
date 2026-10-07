"""Cross-platform data directory resolution.

Mirrors handler.ResolveDataDir. Does not create directories.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from interfaces.errors import ConstraintError

_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC = re.compile(r"^\\\\")


def _foreign_absolute(data_dir: str) -> bool:
    return _DRIVE.match(data_dir) is not None or _UNC.match(data_dir) is not None


def resolve_data_dir(data_dir: str, data_root: str | None) -> Path:
    """Resolve data_dir against data_root.

    Relative paths require data_root. A drive-letter or UNC path on a
    non-Windows host is rejected instead of being joined onto data_root.
    """
    raw = str(data_dir).strip()
    if not raw:
        raise ConstraintError("data_dir is empty")

    if os.name != "nt" and _foreign_absolute(raw):
        raise ConstraintError(
            f"data_dir {raw!r} is an absolute path for another operating system; "
            "pass a path relative to the data root"
        )

    path = Path(raw)
    if path.is_absolute():
        return path

    root = "" if data_root is None else str(data_root).strip()
    if not root:
        raise ConstraintError(
            "relative data_dir requires a data root "
            "(NIB_DATA_ROOT or config.yaml paths.data_root)"
        )

    root_path = Path(root)
    if ".." in path.parts:
        raise ConstraintError(f"data_dir {raw!r} escapes the data root")

    joined = root_path.joinpath(path)
    try:
        joined.resolve().relative_to(root_path.resolve())
    except ValueError as exc:
        raise ConstraintError(f"data_dir {raw!r} escapes the data root") from exc
    return joined
