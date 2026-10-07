"""Catalog, contracts, and dependency pins. No observation files."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = ROOT / "adapters"
CONTRACTS = ROOT / "contracts"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
IDS = ("cma_radar", "gk2a", "himawari9", "fy4b", "mtg", "sevir")


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class CatalogTests(unittest.TestCase):
    def test_ids_and_contracts_match(self) -> None:
        catalog = _load(ADAPTERS / "catalog.json")
        self.assertEqual(catalog["version"], 1)
        listed = [item["id"] for item in catalog["data_adapters"]]
        self.assertEqual(listed, list(IDS))
        for item in catalog["data_adapters"]:
            self.assertTrue(SEMVER.match(item["version"]), item["id"])
            self.assertFalse(re.search(r"loader|_v\d", item["id"]))
            contract = _load(CONTRACTS / "data" / f"{item['id']}.json")
            for key in (
                "id",
                "version",
                "title",
                "instrument",
                "channels",
                "spatial",
                "native_cadence_minutes",
                "formats",
            ):
                self.assertEqual(item[key], contract[key], f"{item['id']}.{key}")
            self.assertTrue((ADAPTERS / item["path"] / "source.py").is_file())
            requirements = (ADAPTERS / item["requirements"]).read_text()
            self.assertNotIn(">=", requirements)
            if item["id"] == "cma_radar":
                self.assertNotIn("satpy", requirements)
                self.assertEqual(item["models"], [])
            elif item["id"] == "sevir":
                self.assertIn("h5py==", requirements)
                self.assertNotIn("satpy", requirements)
                self.assertEqual(
                    [model["id"] for model in item["models"]],
                    ["nowcastnet", "exprecast", "wadepre"],
                )
            else:
                self.assertIn("satpy==", requirements)
                self.assertEqual(item["models"], [])

    def test_sevir_model_contracts(self) -> None:
        catalog = _load(ADAPTERS / "catalog.json")
        sevir = next(item for item in catalog["data_adapters"] if item["id"] == "sevir")
        for model in sevir["models"]:
            contract = _load(CONTRACTS / "models" / "sevir" / f"{model['id']}.json")
            self.assertEqual(model["weights"], contract["weights"])
            self.assertTrue(
                str(model["weights"]["download_url"]).startswith(
                    "https://huggingface.co/"
                )
            )
            self.assertTrue(
                str(model["weights"]["artifacts_relpath"]).startswith("sevir/")
            )
            text = (ADAPTERS / model["path"] / "requirements.txt").read_text()
            self.assertIn("onnxruntime==", text)
            self.assertNotIn(">=", text)

    def test_examples_use_relative_data_dirs(self) -> None:
        for path in (ROOT / "tests" / "adapters" / "requests").glob("*/*.yaml"):
            text = path.read_text()
            self.assertNotIn("C:/", text, path.name)
            self.assertNotIn("C:\\", text, path.name)
            self.assertRegex(text, r'(?m)^data_dir: "[^"/][^"]*"')
