from datetime import timedelta
import hashlib

import numpy as np
import xarray as xr

from interfaces.fields import Forecast
from scripts.run_arco_pysteps import materialise_run
from utils.model_contract import utc
from utils.stored_fields import StoredFields, add_forecast


def test_realtime_vmi_run_has_context_without_fabricated_target(tmp_path):
    times = ["2026-11-06T12:05:00Z", "2026-11-06T12:10:00Z", "2026-11-06T12:15:00Z"]
    x = np.array([100.0, 101.0, 102.0, 103.0])
    y = np.array([200.0, 201.0, 202.0])
    fields = {}
    for index, time in enumerate(times):
        values = np.full((3, 4), 15.0 + index, dtype=np.float32)
        values[0, 0] = -9998  # covered no echo, not missing coverage
        fields[time] = {
            "vmi": xr.DataArray(
                values,
                dims=("y", "x"),
                coords={"x": x, "y": y},
                attrs={"grid_mapping": "radar_crs"},
            )
        }
    bundle = {"data": fields, "metadata": {"local_store": "fixture.zarr"}}
    run = materialise_run(bundle, tmp_path, "arco-vmi-test", "a" * 40, 0.0, (3, 3))
    access = StoredFields(run)
    assert set(access.manifest["observations"]) == {"context"}
    context = access.cube(access.manifest["observations"]["context"])
    assert context["values"].shape == (3, 3, 4)
    assert access.capabilities()["retrospective"] is False

    leads = tuple(range(5, 101, 5))
    initial = times[-1]
    forecast = Forecast(
        "vmi",
        "dBZ",
        np.zeros((len(leads), 3, 4), dtype=np.float32),
        tuple(
            (utc(initial) + timedelta(minutes=lead)).isoformat().replace("+00:00", "Z")
            for lead in leads
        ),
        {**access.manifest["observations"]["context"]["grid"], "x": context["x"], "y": context["y"]},
        np.zeros((len(leads), 3, 4), dtype=bool),
        initial,
        leads,
    )
    add_forecast(
        run,
        "pysteps_lk",
        "0.1.0",
        hashlib.sha256(b"manifest").hexdigest(),
        forecast,
    )
    result = StoredFields(run)
    assert result.cube(result.manifest["forecasts"]["pysteps_lk"])["values"].shape == (20, 3, 4)
