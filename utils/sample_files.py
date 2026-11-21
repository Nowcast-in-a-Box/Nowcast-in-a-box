"""Bounded discovery without following links in the input tree."""

import os
from pathlib import Path

MAX_ENTRIES = 32768
MAX_DEPTH = 16


def first_file(root: Path, predicate) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected a data directory")
    pending = [(root, 0)]
    seen = 0
    while pending:
        directory, depth = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                seen += 1
                if seen > MAX_ENTRIES:
                    raise ValueError("Data directory exceeds inspection limit")
                path = Path(entry.path)
                if entry.is_file(follow_symlinks=False) and predicate(
                    path.relative_to(root)
                ):
                    return path
                if depth < MAX_DEPTH and entry.is_dir(follow_symlinks=False):
                    pending.append((path, depth + 1))
    raise ValueError("No matching sample within inspection limits")
