"""
NiB Data Adapter - Demo v1

Purpose
-------
One entry point for Hub-generated YAML configurations.

Flow:
    YAML config
        -> validate common fields
        -> select source-specific loader
        -> load local raw files
        -> normalize the loader result
        -> return one common DataBundle

This file does NOT download data and does NOT regrid/crop data.
"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Any, Dict

import yaml


# Lazy import is intentional:
# MTG/FY/Himawari have source-specific dependencies and initialization.
# We only import the selected loader.
SOURCE_REGISTRY = {
    "gk2a_ami": {
        "module": "gk2a_loader_v2.loader",
        "function": "load_gk2a",
        "kind": "gk2a",
    },
    "himawari9_ahi": {
        "module": "himawari9_loader_v1_1.loader",
        "function": "load_himawari",
        "kind": "himawari",
    },
    "fy4b_agri": {
        "module": "fy4b_agri_loader_v1.loader",
        "function": "load_fy4b",
        "kind": "fy4b",
    },
    "mtg_fci": {
        "module": "mtg_fci_loader_v1.loader",
        "function": "load_mtg_fci",
        "kind": "mtg",
    },
    "cma_radar": {
        "module": "cma_radar.loader",
        "function": "load_cma_radar",
        "kind": "radar",
    },
}


def read_config(config_path: str | Path) -> dict:
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file does not exist: {config_path}"
        )

    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError("YAML config must contain a mapping/object")

    return config


def validate_common_config(config: dict) -> None:
    """
    Validate only the common NiB Hub -> Data Adapter contract.

    Source-specific details are still checked inside each loader.
    """
    required = [
        "source",
        "data_dir",
        "initial_time",
        "history_steps",
        "interval_minutes",
        "channels",
        "region",
    ]

    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(
            f"Missing common config fields: {missing}"
        )

    source = str(config["source"]).lower()

    if source not in SOURCE_REGISTRY:
        raise ValueError(
            f"Unsupported source: {source}\n"
            f"Supported sources: {sorted(SOURCE_REGISTRY)}"
        )

    if not isinstance(config["channels"], list) or not config["channels"]:
        raise ValueError("'channels' must be a non-empty list")

    if int(config["history_steps"]) < 1:
        raise ValueError("'history_steps' must be >= 1")

    if int(config["interval_minutes"]) < 1:
        raise ValueError("'interval_minutes' must be >= 1")

    region = config["region"]
    if not isinstance(region, dict) or "bbox" not in region:
        raise ValueError(
            "'region' must contain 'bbox: [west, south, east, north]'"
        )

    bbox = region["bbox"]
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(
            "'region.bbox' must be [west, south, east, north]"
        )


def get_loader(source: str):
    """
    Lazily import only the loader requested by YAML.
    """
    source = source.lower()
    spec = SOURCE_REGISTRY[source]

    module = importlib.import_module(spec["module"])
    loader = getattr(module, spec["function"])

    return loader, spec


def normalize_result(
    source: str,
    raw_result: dict,
) -> tuple[dict, dict]:
    """
    Convert the four current loader return formats to a common structure.

    Common structure:
        data[time][channel] -> xarray.DataArray
        source_files[time][channel] -> list[str]

    No regridding, cropping, stacking, or unit harmonization is done here.
    """
    data: Dict[str, Dict[str, Any]] = {}
    source_files: Dict[str, Dict[str, list[str]]] = {}

    if source == "gk2a_ami":
        # Current GK2A:
        # results[time][channel] = {"file": ..., "dataset": xr.Dataset}
        for time_key, time_item in raw_result.items():
            data[time_key] = {}
            source_files[time_key] = {}

            for channel, item in time_item.items():
                ds = item["dataset"]

                # Materialize the array so downstream code does not depend
                # on an open NetCDF file handle.
                da = ds["image_pixel_values"].load().copy(deep=True)

                # Preserve useful dataset-level metadata.
                for key in (
                    "satellite_name",
                    "instrument_name",
                    "channel_spatial_resolution",
                    "channel_center_wavelength",
                ):
                    if key in ds.attrs and key not in da.attrs:
                        da.attrs[key] = ds.attrs[key]

                data[time_key][channel] = da
                source_files[time_key][channel] = [item["file"]]

                ds.close()

    elif source == "himawari9_ahi":
        # Current Himawari:
        # results[time][channel] = {"raw_files": [...], "data": DataArray}
        for time_key, time_item in raw_result.items():
            data[time_key] = {}
            source_files[time_key] = {}

            for channel, item in time_item.items():
                data[time_key][channel] = item["data"]
                source_files[time_key][channel] = list(
                    item["raw_files"]
                )

    elif source in {"fy4b_agri", "mtg_fci"}:
        # Current FY-4B / MTG:
        # results[time] = {
        #     "files": [...],
        #     "channels": {channel: DataArray}
        # }
        for time_key, time_item in raw_result.items():
            data[time_key] = {}
            source_files[time_key] = {}

            files = list(time_item["files"])

            for channel, da in time_item["channels"].items():
                data[time_key][channel] = da
                # The same logical product files may support all channels.
                source_files[time_key][channel] = files

    elif source == "cma_radar":
        # Radar loader is still a placeholder in the current codebase.
        # Keep this explicit rather than guessing a format.
        raise NotImplementedError(
            "CMA radar normalization will be added after the "
            "real radar file format/decoder is confirmed."
        )

    else:
        raise ValueError(f"No normalizer for source: {source}")

    return data, source_files


def load_from_config(config: dict) -> dict:
    """
    Main NiB Data Adapter API.

    Returns
    -------
    bundle : dict
        {
            "source": str,
            "request": {...},
            "data": {
                time: {
                    channel: xarray.DataArray
                }
            },
            "source_files": {
                time: {
                    channel: [file1, file2, ...]
                }
            },
            "metadata": {...}
        }
    """
    validate_common_config(config)

    source = str(config["source"]).lower()
    loader, spec = get_loader(source)

    print("========================================")
    print("NiB Data Adapter")
    print("source :", source)
    print("loader :", spec["module"])
    print("========================================")

    # Source-specific file discovery and decoding remain inside each loader.
    raw_result = loader(config)

    # Only normalize the return interface here.
    data, source_files = normalize_result(
        source=source,
        raw_result=raw_result,
    )

    bundle = {
        "source": source,
        "request": {
            "data_dir": config["data_dir"],
            "initial_time": config["initial_time"],
            "history_steps": int(config["history_steps"]),
            "interval_minutes": int(config["interval_minutes"]),
            "channels": list(config["channels"]),
            "region": config["region"],
        },
        "data": data,
        "source_files": source_files,
        "metadata": {
            "loader_module": spec["module"],
            "n_times": len(data),
            "n_channel_fields": sum(
                len(channel_map)
                for channel_map in data.values()
            ),
        },
    }

    print("\n=== NiB Data Adapter Success ===")
    print("source          :", bundle["source"])
    print("loaded times    :", bundle["metadata"]["n_times"])
    print(
        "loaded fields   :",
        bundle["metadata"]["n_channel_fields"],
    )
    print(
        "output          : DataBundle "
        "(time -> channel -> xarray.DataArray)"
    )

    return bundle


def load_from_yaml(config_path: str | Path) -> dict:
    """
    Public API for Hub/downstream modules.
    """
    config = read_config(config_path)
    return load_from_config(config)


def main():
    parser = argparse.ArgumentParser(
        description="NiB Data Adapter - Demo v1"
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Hub-generated YAML configuration",
    )
    args = parser.parse_args()

    load_from_yaml(args.config)


if __name__ == "__main__":
    main()
