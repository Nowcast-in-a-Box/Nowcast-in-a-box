"""Optional persistent Zarr output for NiB geostationary satellite DataBundles.

The first satellite Zarr prototype preserves the data and metadata already
exposed by the current NiB loaders. It does not regrid, resample, calibrate, or
invent missing geolocation metadata.

Supported sources:
- FY-4B AGRI (``fy4b``)
- GK2A AMI (``gk2a``)
- Himawari-9 AHI (``himawari9``)
- MTG FCI (``mtg``)

Different channels may use different native resolutions. To avoid forcing a
common grid, every channel gets its own spatial dimensions inside one xarray
Dataset. Example::

    C13(time, C13_y, C13_x)
    C02(time, C02_y, C02_x)

Downstream code can therefore still open the whole store with
``xarray.open_zarr(...)`` while each channel keeps its native grid.
"""

from __future__ import annotations

import datetime as dt
import inspect
import json
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

SATELLITE_SOURCES = frozenset({"fy4b", "gk2a", "himawari9", "mtg"})
ZARR_SPEC_VERSION = "0.1.0"
MLCAST_SATELLITE_ISSUE_URL = (
    "https://github.com/mlcast-community/mlcast-dataset-validator/issues/40"
)
MAX_SPATIAL_CHUNK = 1024

# Runtime/private Satpy objects are either serialized separately (``area``) or
# not useful as persistent handoff metadata.
_SKIP_ATTRS = {
    "area",
    "_satpy_id",
    "id",
    "ancillary_variables",
    "modifiers",
    # These can vary by timestep; keeping only the first value as a scalar
    # variable attribute would be misleading in a multi-time Zarr dataset.
    "start_time",
    "end_time",
    "time_parameters",
    "orbital_parameters",
    # The original Satpy references point to an object-valued CRS coordinate
    # that is intentionally converted to explicit WKT metadata below.
    "grid_mapping",
    "coordinates",
}


def _parse_utc(value: str) -> dt.datetime:
    out = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _datetime64(value: str) -> np.datetime64:
    return np.datetime64(_parse_utc(value), "ns")


def _safe_identifier(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_]+", "_", str(value).strip())
    text = text.strip("_") or "channel"
    if text[0].isdigit():
        text = f"ch_{text}"
    return text


