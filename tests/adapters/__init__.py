"""Adapter tests. File-backed cases skip unless the caller sets NIB_DATA_ROOT."""

from __future__ import annotations

from pathlib import Path


def load_tests(loader, tests, pattern):
    start = str(Path(__file__).resolve().parent)
    top = str(Path(__file__).resolve().parents[2])
    return loader.discover(start, pattern or "test_*.py", top_level_dir=top)
