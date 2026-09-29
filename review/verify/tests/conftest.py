"""Shared fixtures.

Everything here is synthetic and deterministic: no files, no network, no GPU.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass
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


# ======================================================================
# a miniature platform tree
# ======================================================================
DATA_MANIFEST = {
    "id": "radar-composite",
    "offers": ["precip-nowcast.v1"],
    "region": "east-china",
    "fields": [
        {
            "name": "vil",
            "unit": "kg m-2",
            "dtype": "float32",
            "shape": [48, 64],
            "cadence_minutes": 10,
            "frame_count": 6,
            "target_frame_count": 3,
        }
    ],
}

MODEL_MANIFEST = {
    "id": "mlcast-det",
    "requires": "precip-nowcast.v1",
    "region": "east-china",
    "context_frames": {"required_count": 5, "mode": "latest"},
    "input": [
        {"name": "vil", "unit": "kg m-2", "dtype": "float32", "shape": [48, 64]}
    ],
    "output": [
        {
            "name": "vil",
            "unit": "kg m-2",
            "dtype": "float32",
            "shape": [48, 64],
            "cadence_minutes": 10,
            "frame_count": 3,
        }
    ],
}


def write_json(path, payload) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_yaml(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_config_text(
    *, model="mlcast-det", data="radar-composite",
    start="2026-01-01T00:00:00Z", horizon=6,
) -> str:
    return (
        "version: 1\n"
        f"data:\n  id: {data}\n"
        f"model:\n  id: {model}\n"
        "run:\n"
        f"  start_time: \"{start}\"\n"
        f"  forecast_horizon: {horizon}\n"
    )


@dataclass
class PlatformTree:
    """A plausible on-disk platform, built inside tmp_path."""

    root: Path
    data_root: Path
    config_path: Path
    packages: list[Path]
    runs_root: Path

    def catalog(self, **kwargs):
        from nib_verify.catalog import discover_packages

        return discover_packages(self.packages, **kwargs)

    def runbook(self, **kwargs):
        from nib_verify.catalog import discover_runs

        return discover_runs([self.runs_root], **kwargs)


@pytest.fixture
def platform(tmp_path) -> PlatformTree:
    """A host config, two packages and two runs, in deliberately odd places.

    The directories are named nothing like a catalog, precisely to show that
    discovery does not depend on them.
    """
    root = tmp_path / "NiB"
    data_root = root / "inputs" / "data"
    data_root.mkdir(parents=True)

    config_path = root / "config.yaml"
    write_yaml(config_path, (
        "handler:\n  host: 127.0.0.1\n  port: 8765\n"
        "review:\n  host: 127.0.0.1\n  port: 8766\n"
        "paths:\n  artifacts: artifacts\n  data_root: inputs/data\n"
    ))

    # packages live in two unrelated subtrees
    p1 = root / "bundle-a" / "radar-thing" / "manifest.json"
    p2 = root / "bundle-b" / "deep" / "down" / "model" / "manifest.json"
    write_json(p1, DATA_MANIFEST)
    write_json(p2, MODEL_MANIFEST)

    runs_root = root / "workspace" / "jobs"
    write_yaml(runs_root / "a" / "run.yaml", run_config_text())
    write_yaml(
        runs_root / "b" / "run.yaml",
        run_config_text(start="2026-01-01T01:00:00Z"),
    )

    return PlatformTree(
        root=root,
        data_root=data_root,
        config_path=config_path,
        packages=[root / "bundle-a", root / "bundle-b"],
        runs_root=runs_root,
    )


# ======================================================================
# the field service, for tests that need a real socket
# ======================================================================
@contextmanager
def serve_data(access, *, host: str = "127.0.0.1"):
    """Run the reference field service on a free port, for the duration.

    A real socket rather than a test transport, on purpose: the client's whole
    job is to be a client, and a fake transport would test everything about it
    except the part that can be wrong — what happens when a server closes an idle
    connection, or is not there at all.

    ``access`` is captured as given, so a caller that passes a
    :class:`~nib_verify.access.probe.CountingAccess` can read back what the
    client actually asked for.
    """
    pytest.importorskip("fastapi", reason="the field service needs fastapi")
    import threading
    import time

    import uvicorn

    from nib_verify.access.mockfeed import create_data_app

    config = uvicorn.Config(
        create_data_app(access), host=host, port=0, log_level="error", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    else:  # pragma: no cover
        raise RuntimeError("the reference field service did not start")

    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def data_service():
    """The reference service, with a counter in front of the provider."""
    counted = CountingAccess(make_access(missing_points=150))
    with serve_data(counted) as url:
        yield url, counted
