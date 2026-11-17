"""Small physical-field fixtures shared by adapter and runtime tests."""

from datetime import datetime, timedelta, timezone

import numpy as np

from interfaces.fields import Forecast, Observations


def forecast(values, *, grid, init_time, leads, variable, units):
    initial = datetime.fromisoformat(init_time.replace("Z", "+00:00"))
    return Forecast(
        variable_id=variable,
        units=units,
        values=values,
        times=tuple(
            (initial + timedelta(minutes=lead)).isoformat().replace("+00:00", "Z")
            for lead in leads
        ),
        grid=grid,
        mask=~np.isfinite(values),
        init_time=init_time,
        lead_minutes=tuple(leads),
    )


def observations(
    *,
    source="sevir",
    variable="vil",
    units="raw",
    cadence=5,
    shape=(5, 128, 128),
    value=127.5,
):
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    values = np.full(shape, value, dtype=np.float32)
    return Observations(
        source=source,
        variable_id=variable,
        units=units,
        values=values,
        times=tuple(
            (start + timedelta(minutes=cadence * i)).isoformat().replace("+00:00", "Z")
            for i in range(shape[0])
        ),
        grid={
            "x": np.arange(shape[2]) * 3000.0,
            "y": np.arange(shape[1]) * 3000.0,
            "crs": "EPSG:3857",
            "axis_units": "m",
        },
        mask=np.zeros(shape, dtype=bool),
    )


def mock_onnx(monkeypatch, tmp_path, adapter, output):
    from dataclasses import replace
    import hashlib
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock

    path = tmp_path / "fixture.onnx"
    path.write_bytes(b"backend fixture, not real weights")
    spec = replace(
        adapter.weights(), sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    monkeypatch.setattr(adapter, "weights", lambda: spec)
    session = Mock()
    session.run.return_value = [output]
    factory = Mock(return_value=session)
    monkeypatch.setitem(
        sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=factory)
    )
    return path, factory, session
