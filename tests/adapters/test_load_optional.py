"""Optional file-backed loader checks.

Skip unless the caller sets NIB_DATA_ROOT. This file does not name a
sample location.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import yaml

from interfaces.data import request_from_mapping


class OptionalLoadTests(unittest.TestCase):
    def test_example_success_when_data_root_is_set(self) -> None:
        root = os.environ.get("NIB_DATA_ROOT", "").strip()
        if not root:
            self.skipTest("set NIB_DATA_ROOT to local files; do not commit samples")
        repo = Path(__file__).resolve().parents[2]
        for path in (repo / "tests" / "adapters" / "requests").glob(
            "*/example_success.yaml"
        ):
            with path.open(encoding="utf-8") as handle:
                config = yaml.safe_load(handle)
            request = request_from_mapping(config, root)
            data_dir = Path(root) / request.data_dir
            if not data_dir.exists():
                self.skipTest(
                    "set NIB_DATA_ROOT so example data_dir exists; do not commit samples"
                )
            from importlib import import_module

            adapter = import_module(f"adapters.{request.source}.source").ADAPTER
            bundle = adapter.load(request)
            self.assertEqual(bundle["source"], request.source)
            self.assertEqual(bundle["metadata"]["n_times"], request.history_steps)
            self.assertGreaterEqual(
                bundle["metadata"]["n_channel_fields"],
                len(request.channels),
            )
