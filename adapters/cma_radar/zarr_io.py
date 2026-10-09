"""Persistent Zarr output for CMA radar DataBundle objects.

This is the first NiB persistent-output prototype. It deliberately stores only
metadata that the current CMA CREF reader can support reliably. The layout is
inspired by the MLCast radar source-data specification, but it is not claimed
to be fully MLCast-compliant yet because projected x/y coordinates, a verified
CRS/grid_mapping, GeoZarr metadata, licensing metadata, and an explicit
missing-times variable are not currently available from the source reader.
"""

from __future__ import annotations

import datetime as dt
import inspect
import shutil
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import xarray as xr
import zarr
from numcodecs import Blosc

ZARR_SPEC_VERSION = "0.1.0"
MLCAST_RADAR_SPEC_URL = (
    "https://mlcast-community.github.io/mlcast-dataset-validator/"
    "specs/source_data/radar_precipitation/"
)


def _parse_utc(value: str) -> dt.datetime:
    out = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _datetime64(value: str) -> np.datetime64:
    return np.datetime64(_parse_utc(value), "ns")


def _bbox_from_coords(field: xr.DataArray) -> list[float]:
    return [
        float(field.longitude.min()),
        float(field.latitude.min()),
        float(field.longitude.max()),
        float(field.latitude.max()),
    ]


def _field_from_bundle(bundle: Mapping[str, Any], time_key: str) -> xr.DataArray:
    try:
        field = bundle["data"][time_key]["CREF"]
    except KeyError as exc:
        raise ValueError(
            f"CMA radar bundle is missing CREF at time {time_key}"
        ) from exc

    if not isinstance(field, xr.DataArray):
        raise TypeError(
            "CMA Zarr output requires xarray.DataArray fields so latitude and "
            "longitude are preserved."
        )

    if field.dims != ("latitude", "longitude"):
        raise ValueError(
            "CMA CREF field must use dimensions ('latitude', 'longitude'); "
            f"got {field.dims!r}"
        )

    if "latitude" not in field.coords or "longitude" not in field.coords:
        raise ValueError("CMA CREF field must include latitude and longitude coordinates")

    return field


def _source_file_names(bundle: Mapping[str, Any], time_keys: list[str]) -> list[str]:
    names = []
    for time_key in time_keys:
        paths = bundle.get("source_files", {}).get(time_key, {}).get("CREF", [])
        names.append(Path(paths[0]).name if paths else "")
    return names


def _dataset_for_time(
    field: xr.DataArray,
    time_key: str,
    global_attrs: dict[str, Any],
) -> xr.Dataset:
    latitude = np.asarray(field.latitude.values, dtype=np.float64)
    longitude = np.asarray(field.longitude.values, dtype=np.float64)
    reflectivity = np.asarray(field.values, dtype=np.float32)

    generation_time = field.attrs.get("generation_time")
    if not generation_time:
        raise ValueError(
            f"CMA CREF field at {time_key} is missing generation_time metadata"
        )

    ds = xr.Dataset(
        data_vars={
            "reflectivity": (
                ("time", "latitude", "longitude"),
                reflectivity[np.newaxis, ...],
                {
                    "long_name": "CMA weather radar mosaic composite reflectivity",
                    "standard_name": "equivalent_reflectivity_factor",
                    "units": "dBZ",
                    "source_variable": "CREF",
                },
            )
        },
        coords={
            "time": (
                "time",
                np.asarray([_datetime64(time_key)], dtype="datetime64[ns]"),
                {"standard_name": "time", "axis": "T"},
            ),
            "generation_time": (
                "time",
                np.asarray([_datetime64(str(generation_time))], dtype="datetime64[ns]"),
                {
                    "long_name": "CREF product generation time",
                },
            ),
            "latitude": (
                "latitude",
                latitude,
                {
                    "standard_name": "latitude",
                    "units": "degrees_north",
                    "axis": "Y",
                },
            ),
            "longitude": (
                "longitude",
                longitude,
                {
                    "standard_name": "longitude",
                    "units": "degrees_east",
                    "axis": "X",
                },
            ),
        },
        attrs=global_attrs,
    )

    return ds



