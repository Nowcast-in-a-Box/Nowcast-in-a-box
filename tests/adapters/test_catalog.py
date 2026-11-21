"""Catalog, contracts, and dependency pins. No observation files."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import pytest

from adapters import __main__ as readers
from adapters.__main__ import adapter_ids
from interfaces.errors import ConstraintError

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "adapters"
CONTRACTS = ROOT / "atlas" / "contracts"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
IDS = ("cma_radar", "gk2a", "himawari9", "fy4b", "mtg", "sevir") + tuple(
    f"mlcast_{item}"
    for item in (
        "radklim_hourly",
        "radklim_5_minutes",
        "dmi_10_minutes",
        "it_dpc_sri_5min",
        "uk_metoffice_5min",
        "be_rmi_radclim_mfb_5min",
        "fr_mf_prate_5min",
    )
)


def test_dispatch_discovers_local_sources_without_atlas(tmp_path, monkeypatch):
    monkeypatch.setattr(readers, "__file__", str(tmp_path / "__main__.py"))
    for name in ("new_reader", "invalid-name", "no_source"):
        (tmp_path / name).mkdir()
    for name in ("new_reader", "invalid-name"):
        (tmp_path / name / "source.py").touch()
    assert readers.adapter_ids() == ["new_reader"]

    imported = []
    adapter = object()

    def import_module(name):
        imported.append(name)
        return type("ReaderModule", (), {"ADAPTER": adapter})

    monkeypatch.setattr(readers.importlib, "import_module", import_module)
    assert readers.adapter_for("new_reader") is adapter
    for invalid in ("invalid-name", "../new_reader", "no_source"):
        with pytest.raises(ConstraintError, match="Unsupported source"):
            readers.adapter_for(invalid)
    assert imported == ["adapters.new_reader.source"]


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class CatalogTests(unittest.TestCase):
    def test_ids_and_contracts_match(self) -> None:
        self.assertLessEqual(set(IDS), set(adapter_ids()))
        for source in adapter_ids():
            contract = _load(CONTRACTS / "data" / f"{source}.json")
            self.assertEqual(contract["id"], source)
            self.assertTrue(SEMVER.fullmatch(contract["version"]), source)
            self.assertFalse(re.search(r"loader|_vd", source))
            requirements = (ADAPTERS / source / "requirements.txt").read_text()
            # A Python environment marker (e.g. python_version >= "3.9")
            # is not an unpinned runtime dependency. Check only package specs.
            package_specs = [
                line.split(";", 1)[0].strip()
                for line in requirements.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            self.assertTrue(all(">=" not in line for line in package_specs))
            if source == "sevir":
                self.assertIn("h5py==", requirements)
                self.assertNotIn("satpy", requirements)
            elif source.startswith("mlcast_"):
                self.assertIn("zarr==", requirements)
                self.assertNotIn("satpy", requirements)
            elif source in {"fy4b", "gk2a", "himawari9", "mtg"}:
                self.assertIn("satpy==", requirements)

    def test_sevir_model_contracts(self) -> None:
        for path in sorted((CONTRACTS / "models/sevir").glob("*.json")):
            weights = _load(path)["weights"]
            self.assertTrue(
                weights["download_url"].startswith("https://huggingface.co/")
            )
            self.assertTrue(weights["artifacts_relpath"].startswith("sevir/"))
            text = (ADAPTERS / "sevir" / path.stem / "requirements.txt").read_text()
            self.assertIn("onnxruntime==", text)
            self.assertNotIn(">=", text)

    def test_examples_use_relative_data_dirs(self) -> None:
        for path in (ROOT / "tests" / "adapters" / "requests").glob("*/*.yaml"):
            text = path.read_text()
            self.assertNotIn("C:/", text, path.name)
            self.assertNotIn("C:\\", text, path.name)
            self.assertRegex(text, r'(?m)^data_dir: "[^"/][^"]*"')
