"""Shared fixtures.

Everything here is synthetic and deterministic: no files, no network, no GPU.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nib_verify.access import CountingAccess, SyntheticAccess  # noqa: E402
from nib_verify.config import (  # noqa: E402
    Member,
    MetricGroup,
    Ruler,
    VerificationConfig,
)
from nib_verify.runner import RunRef  # noqa: E402
from nib_verify.store import Store  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

#: the level set Augur uses, on its 0-255 VIL scale
LEVELS_FULL = (16.0, 74.0, 133.0, 181.0, 219.0)
LEVELS_SUB = (181.0, 219.0)


def make_access(*, peak: float = 250.0, **kwargs) -> SyntheticAccess:
    """A provider with no noise, so the forecast equals the truth."""
    defaults = dict(
        t_start=T0,
        cadence_minutes=10,
        horizon_minutes=60,
        obs_shape=(24, 32),
        model_shape=(20, 26),
        peak=peak,
        sigma=0.14,
        variable="vil",
        unit="kg m-2",
        truth_noise=0.0,
        forecast_noise=0.0,
    )
    defaults.update(kwargs)
    return SyntheticAccess(**defaults)


def make_config(levels=LEVELS_FULL, *, group_id: str = "vil-test") -> VerificationConfig:
    return VerificationConfig(
        group_id=group_id,
        name="VIL verification, synthetic",
        ruler=Ruler(
            ground_truth="radar-composite",
            variable="vil",
            unit="kg m-2",
            time_step_minutes=10,
            extent=(110.0, 30.0, 120.0, 42.0),
            resolution=0.5,
        ),
        members=(
            Member(
                name="mlcast-det",
                variable="vil",
                unit="kg m-2",
                leads=(10, 20, 30),
            ),
        ),
        metrics=(
            MetricGroup(
                family="categorical",
                items=("ts", "bias", "ets"),
                levels=tuple(levels),
            ),
            MetricGroup(family="continuous", items=("me", "mae", "rmse")),
            MetricGroup(family="moments", items=("corr",)),
            MetricGroup(
                family="spatial",
                items=("fss",),
                levels=tuple(levels),
                half_windows=(1, 2),
            ),
        ),
    )


@pytest.fixture
def access():
    return CountingAccess(make_access())


@pytest.fixture
def cfg():
    return make_config()


@pytest.fixture
def store():
    s = Store(":memory:")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def runs():
    return [
        RunRef(member="mlcast-det", init_time=T0),
        RunRef(member="mlcast-det", init_time=T0 + timedelta(hours=1)),
    ]
