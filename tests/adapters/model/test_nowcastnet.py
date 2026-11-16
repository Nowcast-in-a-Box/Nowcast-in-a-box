"""NowcastNet contract tests. No weight file and no product-code literal."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters.sevir.nowcastnet.adapter import ADAPTER
from interfaces.data import (
    ChannelSpec,
    DataBundle,
    DataCapabilities,
    DataRequest,
    DeclaredDataAdapter,
    SpatialSpec,
    VariableSpec,
    bundle_from_parts,
)
from interfaces.errors import WeightsNotReady


class _FakeData(DeclaredDataAdapter):
    CAPABILITIES = DataCapabilities(
        id="sevir",
        version="0.0.1",
        title="Demo",
        instrument="demo",
        channels=ChannelSpec(
            mode="closed",
            variables=(VariableSpec("demo_var", "demo variable", "units"),),
        ),
        spatial=SpatialSpec(crop=False),
        native_cadence_minutes=6,
        formats=("memory",),
    )

    def load(self, request: DataRequest) -> DataBundle:
        raise AssertionError("load should not run")


def _bundle(data_dir: str) -> DataBundle:
    request = DataRequest(
        source="sevir",
        data_dir=data_dir,
        data_root=None,
        initial_time="2024-07-05T00:00:00Z",
        history_steps=1,
        interval_minutes=6,
        channels=("demo_var",),
        bbox=(0.0, 0.0, 1.0, 1.0),
    )
    frame = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    return bundle_from_parts(
        "sevir",
        request,
        {"2024-07-05T00:00:00Z": {"demo_var": frame}},
        {"2024-07-05T00:00:00Z": {"demo_var": []}},
        {},
    )


class NowcastNetTests(unittest.TestCase):
    def test_output_spec(self) -> None:
        spec = ADAPTER.output_spec()
        self.assertEqual(spec.dims, ("batch", "lead", "y", "x"))
        sizes = {axis.name: axis.size for axis in spec.axes}
        self.assertEqual(sizes["lead"], 20)
        self.assertEqual(sizes["y"], 128)
        self.assertEqual(sizes["x"], 128)

    def test_prepare_uses_paired_variable_and_pads(self) -> None:
        prepared = ADAPTER.prepare(_bundle(tempfile.gettempdir()), _FakeData())
        self.assertEqual(prepared.variable_id, "demo_var")
        self.assertEqual(prepared.shape, (1, 5, 128, 128))

    def test_six_minute_bundle_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ADAPTER.validate_input(_bundle(tmp), _FakeData())

    def test_predict_without_weights(self) -> None:
        prepared = ADAPTER.prepare(_bundle(tempfile.gettempdir()), _FakeData())
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["NIB_ARTIFACTS"] = tmp
            try:
                with patch("utils.weights.urlopen", side_effect=OSError("offline")):
                    with self.assertRaises(WeightsNotReady) as caught:
                        ADAPTER.predict(
                            prepared,
                            weights_path=str(Path(tmp) / "missing.onnx"),
                        )
            finally:
                os.environ.pop("NIB_ARTIFACTS", None)
        self.assertIn("download_url", str(caught.exception))