def _write_zarr_v2(ds: xr.Dataset, output: Path, **kwargs) -> None:
    """Write Zarr v2 across both older and newer xarray APIs."""
    params = inspect.signature(ds.to_zarr).parameters

    if "zarr_format" in params:
        kwargs["zarr_format"] = 2
    elif "zarr_version" in params:
        kwargs["zarr_version"] = 2

    ds.to_zarr(output, **kwargs)

def write_cma_zarr(
    bundle: Mapping[str, Any],
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write a CMA radar DataBundle to one consolidated Zarr v2 dataset.

    The first-stage NiB radar layout is:

    - variable: ``reflectivity`` (float32, dBZ)
    - dimensions: ``time, latitude, longitude``
    - coordinates: ``time, generation_time, latitude, longitude``
    - one time step per Zarr chunk, with ZSTD compression

    This is a reliable minimum based on metadata available from the current
    CREF reader. It does not invent unavailable projection/CRS information.
    """
    if bundle.get("source") != "cma_radar":
        raise ValueError("write_cma_zarr only accepts source='cma_radar'")

    data = bundle.get("data")
    if not isinstance(data, Mapping) or not data:
        raise ValueError("CMA radar bundle contains no data")

    output = Path(output_path)
    if output.exists():
        if not overwrite:
            raise FileExistsError(
                f"Zarr output already exists: {output}. Use --overwrite-zarr to replace it."
            )
        if output.is_dir():
            shutil.rmtree(output)
        else:
            output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)

    time_keys = sorted(data.keys(), key=_parse_utc)
    first_field = _field_from_bundle(bundle, time_keys[0])
    first_lat = np.asarray(first_field.latitude.values)
    first_lon = np.asarray(first_field.longitude.values)

    request = bundle.get("request", {})
    metadata = bundle.get("metadata", {})
    requested_bbox = request.get("region", {}).get("bbox")
    native_resolution = first_field.attrs.get("native_resolution_deg")

    global_attrs: dict[str, Any] = {
        "source": "cma_radar",
        "instrument": "CREF mosaic",
        "product": "CREF",
        "nib_zarr_spec_version": ZARR_SPEC_VERSION,
        "nib_zarr_spec_status": (
            "prototype; MLCast-inspired but not fully MLCast-compliant"
        ),
        "design_reference": MLCAST_RADAR_SPEC_URL,
        "georeferencing_status": (
            "latitude/longitude available; projected x/y and verified CRS are not "
            "provided by the current CREF reader"
        ),
        "missing_time_policy": (
            "the Data Adapter raises an error when a requested time is unavailable"
        ),
        "output_bbox": _bbox_from_coords(first_field),
        "source_file_names": _source_file_names(bundle, time_keys),
    }

    if requested_bbox is not None:
        global_attrs["requested_bbox"] = [float(v) for v in requested_bbox]
    if native_resolution is not None:
        global_attrs["native_resolution_deg"] = float(native_resolution)
    if "version" in metadata:
        global_attrs["data_adapter_version"] = str(metadata["version"])

    compressor = Blosc(
        cname="zstd",
        clevel=3,
        shuffle=Blosc.BITSHUFFLE,
    )

    for index, time_key in enumerate(time_keys):
        field = _field_from_bundle(bundle, time_key)

        latitude = np.asarray(field.latitude.values)
        longitude = np.asarray(field.longitude.values)
        if not np.array_equal(latitude, first_lat) or not np.array_equal(
            longitude, first_lon
        ):
            raise ValueError(
                "CMA radar Zarr prototype requires the same spatial grid at all times"
            )

        ds = _dataset_for_time(field, time_key, global_attrs if index == 0 else {})

        if index == 0:
            encoding = {
                "reflectivity": {
                    "dtype": "float32",
                    "compressor": compressor,
                    "chunks": (
                        1,
                        int(field.sizes["latitude"]),
                        int(field.sizes["longitude"]),
                    ),
                }
            }
            _write_zarr_v2(
                ds,
                output,
                mode="w",
                consolidated=False,
                encoding=encoding,
            )
        else:
            _write_zarr_v2(
                ds,
                output,
                mode="a",
                append_dim="time",
                consolidated=False,
            )

    zarr.consolidate_metadata(str(output))
    return output