def _jsonable(value: Any) -> Any:
    """Convert metadata to a JSON-safe representation without inventing values."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, np.datetime64):
        return np.datetime_as_string(value, unit="ns")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    # Satpy objects such as WavelengthRange are useful when rendered as text,
    # but are not directly JSON serializable.
    return str(value)


def _zarr_attr(value: Any) -> Any:
    """Return an xarray/Zarr-safe attribute value."""
    converted = _jsonable(value)
    if isinstance(converted, dict):
        return json.dumps(converted, sort_keys=True, ensure_ascii=False)
    if isinstance(converted, list):
        # Lists of primitive values are safe. Nested structures are stored as
        # JSON text to avoid backend-specific attribute encoding differences.
        if all(item is None or isinstance(item, (str, bool, int, float)) for item in converted):
            return converted
        return json.dumps(converted, sort_keys=True, ensure_ascii=False)
    return converted


def _clean_attrs(attrs: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        if key in _SKIP_ATTRS:
            continue
        try:
            out[str(key)] = _zarr_attr(value)
        except Exception:
            out[str(key)] = str(value)
    return out


def _crs_wkt(field: xr.DataArray) -> str | None:
    """Return CRS WKT only when the current DataArray/area already provides it."""
    candidates = []

    if "crs" in field.coords:
        try:
            candidates.append(field.coords["crs"].item())
        except Exception:
            pass

    area = field.attrs.get("area")
    if area is not None:
        try:
            candidates.append(area.crs)
        except Exception:
            pass

    for candidate in candidates:
        if candidate is None:
            continue
        to_wkt = getattr(candidate, "to_wkt", None)
        if callable(to_wkt):
            try:
                return str(to_wkt())
            except Exception:
                continue
    return None


def _spatial_attrs(field: xr.DataArray) -> dict[str, Any]:
    """Serialize spatial metadata already exposed by the loader."""
    out: dict[str, Any] = {}
    area = field.attrs.get("area")

    if area is not None:
        for source_name, target_name in (
            ("area_id", "area_id"),
            ("description", "area_description"),
            ("proj_id", "projection_id"),
            ("area_extent", "area_extent"),
            ("pixel_size_x", "pixel_size_x"),
            ("pixel_size_y", "pixel_size_y"),
        ):
            try:
                value = getattr(area, source_name)
            except Exception:
                continue
            out[target_name] = _zarr_attr(value)

        wkt = _crs_wkt(field)
        if wkt:
            out["crs_wkt"] = wkt

        out["spatial_metadata_status"] = (
            "native projected coordinates/projection metadata preserved from loader"
        )
    else:
        # This is currently the case for the GK2A direct xarray reader used by
        # NiB. We preserve its native pixel dimensions and do not invent a CRS.
        out["spatial_metadata_status"] = (
            "native pixel dimensions preserved; current loader exposes no area/CRS metadata"
        )

    return out


def _source_file_names(bundle: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    source_files = bundle.get("source_files", {})

    if not isinstance(source_files, Mapping):
        return names

    for time_item in source_files.values():
        if not isinstance(time_item, Mapping):
            continue
        for paths in time_item.values():
            for raw_path in paths or []:
                name = Path(str(raw_path)).name
                if name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


def _field_for_channel(
    bundle: Mapping[str, Any],
    time_key: str,
    channel: str,
) -> xr.DataArray:
    try:
        field = bundle["data"][time_key][channel]
    except KeyError as exc:
        raise ValueError(
            f"Satellite bundle is missing channel {channel!r} at time {time_key}"
        ) from exc

    if not isinstance(field, xr.DataArray):
        raise TypeError(
            "Satellite Zarr output requires xarray.DataArray fields; "
            f"got {type(field).__name__} for {channel!r} at {time_key}"
        )
    if field.ndim != 2:
        raise ValueError(
            "Satellite Zarr prototype expects 2D channel fields; "
            f"got dims={field.dims!r} for {channel!r} at {time_key}"
        )
    return field


def _native_coord(field: xr.DataArray, dim: str):
    """Return a 1D native dimension coordinate if one actually exists."""
    if dim not in field.coords:
        return None
    coord = field.coords[dim]
    if coord.ndim != 1 or coord.dims != (dim,):
        return None
    return coord


def _prepare_channel(
    bundle: Mapping[str, Any],
    time_keys: list[str],
    channel: str,
) -> xr.DataArray:
    first = _field_for_channel(bundle, time_keys[0], channel)
    native_dims = tuple(str(dim) for dim in first.dims)
    native_shape = tuple(int(size) for size in first.shape)
    safe_channel = _safe_identifier(channel)

    # Each channel owns its dimensions so different native satellite
    # resolutions can coexist in one Dataset without regridding.
    target_dims = (
        f"{safe_channel}_y",
        f"{safe_channel}_x",
    )

    arrays: list[xr.DataArray] = []

    first_native_coords: list[np.ndarray | None] = []
    for dim in native_dims:
        coord = _native_coord(first, dim)
        first_native_coords.append(None if coord is None else np.asarray(coord.values))

    for time_key in time_keys:
        field = _field_for_channel(bundle, time_key, channel)
        if tuple(str(dim) for dim in field.dims) != native_dims:
            raise ValueError(
                f"Channel {channel!r} changed native dimensions across times: "
                f"{native_dims!r} -> {field.dims!r}"
            )
        if tuple(int(size) for size in field.shape) != native_shape:
            raise ValueError(
                f"Channel {channel!r} changed native shape across times: "
                f"{native_shape!r} -> {field.shape!r}"
            )

        coords: dict[str, Any] = {}
        for index, (native_dim, target_dim) in enumerate(zip(native_dims, target_dims)):
            coord = _native_coord(field, native_dim)
            expected = first_native_coords[index]
            if expected is None:
                if coord is not None:
                    raise ValueError(
                        f"Channel {channel!r} spatial coordinate availability changed "
                        f"across times for {native_dim!r}"
                    )
                continue

            if coord is None or not np.array_equal(np.asarray(coord.values), expected):
                raise ValueError(
                    f"Channel {channel!r} spatial grid changed across times for "
                    f"{native_dim!r}; the prototype does not regrid automatically"
                )
            coords[target_dim] = xr.DataArray(
                coord.data,
                dims=(target_dim,),
                attrs=_clean_attrs(coord.attrs),
            )

        attrs = _clean_attrs(field.attrs)
        attrs.update(_spatial_attrs(field))
        attrs["channel_id"] = str(channel)
        attrs["native_dimensions"] = list(native_dims)
        attrs["native_shape"] = list(native_shape)

        clean = xr.DataArray(
            field.data,
            dims=target_dims,
            coords=coords,
            name=safe_channel,
            attrs=attrs,
        )
        clean = clean.expand_dims(
            time=np.asarray([_datetime64(time_key)], dtype="datetime64[ns]")
        )
        arrays.append(clean)

    if len(arrays) == 1:
        combined = arrays[0]
    else:
        combined = xr.concat(
            arrays,
            dim="time",
            coords="minimal",
            compat="equals",
            join="exact",
        )

    combined.name = safe_channel
    return combined


def _write_zarr_v2(ds: xr.Dataset, output: Path, **kwargs) -> None:
    """Write Zarr v2 across older and newer xarray APIs."""
    params = inspect.signature(ds.to_zarr).parameters
    if "zarr_format" in params:
        kwargs["zarr_format"] = 2
    elif "zarr_version" in params:
        kwargs["zarr_version"] = 2
    ds.to_zarr(output, **kwargs)


def build_satellite_dataset(bundle: Mapping[str, Any]) -> xr.Dataset:
    """Build the first-stage satellite xarray Dataset without writing to disk."""
    source = str(bundle.get("source", ""))
    if source not in SATELLITE_SOURCES:
        raise ValueError(
            "Satellite Zarr output supports only "
            f"{sorted(SATELLITE_SOURCES)}; got source={source!r}"
        )

    data = bundle.get("data")
    if not isinstance(data, Mapping) or not data:
        raise ValueError("Satellite bundle contains no data")

    time_keys = sorted(data.keys(), key=_parse_utc)
    first_time = data[time_keys[0]]
    if not isinstance(first_time, Mapping) or not first_time:
        raise ValueError("Satellite bundle contains no channels")

    channels = list(first_time.keys())
    for time_key in time_keys[1:]:
        current = data[time_key]
        if set(current.keys()) != set(channels):
            raise ValueError(
                "Satellite Zarr prototype requires the same requested channels "
                f"at every time; mismatch at {time_key}"
            )

    data_vars: dict[str, xr.DataArray] = {}
    channel_name_map: dict[str, str] = {}
    for channel in channels:
        variable_name = _safe_identifier(channel)
        if variable_name in data_vars:
            raise ValueError(
                f"Channel names collide after Zarr-safe normalization: {channel!r}"
            )
        data_vars[variable_name] = _prepare_channel(
            bundle,
            time_keys,
            str(channel),
        )
        channel_name_map[str(channel)] = variable_name

    request = bundle.get("request", {})
    metadata = bundle.get("metadata", {})
    files = _source_file_names(bundle)

    attrs: dict[str, Any] = {
        "source": source,
        "nib_zarr_spec_version": ZARR_SPEC_VERSION,
        "nib_zarr_spec_status": (
            "prototype; preserves current NiB satellite-loader output without regridding"
        ),
        "design_reference": MLCAST_SATELLITE_ISSUE_URL,
        "spatial_policy": (
            "each channel keeps its native grid; channel-specific dimensions avoid forced regridding"
        ),
        "channels": [str(channel) for channel in channels],
        "channel_variable_map": json.dumps(channel_name_map, sort_keys=True),
        "source_file_count": len(files),
        "source_file_names": files,
    }

    requested_bbox = request.get("region", {}).get("bbox") if isinstance(request, Mapping) else None
    if requested_bbox is not None:
        attrs["requested_bbox"] = [float(v) for v in requested_bbox]
        attrs["region_status"] = (
            "request recorded; satellite loaders currently preserve native full-disk/source domain"
        )
    if isinstance(metadata, Mapping) and "version" in metadata:
        attrs["data_adapter_version"] = str(metadata["version"])

    ds = xr.Dataset(data_vars=data_vars, attrs=attrs)
    ds["time"].attrs.update({"standard_name": "time", "axis": "T"})
    return ds


def write_satellite_zarr(
    bundle: Mapping[str, Any],
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write one NiB satellite DataBundle to a consolidated Zarr v2 dataset."""
    ds = build_satellite_dataset(bundle)

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

    import zarr
    from numcodecs import Blosc

    compressor = Blosc(
        cname="zstd",
        clevel=3,
        shuffle=Blosc.BITSHUFFLE,
    )

    encoding: dict[str, dict[str, Any]] = {}
    for name, var in ds.data_vars.items():
        spatial = var.shape[-2:]
        encoding[name] = {
            "compressor": compressor,
            "chunks": (
                1,
                min(int(spatial[0]), MAX_SPATIAL_CHUNK),
                min(int(spatial[1]), MAX_SPATIAL_CHUNK),
            ),
        }

    _write_zarr_v2(
        ds,
        output,
        mode="w",
        consolidated=False,
        encoding=encoding,
    )
    zarr.consolidate_metadata(str(output))
    return output
