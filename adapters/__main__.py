"""Run one data adapter from a YAML request.

Usage:
    python -m adapters --config <yaml> [--data-root <path>]
    python -m adapters --config <yaml> --output-zarr <path>

Persistent Zarr output is optional. If neither ``--output-zarr`` nor
``output.zarr.path`` is provided in YAML, the adapter only returns the
in-memory DataBundle.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from collections.abc import Mapping
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


def zarr_options_from_config(config: Mapping) -> tuple[str | None, bool]:
    """Read optional persistent-output settings from YAML.

    Supported YAML forms:

    output:
      zarr: "runs/cma_radar.zarr"

    output:
      zarr:
        path: "runs/cma_radar.zarr"
        overwrite: true

    The command-line flags still take precedence.
    """
    output = config.get("output")
    if output is None:
        return None, False
    if not isinstance(output, Mapping):
        raise ConstraintError("output must be a mapping when provided")

    zarr = output.get("zarr")
    if zarr is None:
        return None, False
    if isinstance(zarr, str):
        return zarr, False
    if not isinstance(zarr, Mapping):
        raise ConstraintError("output.zarr must be a path string or a mapping")

    enabled = bool(zarr.get("enabled", True))
    if not enabled:
        return None, False

    path = zarr.get("path") or zarr.get("store")
    if not path:
        raise ConstraintError("output.zarr.path is required when output.zarr is enabled")

    return str(path), bool(zarr.get("overwrite", False))


def run(
    config_path: str,
    data_root: str | None,
    output_zarr: str | None = None,
    overwrite_zarr: bool = False,
) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file does not exist: {path}")
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ConstraintError("YAML config must contain a mapping")

    yaml_zarr, yaml_overwrite = zarr_options_from_config(config)
    output_zarr = output_zarr or yaml_zarr
    overwrite_zarr = overwrite_zarr or yaml_overwrite

    # YAML is converted into the shared typed request before source code runs.
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

    if output_zarr:
        if request.source == "cma_radar":
            from adapters.cma_radar.zarr_io import write_cma_zarr

            written = write_cma_zarr(
                bundle,
                output_zarr,
                overwrite=overwrite_zarr,
            )
            spec_label = "cma_radar v0.1 prototype"
        elif request.source in {"fy4b", "gk2a", "himawari9", "mtg"}:
            from adapters.satellite_zarr import write_satellite_zarr

            written = write_satellite_zarr(
                bundle,
                output_zarr,
                overwrite=overwrite_zarr,
            )
            spec_label = "satellite v0.1 prototype"
        else:
            raise ConstraintError(
                "Persistent Zarr output is currently implemented for CMA radar "
                "and the FY-4B, GK2A, Himawari-9, and MTG satellite adapters"
            )

        print("zarr output     :", written)
        print("zarr spec       :", spec_label)
    else:
        print("zarr output     : disabled")

    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="NiB data adapter")
    parser.add_argument("--config", required=True, help="YAML request")
    parser.add_argument(
        "--data-root",
        default=None,
        help="Host data root. Overrides NIB_DATA_ROOT.",
    )
    parser.add_argument(
        "--output-zarr",
        default=None,
        help=(
            "Optional persistent Zarr output path. "
            "Overrides output.zarr.path in YAML. "
            "Supported by CMA radar and the current geostationary satellite adapters."
        ),
    )
    parser.add_argument(
        "--overwrite-zarr",
        action="store_true",
        help="Replace an existing --output-zarr dataset.",
    )
    args = parser.parse_args()
    run(
        args.config,
        data_root_from(args.data_root),
        output_zarr=args.output_zarr,
        overwrite_zarr=args.overwrite_zarr,
    )


if __name__ == "__main__":
    main()
