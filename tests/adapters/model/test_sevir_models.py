"""Discovered SEVIR declarations and mocked backends; no real inference or weights."""

from __future__ import annotations

import importlib
import json

import numpy as np
import pytest
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from interfaces.model import WeightSpec
from utils.weights import ensure_weights, file_sha256
from interfaces.errors import ConstraintError
from interfaces.fields import Forecast
from tests.field_fixtures import mock_onnx, observations

ROOT = Path(__file__).resolve().parents[3]
DECLARATIONS = sorted((ROOT / "contracts/models/sevir").glob("*.json"))


class WeightSkipTests(unittest.TestCase):
    def test_matching_sha256_skips_huggingface_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "sevir" / "demo" / "weight.onnx"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b"local-weight")
            spec = WeightSpec(
                status="pinned",
                download_url="https://huggingface.co/example/demo/resolve/main/weight.onnx",
                sha256=file_sha256(dest),
                filename="weight.onnx",
                hf={
                    "repo": "example/demo",
                    "revision": "main",
                    "filename": "weight.onnx",
                },
                artifacts_relpath="sevir/demo/weight.onnx",
            )

            def fail_download(*args, **kwargs):
                raise AssertionError("download should be skipped when sha256 matches")

            with patch("utils.weights.urlopen", fail_download):
                ready = ensure_weights(spec, root)
            self.assertEqual(ready, dest)


@pytest.mark.parametrize("declaration", DECLARATIONS, ids=lambda path: path.stem)
def test_declared_backend_returns_physical_forecast(declaration, tmp_path, monkeypatch):
    declared = json.loads(declaration.read_text())
    model = importlib.import_module(f"adapters.sevir.{declared['id']}.adapter").ADAPTER
    assert model.capabilities().id == declared["id"]
    assert model.input_requirements().context_frames == 5
    assert model.input_requirements().spatial == (128, 128)
    obs = observations()
    prepared = model.prepare(obs)
    np.testing.assert_array_equal(prepared[0], 0.5)
    assert prepared[1] is obs
    spec = model.output_specification()
    output = np.full((1, len(spec.lead_minutes), *spec.spatial), 0.25, np.float32)
    weights, factory, session = mock_onnx(monkeypatch, tmp_path, model, output)
    result = model.predict(prepared, weights_path=str(weights))
    factory.assert_called_once_with(str(weights), providers=["CPUExecutionProvider"])
    names, inputs = session.run.call_args.args
    assert names == (["refined_out"] if declared["id"] == "wadepre" else ["forecast"])
    assert inputs["input_sequence"] is prepared[0]
    assert isinstance(result, Forecast)
    assert result.values.shape == (len(spec.lead_minutes), *spec.spatial)
    assert result.values.dtype == np.float32
    np.testing.assert_array_equal(result.values, 63.75)
    assert result.grid is obs.grid and not result.mask.any()
    assert result.init_time == obs.times[-1]
    assert result.lead_minutes == spec.lead_minutes

    session.run.return_value = [output[:, :-1]]
    with pytest.raises(ConstraintError, match="backend output shape"):
        model.predict(prepared, weights_path=str(weights))
