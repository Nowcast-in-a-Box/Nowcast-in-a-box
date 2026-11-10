"""Every source answers the same questions about its real sample.

Reads the slot named by tests/adapters/requests/<source>/example_success.yaml,
walks one step back, and asks for a time that has no file. A source whose
samples are missing from the fetched set skips and names the reason.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path

import numpy as np
import pytest
import yaml

from interfaces.data import request_from_mapping

ROOT = Path(__file__).resolve().parents[3]
REQUESTS = ROOT / "tests" / "adapters" / "requests"
SOURCES = sorted(path.parent.name for path in REQUESTS.glob("*/example_success.yaml"))


def _config(source: str) -> dict:
    path = REQUESTS / source / "example_success.yaml"
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _source(source: str):
    return import_module(f"adapters.{source}.source").ADAPTER


def _start(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _request(source: str, steps: int | None = None):
    data_root = os.environ.get("NIB_DATA_ROOT", "").strip()
    if not data_root:
        pytest.skip("run scripts/fetch-demo-data.py or set NIB_DATA_ROOT")
    config = _config(source)
    if steps is not None:
        config["history_steps"] = steps
    request = request_from_mapping(config, data_root)
    if not (Path(data_root) / request.data_dir).is_dir():
        pytest.skip(f"{source} samples are not in this data root")
    return request, _source(source)


@pytest.mark.parametrize("source", SOURCES)
def test_reads_the_requested_slot(source: str) -> None:
    request, adapter = _request(source)
    bundle = adapter.load(request)
    assert bundle["source"] == request.source
    assert request.initial_time in bundle["data"]
    for channel in request.channels:
        values = np.asarray(bundle["data"][request.initial_time][channel])
        assert values.ndim >= 2 and values.size > 0, channel
        assert np.isfinite(values).all(), channel
        assert bundle["source_files"][request.initial_time][channel], channel


@pytest.mark.parametrize("source", SOURCES)
def test_history_keeps_the_interval(source: str) -> None:
    steps = max(2, int(_config(source)["history_steps"]))
    request, adapter = _request(source, steps)
    bundle = adapter.load(request)
    times = sorted(_start(key) for key in bundle["data"])
    assert len(times) == steps
    assert _start(request.initial_time) in times
    assert {later - earlier for earlier, later in zip(times, times[1:])} == {
        timedelta(minutes=request.interval_minutes)
    }


@pytest.mark.parametrize("source", SOURCES)
def test_missing_time_is_reported(source: str) -> None:
    steps = max(2, int(_config(source)["history_steps"]))
    request, adapter = _request(source, steps)
    later = _start(request.initial_time) + timedelta(minutes=request.interval_minutes)
    with pytest.raises(FileNotFoundError):
        adapter.load(
            replace(request, initial_time=later.strftime("%Y-%m-%dT%H:%M:%SZ"))
        )
