"""Point NIB_DATA_ROOT at fetched demo samples when the caller has not set one."""

from __future__ import annotations

import os
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[2] / "testdata" / "demo-data"


def pytest_configure(config) -> None:
    if os.environ.get("NIB_DATA_ROOT", "").strip():
        return
    if SAMPLES.is_dir():
        os.environ["NIB_DATA_ROOT"] = str(SAMPLES)
