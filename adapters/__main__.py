"""Run one data adapter from a YAML request.

Usage: python -m adapters --config <yaml> [--data-root <path>]
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path

import yaml

from interfaces.data import request_from_mapping
from interfaces.errors import ConstraintError


def catalog_path() -> Path:
    return Path(__file__).resolve().parent / "catalog.json"


def load_catalog() -> dict:
    with catalog_path().open(encoding="utf-8") as handle:
        return json.load(handle)


def adapter_for(source: str):
    catalog = load_catalog()
    known = [item["id"] for item in catalog["data_adapters"]]
    if source not in known:
        raise ConstraintError(
            f"Unsupported source: {source}. Supported sources: {known}"
        )
    module = importlib.import_module(f"adapters.{source}.source")
    return module.ADAPTER


def data_root_from(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    env = os.environ.get("NIB_DATA_ROOT", "").strip()
    return env or None


def run(config_path: str, data_root: str | None) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file does not exist: {path}")
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ConstraintError("YAML config must contain a mapping")
    request = request_from_mapping(config, data_root)
    adapter = adapter_for(request.source)
    adapter.validate(request)
    print("========================================")
    print("NiB Data Adapter")
    print("source :", request.source)
    print("version:", adapter.capabilities().version)
    print("========================================")
    bundle = adapter.load(request)
    print("\n=== NiB Data Adapter Success ===")
    print("source          :", bundle["source"])
    print("loaded times    :", bundle["metadata"]["n_times"])
    print("loaded fields   :", bundle["metadata"]["n_channel_fields"])
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="NiB data adapter")
    parser.add_argument("--config", required=True, help="YAML request")
    parser.add_argument(
        "--data-root",
        default=None,
        help="Host data root. Overrides NIB_DATA_ROOT.",
    )
    args = parser.parse_args()
    run(args.config, data_root_from(args.data_root))


if __name__ == "__main__":
    main()
