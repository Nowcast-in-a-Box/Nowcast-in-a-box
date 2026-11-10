"""Local MLCast radar Zarr adapter (no network access or model preprocessing).

YAML requests select an exact sequence of UTC timestamps, native channel and
geographic bbox from a previously-downloaded, larger local Zarr sample.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import xarray as xr

from interfaces.data import DeclaredDataAdapter, bundle_from_parts, loader_config
from interfaces.errors import ConstraintError


def _utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _key(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def locate_store(data_dir: Path) -> Path:
    """Accept a direct .zarr directory or a directory holding exactly one."""
    if data_dir.suffix.lower() == ".zarr" and data_dir.is_dir():
        return data_dir
    if not data_dir.is_dir():
        raise FileNotFoundError(f"MLCast local directory does not exist: {data_dir}")
    candidates = sorted(p for p in data_dir.glob("*.zarr") if p.is_dir())
    if len(candidates) != 1:
        raise ConstraintError(
            f"Expected exactly one *.zarr under {data_dir}; found {len(candidates)}. "
            "Pass data_dir pointing directly to the desired *.zarr store."
        )
    return candidates[0]


def _latlon(field: xr.DataArray, dataset: xr.Dataset):
    """Use real 1D/2D lat-lon, or transform a verified CF projected grid."""
    if len(field.dims) != 2:
        raise ConstraintError(f"Expected one 2D radar frame, got {field.dims}")
    y_dim, x_dim = field.dims
    lat = lon = None
    for lat_name, lon_name in (("lat", "lon"), ("latitude", "longitude")):
        if lat_name in dataset and lon_name in dataset:
            lat, lon = dataset[lat_name], dataset[lon_name]
            break
    if lat is not None and lon is not None:
        if lat.dims == (y_dim,) and lon.dims == (x_dim,):
            lat2d, lon2d = np.meshgrid(lat.values, lon.values, indexing="ij")
        elif lat.dims == lon.dims == (y_dim, x_dim):
            lat2d, lon2d = np.asarray(lat.values), np.asarray(lon.values)
        else:
            raise ConstraintError(
                f"Unsupported lat/lon grid dimensions: {lat.dims}, {lon.dims}"
            )
        return np.asarray(lat2d, dtype=float), np.asarray(lon2d, dtype=float)

    mapping_name = field.attrs.get("grid_mapping") or field.encoding.get("grid_mapping")
    if not mapping_name or mapping_name not in dataset:
        raise ConstraintError(
            "Dataset has no usable latitude/longitude or CF grid_mapping; "
            "cannot safely crop a geographic bbox"
        )
    mapping = dataset[mapping_name].attrs
    crs_wkt = mapping.get("crs_wkt") or mapping.get("spatial_ref")
    if not crs_wkt:
        raise ConstraintError(f"CF grid_mapping {mapping_name!r} has no verified WKT")
    try:
        from pyproj import CRS, Transformer
    except ImportError as exc:
        raise ImportError("Projected MLCast bbox cropping requires pyproj") from exc
    if x_dim not in field.coords or y_dim not in field.coords:
        raise ConstraintError("Projected radar grids require numeric x/y coordinates")
    x, y = np.meshgrid(np.asarray(field[x_dim].values), np.asarray(field[y_dim].values))
    lon2d, lat2d = Transformer.from_crs(
        CRS.from_wkt(crs_wkt), CRS.from_epsg(4326), always_xy=True
    ).transform(x, y)
    return np.asarray(lat2d, dtype=float), np.asarray(lon2d, dtype=float)


def spatial_selection(field: xr.DataArray, dataset: xr.Dataset, bbox: tuple):
    """Calculate crop once; reject even partially out-of-bounds bbox requests.

    The geolocation bounding box is a necessary condition for coverage. For
    rotated/irregular source grids it is not a proof of full footprint coverage;
    points outside the requested bbox are masked after slicing.
    """
    lat, lon = _latlon(field, dataset)
    valid = np.isfinite(lat) & np.isfinite(lon)
    if not valid.any():
        raise ConstraintError("No finite lat/lon coordinates in local radar sample")
    actual = (
        float(np.min(lon[valid])), float(np.min(lat[valid])),
        float(np.max(lon[valid])), float(np.max(lat[valid])),
    )
    west, south, east, north = bbox
    tol = 1e-6  # allow YAML float roundoff, but not meaningful out-of-range crops
    if (west < actual[0] - tol or south < actual[1] - tol or
            east > actual[2] + tol or north > actual[3] + tol):
        raise ConstraintError(
            f"Requested region.bbox {list(bbox)} exceeds the local sample "
            f"geographic bounds {list(actual)}. Download a larger sample or "
            "edit the YAML bbox to be within the downloaded coverage."
        )
    inside = valid & (lon >= west - tol) & (lon <= east + tol) & (lat >= south - tol) & (lat <= north + tol)
    if not inside.any():
        raise ConstraintError(f"No local grid points fall inside bbox {list(bbox)}")
    rows, cols = np.where(inside)
    row_slice = slice(int(rows.min()), int(rows.max()) + 1)
    col_slice = slice(int(cols.min()), int(cols.max()) + 1)
    return row_slice, col_slice, inside[row_slice, col_slice], lat[row_slice, col_slice], lon[row_slice, col_slice], actual


def _crop_selected(field, selection, bbox):
    row_slice, col_slice, inside, lat, lon, _bounds = selection
    frame = field.isel({field.dims[0]: row_slice, field.dims[1]: col_slice})
    mask = xr.DataArray(inside, dims=frame.dims)
    frame = frame.where(mask)
    frame = frame.assign_coords({
        "latitude": (frame.dims, lat), "longitude": (frame.dims, lon)
    })
    frame.attrs = dict(field.attrs)
    frame.attrs.update({"requested_region": list(bbox), "crop_applied": True})
    return frame


def _crop(field, dataset, bbox):
    """Backwards-compatible helper used in earlier tests."""
    return _crop_selected(field, spatial_selection(field, dataset, bbox), bbox)


def _projected_crs_linear_unit(field: xr.DataArray):
    """Get a verified planar axis unit from a CF CRS, if the axes omit units.

    Do not infer metres merely from a numeric x/y step or the variable name.
    Only projected CRSs with two compatible linear axes qualify. This leaves
    geographic, missing and ambiguous CRS metadata explicitly unresolved.
    """
    names = []
    mapping = field.attrs.get("grid_mapping") or field.encoding.get("grid_mapping")
    if isinstance(mapping, str):
        names.extend(mapping.split())
    names.extend(("spatial_ref", "crs"))
    names.extend(
        name for name, coord in field.coords.items()
        if "crs_wkt" in coord.attrs or "spatial_ref" in coord.attrs
    )
    try:
        from pyproj import CRS
        from pyproj.exceptions import CRSError
    except ImportError:
        return None
    for name in dict.fromkeys(names):
        if name not in field.coords:
            continue
        attrs = field.coords[name].attrs
        wkt = attrs.get("crs_wkt") or attrs.get("spatial_ref")
        if not wkt:
            continue
        try:
            crs = CRS.from_wkt(wkt)
            if not crs.is_projected or len(crs.axis_info) < 2:
                continue
            first, second = crs.axis_info[:2]
            f0, f1 = float(first.unit_conversion_factor), float(second.unit_conversion_factor)
            # A single x/y conversion factor is safe only when both planar axes
            # share the same units; never assume axis ordering if they differ.
            if not np.isfinite(f0) or f0 <= 0 or not np.isclose(f0, f1, rtol=1e-10):
                continue
            if np.isclose(f0, 1.0, rtol=1e-10):
                return "m", 1.0
            if np.isclose(f0, 1000.0, rtol=1e-10):
                return "km", 1000.0
            return str(first.unit_name), f0
        except (TypeError, ValueError, AttributeError, CRSError):
            continue
    return None


def native_grid_spacing(field: xr.DataArray):
    """Read native x/y coordinate steps, without regridding or guessing units.

    Returns (grid_spacing, spatial_resolution_m). The latter is available only
    for 1D, nearly uniform *metric* x/y coordinates. Geographic degrees are
    reported as angular spacing, never mislabelled as metres or kilometres.
    """
    if field.ndim not in (2, 3):
        return None, None
    y_dim, x_dim = field.dims[-2:]
    axes = {}
    for name, dim in (("x", x_dim), ("y", y_dim)):
        if dim not in field.coords:
            return None, None
        coord = field.coords[dim]
        if coord.ndim != 1 or coord.dims != (dim,):
            return None, None
        try:
            values = np.asarray(coord.values, dtype=np.float64)
        except (ValueError, TypeError):
            return None, None
        if values.size < 2 or not np.isfinite(values).all():
            return None, None
        deltas = np.abs(np.diff(values))
        step = float(np.median(deltas))
        if not np.isfinite(step) or step <= 0:
            return None, None
        # Nonuniform coordinates cannot be assigned a single reliable step.
        if not np.allclose(deltas, step, rtol=1e-3, atol=1e-7):
            return None, None
        unit = str(coord.attrs.get("units", "")).strip()
        axes[name] = {"step": step, "units": unit or "unknown"}

    # Some CF datasets (e.g. Belgian RMI) omit units on x/y but carry a
    # projected CRS with explicit metre axes in spatial_ref. Use that verified
    # information, without changing the source coordinate attributes.
    derived = _projected_crs_linear_unit(field) if any(
        axes[name]["units"] == "unknown" for name in ("x", "y")
    ) else None
    if derived is not None:
        crs_unit, _factor = derived
        for name in ("x", "y"):
            if axes[name]["units"] == "unknown":
                axes[name]["units"] = crs_unit
                axes[name]["unit_source"] = "projected_crs"

    spacing = {"x": axes["x"], "y": axes["y"], "method": "1D_coordinate_differences"}

    metre_units = {"m", "meter", "meters", "metre", "metres"}
    km_units = {"km", "kilometer", "kilometers", "kilometre", "kilometres"}
    factors = {}
    for name in ("x", "y"):
        unit = axes[name]["units"].lower()
        if unit in metre_units:
            factors[name] = 1.0
        elif unit in km_units:
            factors[name] = 1000.0
        elif axes[name].get("unit_source") == "projected_crs" and derived is not None:
            factors[name] = derived[1]
        else:
            # Unknown or geographic units: do not claim metric resolution.
            return spacing, None

    resolution_m = {name: axes[name]["step"] * factors[name] for name in ("x", "y")}
    return spacing, resolution_m


class MLCastZarrSource(DeclaredDataAdapter):
    """Each registered dataset declares CAPABILITIES; all I/O stays here."""

    def validate(self, request):
        super().validate(request)
        native = self.capabilities().native_cadence_minutes
        if request.interval_minutes % native:
            raise ConstraintError(
                f"{request.source}: interval_minutes={request.interval_minutes} "
                f"must be a multiple of native cadence {native}"
            )

    def load(self, request):
        self.validate(request)
        data_dir = Path(loader_config(request)["data_dir"])
        store = locate_store(data_dir)
        try:
            ds = xr.open_zarr(store, consolidated=True)
        except Exception as exc:
            raise RuntimeError(f"Cannot open local MLCast Zarr {store}: {exc}") from exc
        variable = self.capabilities().channels.variables[0].id
        try:
            if variable not in ds:
                raise ConstraintError(
                    f"MLCast {request.source} expects {variable!r}; "
                    f"available variables: {list(ds.data_vars)}"
                )
            field3d = ds[variable]
            if "time" not in field3d.dims or len(field3d.dims) != 3:
                raise ConstraintError(
                    f"{variable!r} must have time plus two spatial dimensions: {field3d.dims}"
                )
            if not np.issubdtype(ds.time.dtype, np.datetime64):
                raise ConstraintError("Zarr time coordinate must decode to datetime64")

            wanted = [
                _utc(request.initial_time) - dt.timedelta(minutes=request.interval_minutes * i)
                for i in range(request.history_steps - 1, -1, -1)
            ]
            available = set(np.asarray(ds.time.values).astype("datetime64[ns]"))
            missing = [
                t for t in wanted
                if np.datetime64(t.replace(tzinfo=None), "ns") not in available
            ]
            if missing:
                minimum, maximum = np.min(ds.time.values), np.max(ds.time.values)
                raise FileNotFoundError(
                    f"Missing requested historical frames (UTC): {[_key(t) for t in missing]}. "
                    f"Local sample time range: [{minimum}, {maximum}]. "
                    "Choose an existing time in your YAML or download a longer sample."
                )

            # The coordinates are time-invariant: calculate spatial selection just once.
            selection = spatial_selection(field3d.isel(time=0, drop=True), ds, request.bbox)
            grid_spacing, spatial_resolution_m = native_grid_spacing(field3d)
            data, source_files, frame_stats = {}, {}, []
            shape = None
            for t in wanted:
                k = _key(t)
                dt64 = np.datetime64(t.replace(tzinfo=None), "ns")
                frame = field3d.sel(time=dt64, drop=True)
                cropped = _crop_selected(frame, selection, request.bbox)
                # Materialize the requested small patch so it remains usable after ds.close().
                cropped = cropped.load()
                vals = np.asarray(cropped.values)
                finite = np.isfinite(vals)
                n = int(finite.sum())
                if n == 0:
                    raise ConstraintError(
                        f"{variable} frame at {k} contains no finite pixels within bbox; "
                        "choose another time/region."
                    )
                cropped.attrs.update({"source": request.source, "data_time": k})
                shape = tuple(int(n) for n in cropped.shape)
                frame_stats.append({
                    "time": k, "shape": shape,
                    "valid_fraction": float(finite.mean()),
                    "min": float(np.min(vals[finite])),
                    "max": float(np.max(vals[finite])),
                })
                data[k] = {variable: cropped}
                source_files[k] = {variable: [str(store)]}
            return bundle_from_parts(
                self.capabilities().id, request, data, source_files,
                {
                    "loader_module": "adapters.mlcast_common",
                    "version": self.capabilities().version,
                    "n_times": len(data),
                    "n_channel_fields": sum(len(row) for row in data.values()),
                    "dataset_id": request.source[len("mlcast_"):],
                    "data_variable": variable,
                    "units": field3d.attrs.get("units", "unknown"),
                    "grid_mapping": field3d.attrs.get("grid_mapping"),
                    "local_store": str(store),
                    "native_cadence_minutes": self.capabilities().native_cadence_minutes,
                    "requested_bbox": list(request.bbox),
                    "sample_bbox": list(selection[-1]),
                    "frame_shape": shape,
                    "grid_spacing": grid_spacing,
                    "spatial_resolution_m": spatial_resolution_m,
                    "frame_stats": frame_stats,
                    "model_ready": False,
                },
            )
        finally:
            ds.close()
