"""Four-method data handoff over one immutable, native-grid CMA run."""

from copy import deepcopy
from datetime import UTC, datetime
from itertools import pairwise
from numbers import Real
from zipfile import BadZipFile

import numpy as np
import xarray as xr
from nib_verify.config.schema import MetricGroup
from nib_verify.core.metrics import spec_for
from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError

from utils.stored_fields import FieldUnavailable, StoredFields


class RunSourceError(ValueError):
    """Invalid handoff configuration or an unavailable/corrupt stored field."""


def _time(value):
    try:
        if not isinstance(value, str):
            raise ValueError("expected an ISO 8601 string")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("include a timezone, for example Z or +08:00")
        return parsed.astimezone(UTC)
    except ValueError as exc:
        raise RunSourceError(f"Invalid time {value!r}: {exc}") from exc


def _stamp(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _metrics(groups, unit):
    if not isinstance(groups, list) or not groups:
        raise RunSourceError("Provide a non-empty list of metric groups")
    result, seen_levels, seen_items = [], {}, set()
    for raw in groups:
        if not isinstance(raw, dict) or set(raw) - {
            "family",
            "items",
            "levels",
            "half_windows",
            "unit",
        }:
            raise RunSourceError("Metric groups contain an invalid declaration")
        items = raw.get("items")
        if (
            not isinstance(items, list)
            or not items
            or any(not isinstance(item, str) or not item for item in items)
        ):
            raise RunSourceError("Each metric group needs non-empty string items")
        canonical = [spec_for(item).name for item in items]
        if seen_items.intersection(canonical) or len(set(canonical)) != len(canonical):
            raise RunSourceError("A metric must not be listed more than once")
        seen_items.update(canonical)
        levels, windows = raw.get("levels", []), raw.get("half_windows", [])
        if not isinstance(levels, list) or any(
            isinstance(v, bool) or not isinstance(v, Real) or not np.isfinite(v)
            for v in levels
        ):
            raise RunSourceError("Metric levels must be finite numbers")
        if not isinstance(windows, list) or any(
            type(v) is not int or v < 0 for v in windows
        ):
            raise RunSourceError("Metric half_windows must be non-negative integers")
        if len(set(levels)) != len(levels) or len(set(windows)) != len(windows):
            raise RunSourceError("Metric levels and half_windows must not repeat")
        family = raw.get("family")
        if not isinstance(family, str):
            raise RunSourceError("Metric family must be a string")
        previous = seen_levels.setdefault(family, set())
        if previous.intersection(levels):
            raise RunSourceError("Levels in groups of the same family must not overlap")
        previous.update(levels)
        MetricGroup(family, tuple(items), tuple(levels), tuple(windows))
        if "unit" in raw and raw["unit"] != unit:
            raise RunSourceError(
                "Metric unit differs from truth; convert explicitly first"
            )
        result.append(deepcopy(raw))
    return result


class RunSource:
    """Bind one run, selected stored models and an explicit metric configuration.

    Version is supplied by the platform, not inferred from the latest attempt.
    The caller must increment it when changing the semantics of the same group.
    The provider never writes the run or starts inference/diagnostics.
    """

    def __init__(self, directory, *, metrics, version, group_id=None, models=None):
        try:
            self._load(directory, metrics, version, group_id, models)
        except RunSourceError:
            raise
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            BadZipFile,
            EOFError,
            ProjError,
        ) as exc:
            raise RunSourceError(
                f"Cannot open diagnostic run {directory}: {exc}"
            ) from exc

    def _load(self, directory, metrics, version, group_id, models):
        self._fields = StoredFields(directory)
        manifest = self._fields.manifest
        if manifest["data"]["id"] != "cma_radar":
            raise RunSourceError("This handoff currently supports CMA Radar runs only")
        if type(version) is not int or version < 1:
            raise RunSourceError("Group version must be a positive integer")
        run_id = manifest["id"]
        group_id = group_id if group_id is not None else f"{run_id}-verification"
        if not isinstance(group_id, str) or not group_id.strip():
            raise RunSourceError("Group ID must be a non-empty string")
        forecasts = manifest["forecasts"]
        models = list(forecasts) if models is None else models
        if (
            not isinstance(models, list)
            or not models
            or any(
                not isinstance(model, str) or model not in forecasts for model in models
            )
        ):
            raise RunSourceError("Select models with saved forecasts in this run")
        if len(set(models)) != len(models):
            raise RunSourceError("Selected models must not repeat")

        target = manifest["observations"]["target"]
        cube = self._fields.cube(target)
        self._crs = CRS.from_user_input(target["grid"]["crs"])
        if not self._crs.is_projected and self._crs.to_epsg() != 4326:
            raise RunSourceError("Grid must be projected or EPSG:4326")
        self._x, self._y = cube["x"], cube["y"]
        dx, dy = np.diff(self._x), np.diff(self._y)
        if not np.allclose(dx, dx[0]) or not np.allclose(dy, dy[0]):
            raise RunSourceError("The handoff requires regular native grid axes")
        axis_units = target["grid"].get("axis_units")
        expected_unit = (
            "degrees"
            if self._crs.is_geographic
            else {"metre": "m"}.get(
                self._crs.axis_info[0].unit_name, self._crs.axis_info[0].unit_name
            )
        )
        if axis_units != expected_unit:
            raise RunSourceError("Stored axis units disagree with the declared CRS")
        self._dims = ("lat", "lon") if self._crs.is_geographic else ("y", "x")
        y_name, x_name = self._dims
        self._coords = {
            x_name: xr.Variable(x_name, self._x.copy(), attrs={"units": axis_units}),
            y_name: xr.Variable(y_name, self._y.copy(), attrs={"units": axis_units}),
            "spatial_ref": xr.Variable(
                (), 0, attrs={**self._crs.to_cf(), "crs_wkt": self._crs.to_wkt()}
            ),
        }
        if self._crs.is_projected:
            lon, lat = Transformer.from_crs(
                self._crs, "EPSG:4326", always_xy=True
            ).transform(*np.meshgrid(self._x, self._y))
            if not np.isfinite(lon).all() or not np.isfinite(lat).all():
                raise RunSourceError("Grid has non-finite geographic coordinates")
            self._coords.update(
                lon=xr.Variable(self._dims, lon), lat=xr.Variable(self._dims, lat)
            )
            self._coords["x"].attrs["standard_name"] = "projection_x_coordinate"
            self._coords["y"].attrs["standard_name"] = "projection_y_coordinate"
        self._coords["lon"].attrs.update(
            standard_name="longitude", units="degrees_east"
        )
        self._coords["lat"].attrs.update(
            standard_name="latitude", units="degrees_north"
        )

        self._truth = f"{run_id}/truth/{manifest['data']['id']}"
        self._models = {f"{run_id}/forecast/{model}": model for model in models}
        initial = _time(manifest["sample"]["init_time"])
        declarations = []
        for name, model in self._models.items():
            spec = forecasts[model]
            leads = spec["lead_minutes"]
            if (
                not leads
                or any(type(v) is not int or v <= 0 for v in leads)
                or any(b <= a for a, b in pairwise(leads))
            ):
                raise RunSourceError(
                    "Stored forecast leads must be increasing positive integers"
                )
            if _time(spec["init_time"]) != initial:
                raise RunSourceError("Forecast initialization differs from the run")
            declarations.append(
                {
                    "name": name,
                    "model_id": model,
                    "display_name": manifest.get("model_names", {}).get(model, model),
                    "variable": spec["variable"],
                    "unit": spec["unit"],
                    "leads_minutes": list(leads),
                }
            )
        for spec in [
            *manifest["observations"].values(),
            *(forecasts[m] for m in models),
        ]:
            saved = self._fields.cube(spec)
            if (
                not CRS.from_user_input(spec["grid"]["crs"]).equals(self._crs)
                or spec["grid"].get("axis_units") != axis_units
                or not np.array_equal(saved["x"], self._x)
                or not np.array_equal(saved["y"], self._y)
                or spec["variable"] != target["variable"]
                or spec["unit"] != target["unit"]
            ):
                raise RunSourceError(
                    "Truth and selected forecasts must share grid, variable and unit"
                )
        space = {
            "projection": self._crs.to_string(),
            "coordinate_units": axis_units,
            "resolution": [float(dx[0]), float(dy[0])],
        }
        extent = [
            float(self._x[0]),
            float(self._y[0]),
            float(self._x[-1]),
            float(self._y[-1]),
        ]
        space["bbox" if self._crs.is_geographic else "extent"] = extent
        self._group = {
            "group_id": group_id,
            "name": manifest["sample"]["sample_id"],
            "version": version,
            "status": "active",
            "truth": {
                "name": self._truth,
                "variable": target["variable"],
                "unit": target["unit"],
                "cadence_minutes": self._fields.cadence,
            },
            "space": space,
            "time": {
                "timezone": "UTC",
                "init_from": _stamp(initial),
                "init_to": _stamp(initial),
            },
            "forecasts": declarations,
            "metrics": _metrics(metrics, target["unit"]),
        }

    @property
    def file_reads(self):
        """Verified cube loads, for demonstrating the once-per-cube guarantee."""
        return self._fields.file_reads

    def list_groups(self):
        return [self._group["group_id"]]

    def get_group(self, group_id):
        if group_id != self._group["group_id"]:
            raise RunSourceError(
                f"Unknown verification group {group_id!r}; use list_groups()"
            )
        return deepcopy(self._group)

    def read_truth(self, name, time):
        if name != self._truth:
            raise RunSourceError(
                f"Unknown truth {name!r}; use get_group()['truth']['name']"
            )
        try:
            return self._array(
                self._fields.get_observation(
                    self._fields.manifest["data"]["id"], _time(time)
                ),
                name,
            )
        except FieldUnavailable as exc:
            raise RunSourceError(f"Truth {name!r} has no field at {time!r}") from exc

    def read_forecast(self, name, init_time, lead_minutes):
        if not isinstance(name, str) or name not in self._models:
            raise RunSourceError(
                f"Unknown forecast {name!r}; use get_group()['forecasts']"
            )
        if type(lead_minutes) is not int:
            raise RunSourceError("Forecast lead_minutes must be integer minutes")
        try:
            return self._array(
                self._fields.get_prediction(
                    self._models[name], _time(init_time), lead_minutes
                ),
                name,
            )
        except FieldUnavailable as exc:
            raise RunSourceError(
                f"Forecast {name!r} has no initialization {init_time!r} / lead {lead_minutes} min"
            ) from exc

    def _array(self, field, name):
        values = np.where(field["mask"], np.nan, field["data"])
        if not np.isfinite(values).any():
            raise RunSourceError(
                f"Field {name!r} at {_stamp(field['valid_time'])} is entirely missing"
            )
        return xr.DataArray(
            values,
            dims=self._dims,
            coords=deepcopy(self._coords),
            name=field["variable"],
            attrs={
                "units": field["unit"],
                "variable": field["variable"],
                "valid_time": _stamp(field["valid_time"]),
                "crs": self._crs.to_string(),
                "grid_mapping": "spatial_ref",
                "source_name": name,
            },
        )
