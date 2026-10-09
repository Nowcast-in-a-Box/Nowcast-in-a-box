"""Visualization-local input contract: field, validation, single-frame access.

VIS-002 scope (see doc_log/plans/visualization-mvp-tasks/2026-09-21-vis-002-field-contract.md
and the shared contract in doc_log/plans/2026-09-20-nib-visualization-mvp-implementation.md
§3.1): deterministic gridded forecast fields normalized to a strictly
``(lead_time, y, x)`` DataArray with explicit CRS, variable name/units and
complete time semantics.

Source loading, dimension transposition, sentinel decoding and file-level grid
mapping happen in an adapter *before* this contract; this module never guesses
a missing CRS or units, never re-sorts times and never mutates caller input.
Longitude-seam handling, 0-360 display normalization, cell edges, reprojection
and regridding stay with the renderer/adapter and are out of scope here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import xarray as xr
from pyproj import CRS

INVALID_SHAPE = "INVALID_SHAPE"
INVALID_GRID = "INVALID_GRID"
INVALID_CRS = "INVALID_CRS"
MISSING_UNITS = "MISSING_UNITS"
INVALID_TIME = "INVALID_TIME"
INVALID_INTERVAL = "INVALID_INTERVAL"
EMPTY_FRAME = "EMPTY_FRAME"

FIELD_DIMS = ("lead_time", "y", "x")
TEMPORAL_STATISTICS = ("instantaneous", "accumulation", "mean")

#: relative tolerance for the regular-spacing check on x/y cell centres
_REGULAR_RTOL = 1e-6


class FieldValidationError(Exception):
    """Input contract violation; ``code`` carries the machine-readable reason."""

    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(f"{code}: {field}: {message}")
        self.code = code
        self.field = field
        self.message = message


@dataclass(frozen=True)
class VisualizationField:
    """Deterministic gridded forecast field in Visualization-local form.

    ``values`` is the single source of truth for the numeric data, the
    ``lead_time/y/x`` coordinates, the variable name (``values.name``) and the
    units (``values.attrs["units"]``); name and units are exposed read-only via
    :attr:`name`/:attr:`units` instead of being duplicated here.

    ``crs`` may be anything ``pyproj.CRS.from_user_input`` accepts (e.g. an
    EPSG string, WKT or a ``pyproj.CRS``); :func:`validate_field` returns a
    field whose ``crs`` is a resolved ``pyproj.CRS`` and whose ``valid_time``
    is always populated. ``valid_time``/``interval_start``/``interval_end``
    are 1D ``datetime64`` arrays along the lead axis (intervals are absolute
    times so that ``interval_end`` can equal ``valid_time`` per lead).
    """

    values: xr.DataArray
    crs: Any
    init_time: np.datetime64
    temporal_statistic: str
    valid_time: np.ndarray | None = None
    interval_start: np.ndarray | None = None
    interval_end: np.ndarray | None = None
    provenance: Mapping[str, Any] | None = None

    @property
    def name(self) -> str | None:
        """Variable name, read from ``values`` (no duplicated copy)."""
        return self.values.name

    @property
    def units(self) -> str | None:
        """Units, read from ``values.attrs`` (no duplicated copy)."""
        return self.values.attrs.get("units")


def _validate_spatial_axis(values: xr.DataArray, axis: str) -> None:
    if axis not in values.coords:
        raise FieldValidationError(
            INVALID_GRID, axis, f"missing aligned 1D '{axis}' cell-centre coordinate"
        )
    coord = values.coords[axis]
    if coord.dims != (axis,):
        raise FieldValidationError(
            INVALID_GRID,
            axis,
            f"'{axis}' coordinate must be 1D and aligned to dim '{axis}', got dims {coord.dims}",
        )
    centres = coord.values
    if centres.dtype.kind not in "iuf":
        raise FieldValidationError(
            INVALID_GRID, axis, f"'{axis}' coordinate must be numeric, got dtype {centres.dtype}"
        )
    if not np.isfinite(centres).all():
        raise FieldValidationError(
            INVALID_GRID, axis, f"'{axis}' coordinate contains non-finite values: {centres}"
        )
    diffs = np.diff(centres)
    if not (np.all(diffs > 0) or np.all(diffs < 0)):
        raise FieldValidationError(
            INVALID_GRID,
            axis,
            f"'{axis}' coordinate must be strictly monotonic (ascending or descending) "
            f"without duplicates: {centres}",
        )
    # Stored coordinates carry the quantization error of their own dtype
    # (~eps * |centre|), so the achievable diff-to-diff consistency scales with
    # coordinate magnitude and dtype precision, not only with the step size:
    # representative float32 decimal-degree grids stay acceptable while clearly
    # irregular grids of any dtype are still rejected. Integer centres are exact.
    coord_eps = np.finfo(centres.dtype).eps if centres.dtype.kind == "f" else 0.0
    quantization_slack = coord_eps * float(np.max(np.abs(centres)))
    if not np.allclose(diffs, diffs[0], rtol=_REGULAR_RTOL, atol=quantization_slack):
        raise FieldValidationError(
            INVALID_GRID, axis, f"'{axis}' coordinate spacing is not regular: {centres}"
        )


def _validate_crs(crs: Any) -> CRS:
    if crs is None or (isinstance(crs, str) and not crs.strip()):
        raise FieldValidationError(
            INVALID_CRS,
            "crs",
            "CRS must be provided explicitly (EPSG string, WKT or pyproj CRS); "
            "a missing CRS is never guessed",
        )
    try:
        return CRS.from_user_input(crs)
    except Exception as err:  # pyproj raises several exception types
        raise FieldValidationError(
            INVALID_CRS, "crs", f"CRS not parseable by pyproj: {crs!r} ({err})"
        ) from err


def _validate_units(values: xr.DataArray) -> None:
    units = values.attrs.get("units")
    if not isinstance(units, str) or not units.strip():
        raise FieldValidationError(
            MISSING_UNITS,
            "units",
            f"non-empty units required in values.attrs['units'], got {units!r}; "
            "units are never inferred or converted",
        )


def _validate_init_time(init_time: Any) -> np.datetime64:
    if not isinstance(init_time, np.datetime64) or np.isnat(init_time):
        raise FieldValidationError(
            INVALID_TIME,
            "init_time",
            f"init_time must be a valid scalar numpy datetime64, got {init_time!r}",
        )
    return init_time


def _validate_lead_time(values: xr.DataArray) -> np.ndarray:
    coord = values.coords.get("lead_time")
    if coord is None:
        raise FieldValidationError(
            INVALID_TIME, "lead_time", "missing aligned 1D 'lead_time' coordinate"
        )
    if coord.dims != ("lead_time",):
        raise FieldValidationError(
            INVALID_TIME,
            "lead_time",
            f"'lead_time' coordinate must be 1D aligned to dim 'lead_time', "
            f"got dims {coord.dims}",
        )
    leads = coord.values
    if not np.issubdtype(leads.dtype, np.timedelta64):
        raise FieldValidationError(
            INVALID_TIME, "lead_time", f"lead_time must be timedelta64, got dtype {leads.dtype}"
        )
    if np.any(np.isnat(leads)):
        raise FieldValidationError(INVALID_TIME, "lead_time", "lead_time contains NaT")
    if np.any(leads < np.zeros_like(leads)):
        raise FieldValidationError(
            INVALID_TIME, "lead_time", "lead_time must be non-negative; input is never re-sorted"
        )
    if not np.all(leads[1:] > leads[:-1]):
        raise FieldValidationError(
            INVALID_TIME,
            "lead_time",
            "lead_time must be strictly increasing without duplicates; unordered input is "
            "rejected, never silently sorted",
        )
    return leads


def _resolve_valid_time(
    provided: np.ndarray | None, init_time: np.datetime64, leads: np.ndarray
) -> np.ndarray:
    derived = init_time + leads
    if provided is None:
        return derived
    arr = np.asarray(provided)
    if arr.ndim != 1 or not np.issubdtype(arr.dtype, np.datetime64):
        raise FieldValidationError(
            INVALID_TIME,
            "valid_time",
            f"valid_time must be a 1D datetime64 array along lead_time, "
            f"got shape {arr.shape} dtype {arr.dtype}",
        )
    if arr.shape[0] != leads.shape[0]:
        raise FieldValidationError(
            INVALID_TIME,
            "valid_time",
            f"valid_time has {arr.shape[0]} entries but lead_time has {leads.shape[0]}",
        )
    if not np.array_equal(arr, derived):
        raise FieldValidationError(
            INVALID_TIME,
            "valid_time",
            "valid_time contradicts init_time + lead_time; values are never adjusted",
        )
    return arr


def _validate_intervals(
    statistic: str,
    interval_start: np.ndarray | None,
    interval_end: np.ndarray | None,
    valid_time: np.ndarray,
    n_leads: int,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    if statistic not in TEMPORAL_STATISTICS:
        raise FieldValidationError(
            INVALID_INTERVAL,
            "temporal_statistic",
            f"temporal_statistic must be one of {TEMPORAL_STATISTICS}, got {statistic!r}",
        )
    if statistic == "instantaneous":
        return interval_start, interval_end
    if interval_start is None:
        raise FieldValidationError(
            INVALID_INTERVAL,
            "interval_start",
            f"temporal_statistic '{statistic}' requires interval_start along lead_time",
        )
    if interval_end is None:
        raise FieldValidationError(
            INVALID_INTERVAL,
            "interval_end",
            f"temporal_statistic '{statistic}' requires interval_end along lead_time",
        )
    start = np.asarray(interval_start)
    end = np.asarray(interval_end)
    for arr, label in ((start, "interval_start"), (end, "interval_end")):
        if arr.ndim != 1 or not np.issubdtype(arr.dtype, np.datetime64):
            raise FieldValidationError(
                INVALID_INTERVAL,
                label,
                f"{label} must be a 1D datetime64 array along lead_time, "
                f"got shape {arr.shape} dtype {arr.dtype}",
            )
        if arr.shape[0] != n_leads:
            raise FieldValidationError(
                INVALID_INTERVAL,
                label,
                f"{label} has {arr.shape[0]} entries but lead_time has {n_leads}",
            )
    if not np.all(start < end):
        raise FieldValidationError(
            INVALID_INTERVAL,
            "interval_start",
            "interval_start must be strictly before interval_end for every lead",
        )
    if not np.array_equal(end, valid_time):
        raise FieldValidationError(
            INVALID_INTERVAL,
            "interval_end",
            "interval_end must equal valid_time for every lead",
        )
    return interval_start, interval_end


def _reject_all_nan_frames(values: xr.DataArray, leads: np.ndarray) -> None:
    fully_missing = np.isnan(values.values).all(axis=(1, 2))
    hit = int(np.flatnonzero(fully_missing)[0]) if fully_missing.any() else -1
    if hit >= 0:
        raise FieldValidationError(
            EMPTY_FRAME,
            f"values[lead_index={hit}]",
            f"frame at lead_index={hit} (lead_time={leads[hit]}) is entirely NaN; "
            "all-missing frames are rejected, not dropped or zero-filled",
        )


def validate_field(field: VisualizationField) -> VisualizationField:
    """Validate ``field`` against the Visualization-local contract.

    Returns a new normalized :class:`VisualizationField`: the same ``values``
    object (never modified, name/units stay owned by the DataArray), a
    resolved ``pyproj.CRS``, and ``valid_time`` derived as
    ``init_time + lead_time`` when absent. Raises :class:`FieldValidationError`
    with the shared code set on the first violation found; nothing is guessed,
    re-sorted, converted or filled.
    """
    values = field.values
    if not isinstance(values, xr.DataArray):
        raise FieldValidationError(
            INVALID_SHAPE,
            "values",
            f"values must be an xarray.DataArray, got {type(values).__name__}",
        )
    if values.dtype.kind not in "iuf":
        raise FieldValidationError(
            INVALID_SHAPE,
            "values",
            f"values dtype must be numeric (integer or float), got {values.dtype}",
        )
    if values.dims != FIELD_DIMS:
        raise FieldValidationError(
            INVALID_SHAPE,
            "values",
            f"values dims must be {FIELD_DIMS} (exact names and order), "
            f"got {tuple(values.dims)}",
        )
    if values.sizes["y"] < 2 or values.sizes["x"] < 2:
        raise FieldValidationError(
            INVALID_SHAPE,
            "values",
            "each spatial axis needs at least 2 cell centres "
            f"(got y={values.sizes['y']}, x={values.sizes['x']})",
        )
    name = values.name
    if not isinstance(name, str) or not name.strip():
        raise FieldValidationError(
            INVALID_SHAPE,
            "name",
            f"DataArray name must be a non-empty string, got {name!r}",
        )

    for axis in ("y", "x"):
        _validate_spatial_axis(values, axis)
    crs = _validate_crs(field.crs)
    _validate_units(values)
    init_time = _validate_init_time(field.init_time)
    leads = _validate_lead_time(values)
    valid_time = _resolve_valid_time(field.valid_time, init_time, leads)
    interval_start, interval_end = _validate_intervals(
        field.temporal_statistic, field.interval_start, field.interval_end, valid_time,
        leads.shape[0],
    )
    _reject_all_nan_frames(values, leads)

    return VisualizationField(
        values=values,
        crs=crs,
        init_time=init_time,
        temporal_statistic=field.temporal_statistic,
        valid_time=valid_time,
        interval_start=interval_start,
        interval_end=interval_end,
        provenance=field.provenance,
    )


def frame_at(field: VisualizationField, lead_index: int) -> xr.DataArray:
    """Return the 2D ``(y, x)`` frame for ``lead_index`` as a view of the source.

    Validates first (so contract violations, including an all-NaN frame
    anywhere in the sequence, raise :class:`FieldValidationError`), then
    selects. The returned DataArray shares memory with ``field.values``, keeps
    the scalar ``lead_time``/``valid_time`` coordinates and the source attrs;
    the source field's 3D ``values`` and all coordinates stay untouched.
    ``lead_index`` outside ``[0, n_leads)`` raises ``IndexError``.
    """
    validated = validate_field(field)
    n_leads = validated.values.sizes["lead_time"]
    if not 0 <= lead_index < n_leads:
        raise IndexError(f"lead_index must be in [0, {n_leads}), got {lead_index!r}")
    frame = validated.values.isel(lead_time=lead_index)
    return frame.assign_coords(valid_time=validated.valid_time[lead_index])
