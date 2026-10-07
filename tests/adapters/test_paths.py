"""Path rules shared with handler.ResolveDataDir."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from interfaces.errors import ConstraintError
from utils.paths import resolve_data_dir


class PathTests(unittest.TestCase):
    def test_relative_joins_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            got = resolve_data_dir("cma_radar", tmp)
            self.assertEqual(got, Path(tmp) / "cma_radar")

    def test_relative_requires_root(self) -> None:
        with self.assertRaises(ConstraintError):
            resolve_data_dir("cma_radar", None)

    def test_rejects_parent_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConstraintError):
                resolve_data_dir("../outside", tmp)

    def test_absolute_ignores_missing_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            got = resolve_data_dir(tmp, None)
            self.assertEqual(got, Path(tmp))

    @unittest.skipIf(os.name == "nt", "drive-letter paths are native on Windows")
    def test_foreign_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConstraintError):
                resolve_data_dir("C:/data", tmp)
