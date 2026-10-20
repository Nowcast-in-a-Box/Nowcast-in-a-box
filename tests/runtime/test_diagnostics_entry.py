import json
import sys

import numpy as np
import pytest

from diagnostics.__main__ import main
from utils.stored_fields import StoredFields, add_forecast
from test_stored_fields import run


@pytest.mark.parametrize(
    "missing,metric,expected", [(True, "rmse", "partial"), (False, "unknown", "failed")]
)
def test_diagnostics_retains_incomplete_attempt_and_forecasts(
    run, tmp_path, monkeypatch, missing, metric, expected
):
    values = np.full((20, 4, 4), np.nan if missing else 1.0, np.float32)
    add_forecast(run, "wadepre", "0.1.0", "a" * 64, values, list(range(5, 101, 5)))
    request = tmp_path / "diagnostics.json"
    request.write_text(
        json.dumps(
            {
                "version": 2,
                "run": str(run),
                "metrics": [{"family": "continuous", "items": [metric]}],
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["diagnostics", "--config", str(request)])
    assert main() == 1
    manifest = StoredFields(run).manifest
    assert manifest["status"] == "partial"
    assert "wadepre" in manifest["forecasts"]
    attempt = next(
        item for item in manifest["diagnostics"] if item["kind"] == "verification"
    )
    report = manifest["diagnostics"][-1]
    assert report["kind"] == "report" and report["status"] == "partial"
    assert report["attempt_ids"] == [attempt["id"]]
    assert not (run / report["entry"]).exists()
    assert attempt["status"] == expected
    summary = json.loads((run / attempt["path"] / "summary.json").read_text())
    assert summary["status"] == expected
    if missing:
        assert summary["unavailable"] == 20
        assert summary["ran"] == 0
