import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

from test_stored_fields import run


def model_entry():
    path = Path(__file__).resolve().parents[2] / "containers/models/run.py"
    spec = importlib.util.spec_from_file_location("model_entry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_entry_rejects_wrong_grid_and_weights_before_inference(
    run, tmp_path, monkeypatch
):
    import hashlib
    from types import SimpleNamespace

    weights = tmp_path / "model.onnx"
    weights.write_bytes(b"fixture")
    digest = hashlib.sha256(b"fixture").hexdigest()
    calls = []

    class Adapter:
        def weights(self):
            return SimpleNamespace(sha256=digest)

        def capabilities(self):
            return SimpleNamespace(version="test")

        def predict(self, prepared, *, weights_path):
            calls.append(prepared)
            return SimpleNamespace(
                values=np.ones((1, 20, 4, 4), np.float32),
                coords={"lead_minutes": list(range(5, 101, 5))},
            )

    entry = model_entry()
    monkeypatch.setenv("NIB_MODEL", "wadepre")
    monkeypatch.setattr(
        sys, "argv", ["model_run", "--run", str(run), "--weights", str(weights)]
    )
    monkeypatch.setattr(
        entry.importlib,
        "import_module",
        lambda name: SimpleNamespace(ADAPTER=Adapter()),
    )
    # The model rejects a cube at the wrong declared resolution before inference.
    with pytest.raises(ValueError, match="six unmasked"):
        entry.main()
    assert not calls
    weights.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="offline weight"):
        entry.main()
    assert not calls
