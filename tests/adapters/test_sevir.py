"""SEVIR reader and the three ONNX forecasts.

The HDF5 path comes from NIB_DATA_ROOT. This file does not name a machine path.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from interfaces.model import WeightSpec
from utils.weights import ensure_weights, file_sha256
from adapters.sevir.exprecast.adapter import ADAPTER as EXPRECAST
from adapters.sevir.nowcastnet.adapter import ADAPTER as NOWCASTNET
from adapters.sevir.wadepre.adapter import ADAPTER as WADEPRE

ROOT = Path(__file__).resolve().parents[2]
MODELS = (NOWCASTNET, EXPRECAST, WADEPRE)


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


class SevirCaseTests(unittest.TestCase):
    def test_forecasts_against_one_sample(self) -> None:
        data_root = os.environ.get("NIB_DATA_ROOT", "").strip()
        h5 = (
            Path(data_root) / "SEVIR" / "processed" / "sevir_vil.h5"
            if data_root
            else None
        )
        if h5 is None or not h5.is_file():
            self.skipTest(
                "set NIB_DATA_ROOT to a catalog that contains SEVIR/processed/sevir_vil.h5"
            )
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            self.skipTest("onnxruntime is not installed")

        from interfaces.data import request_from_mapping
        from adapters.sevir.source import ADAPTER
        import yaml

        config_path = (
            ROOT / "tests" / "adapters" / "requests" / "sevir" / "example_success.yaml"
        )
        with config_path.open(encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        request = request_from_mapping(config, data_root)
        bundle = ADAPTER.load(request)
        self.assertEqual(bundle["metadata"]["n_times"], 6)
        target = bundle["metadata"]["target"]

        import numpy as np

        for model in MODELS:
            spec = model.weights()
            local = ROOT / "artifacts" / spec.artifacts_relpath
            if not local.is_file():
                self.skipTest(f"local weight is not present: {spec.artifacts_relpath}")
            with patch("utils.weights.urlopen", _fail_download):
                forecast = model.predict(
                    model.prepare(bundle, ADAPTER),
                    weights_path=str(local),
                )
            self.assertEqual(forecast.shape, (1, 20, 128, 128))
            values = np.asarray(forecast.values[0], dtype=np.float32)
            self.assertTrue(np.isfinite(values).all(), model.capabilities().id)
            mae = float(np.mean(np.abs(values - target)))
            print(f"{model.capabilities().id} mae={mae:.3f}")
            self.assertLess(mae, 255.0, model.capabilities().id)


def _fail_download(*args, **kwargs):
    raise AssertionError(
        "Hugging Face download should be skipped after the sha256 check"
    )
