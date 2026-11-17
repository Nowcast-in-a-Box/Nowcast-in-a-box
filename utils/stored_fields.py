"""Versioned stored fields shared by container producers and diagnostics."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import numpy as np

ID = re.compile(r"^[A-Za-z0-9_-]+$")


class FieldUnavailable(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(root, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("artifact path must be relative and contained in its run")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("artifact path escapes the run")
    return path


def save_manifest(root, manifest):
    path = Path(root) / "manifest.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def write_cube(
    root,
    name,
    values,
    times,
    grid,
    *,
    variable,
    unit,
    init_time=None,
    leads=None,
    mask=None,
):
    from interfaces.fields import Observations
    from utils.model_contract import utc, validate_field

    path = relative_path(Path(root), name)
    values = np.asarray(values, dtype=np.float32)
    mask = ~np.isfinite(values) if mask is None else np.asarray(mask)
    field = Observations("", variable, unit, values, tuple(times), grid, mask)
    parsed = validate_field(field)
    if init_time is not None:
        initial = utc(init_time)
        if (
            leads is None
            or len(leads) != len(times)
            or any(
                type(lead) is not int
                or lead <= 0
                or (valid - initial).total_seconds() != lead * 60
                for valid, lead in zip(parsed, leads)
            )
        ):
            raise ValueError("forecast times must equal init_time plus positive leads")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        np.savez_compressed(stream, values=values, mask=mask, x=grid["x"], y=grid["y"])
    descriptor = {
        "path": name,
        "sha256": sha256(path),
        "shape": list(values.shape),
        "dtype": "float32",
        "variable": variable,
        "unit": unit,
        "times": list(times),
        "grid": {k: v for k, v in grid.items() if k not in ("x", "y")},
    }
    if init_time is not None:
        descriptor.update(init_time=init_time, lead_minutes=list(leads))
    return descriptor


def create_run(root, run_id, window, grid, *, atlas_revision):
    if not ID.fullmatch(run_id):
        raise ValueError("invalid run ID")
    directory = Path(root) / run_id
    directory.mkdir(parents=False, exist_ok=False)
    manifest = {
        "schema": "nib.run.v2",
        "id": run_id,
        "atlas_revision": atlas_revision,
        "status": "ingested",
        "data": {"id": window["data_id"], "version": "1.0.0"},
        "sample": {
            "data_id": window["data_id"],
            "cadence_minutes": window["cadence_minutes"],
            **{
                k: window[k]
                for k in (
                    "sample_id",
                    "event_id",
                    "row",
                    "context_start_time",
                    "init_time",
                )
            },
        },
        "observations": {},
        "forecasts": {},
        "diagnostics": [],
    }
    if "provenance" in window:
        manifest["sample"]["provenance"] = window["provenance"]
    manifest["data"]["cadence_minutes"] = window["cadence_minutes"]
    field_options = {
        "variable": window["variable"],
        "unit": window["unit"],
    }
    try:
        manifest["observations"]["context"] = write_cube(
            directory,
            "inputs/context.npz",
            window["context"],
            window["context_times"],
            grid,
            **field_options,
        )
        manifest["observations"]["target"] = write_cube(
            directory,
            "inputs/target.npz",
            window["target"],
            window["target_times"],
            grid,
            **field_options,
        )
    except Exception as exc:
        manifest.update(status="failed", error=str(exc))
        save_manifest(directory, manifest)
        raise
    save_manifest(directory, manifest)
    return directory


def add_forecast(directory, model_id, version, weight_sha256, forecast):
    from interfaces.fields import Forecast
    from utils.model_contract import utc, validate_field

    if not ID.fullmatch(model_id) or not re.fullmatch(r"[0-9a-f]{64}", weight_sha256):
        raise ValueError("invalid model identity or weight digest")
    if not isinstance(forecast, Forecast):
        raise TypeError("add_forecast requires a physical Forecast")
    validate_field(forecast)
    access = StoredFields(directory)
    if access.legacy:
        raise ValueError("legacy runs are read-only; prepare a new nib.run.v2 run")
    if model_id in access.manifest["forecasts"]:
        raise FileExistsError("model forecast already registered")
    if utc(forecast.init_time) != utc(access.manifest["sample"]["init_time"]):
        raise ValueError("forecast and sample init_time disagree")
    descriptor = write_cube(
        access.root,
        f"forecasts/{model_id}/fields.npz",
        forecast.values,
        forecast.times,
        forecast.grid,
        mask=forecast.mask,
        init_time=forecast.init_time,
        leads=forecast.lead_minutes,
        variable=forecast.variable_id,
        unit=forecast.units,
    )
    descriptor.update(
        model_id=model_id, model_version=version, weight_sha256=weight_sha256
    )
    access.manifest["forecasts"][model_id] = descriptor
    save_manifest(access.root, access.manifest)


class StoredFields:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.manifest = json.loads((self.root / "manifest.json").read_text())
        self.legacy = self.manifest.get("schema") == "nib.run.v1"
        if not self.legacy and self.manifest.get("schema") != "nib.run.v2":
            raise ValueError("unsupported run schema")
        if self.legacy:
            if self.manifest["data"]["id"] not in ("sevir", "cma_radar"):
                raise ValueError("unsupported stored dataset")
            self.cadence = 6 if self.manifest["data"]["id"] == "cma_radar" else 5
            if (
                self.manifest["data"].get("cadence_minutes", self.cadence)
                != self.cadence
            ):
                raise ValueError("stored cadence disagrees with dataset")
        else:
            self.cadence = self.manifest["data"]["cadence_minutes"]
            if type(self.cadence) is not int or self.cadence <= 0:
                raise ValueError("stored cadence must be positive integer minutes")
        self.cache = {}
        self._descriptors = {}
        self.file_reads = 0

    def observations(self, requirements):
        """Select a declared recent history without changing cadence, pixels or units."""
        from interfaces.fields import Observations
        from utils.model_contract import utc

        descriptor = self.manifest["observations"]["context"]
        cube = self.cube(descriptor)
        count = requirements.context_frames
        if cube["values"].shape[0] < count:
            raise ValueError("insufficient observation history for model declaration")
        if utc(descriptor["times"][-1]) != utc(self.manifest["sample"]["init_time"]):
            raise ValueError("context must end at sample initialization")
        return Observations(
            self.manifest["data"]["id"],
            descriptor["variable"],
            descriptor["unit"],
            cube["values"][-count:],
            tuple(descriptor["times"][-count:]),
            {**descriptor["grid"], "x": cube["x"], "y": cube["y"]},
            cube["mask"][-count:],
        )

    def cube(self, descriptor):
        path = relative_path(self.root, descriptor["path"])
        if path in self.cache and self._descriptors[path] != descriptor:
            raise ValueError("conflicting stored descriptor for cached artifact")
        if path not in self.cache:
            if sha256(path) != descriptor["sha256"]:
                raise ValueError("artifact checksum mismatch")
            with np.load(path, allow_pickle=False) as archive:
                cube = {key: archive[key] for key in ("values", "mask", "x", "y")}
            from interfaces.fields import Observations
            from utils.model_contract import utc, validate_field

            if (
                list(cube["values"].shape) != descriptor["shape"]
                or descriptor["dtype"] != "float32"
            ):
                raise ValueError("stored cube shape/dtype mismatch")
            field = Observations(
                self.manifest["data"]["id"],
                descriptor["variable"],
                descriptor["unit"],
                cube["values"],
                tuple(descriptor["times"]),
                {**descriptor["grid"], "x": cube["x"], "y": cube["y"]},
                cube["mask"],
            )
            times = validate_field(field)
            if self.legacy:
                expected = (
                    ("CREF", "dBZ") if field.source == "cma_radar" else ("vil", "raw")
                )
                if (field.variable_id, field.units) != expected:
                    raise ValueError("unsupported stored variable/unit/grid")
            if self.legacy or "init_time" not in descriptor:
                if any(
                    (b - a).total_seconds() % (60 * self.cadence) != 0
                    for a, b in zip(times, times[1:])
                ):
                    raise ValueError("stored fields require their declared UTC cadence")
            if "init_time" in descriptor:
                initial = utc(descriptor["init_time"])
                leads = descriptor["lead_minutes"]
                if len(leads) != len(times) or any(
                    type(lead) is not int
                    or lead <= 0
                    or (valid - initial).total_seconds() != lead * 60
                    for valid, lead in zip(times, leads)
                ):
                    raise ValueError("stored forecast init/lead alignment mismatch")
                if initial != utc(self.manifest["sample"]["init_time"]):
                    raise ValueError("forecast and sample init_time disagree")
            for array in cube.values():
                array.setflags(write=False)
            self.cache[path] = cube
            self._descriptors[path] = deepcopy(descriptor)
            self.file_reads += 1
        return self.cache[path]

    def capabilities(self):
        return {
            "cadence_minutes": self.cadence,
            "observation_names": [self.manifest["data"]["id"]],
            "model_ids": list(self.manifest["forecasts"]),
            "retrospective": True,
            "unit": self.manifest["observations"]["target"]["unit"],
        }

    def list_observations(self, name, t0, t1):
        if name != self.manifest["data"]["id"]:
            raise FieldUnavailable("unknown observation")
        times = [
            datetime.fromisoformat(t.replace("Z", "+00:00"))
            for spec in self.manifest["observations"].values()
            for t in spec["times"]
        ]
        return sorted(t for t in times if t0 <= t <= t1)

    def get_observation(self, name, valid_time):
        if name != self.manifest["data"]["id"]:
            raise FieldUnavailable("unknown observation")
        for descriptor in self.manifest["observations"].values():
            for index, time in enumerate(descriptor["times"]):
                if datetime.fromisoformat(time.replace("Z", "+00:00")) == valid_time:
                    return self.field(descriptor, index)
        raise FieldUnavailable("observation time unavailable")

    def forecast(self, model_id, init_time):
        descriptor = self.manifest["forecasts"].get(model_id)
        if (
            descriptor is None
            or datetime.fromisoformat(descriptor["init_time"].replace("Z", "+00:00"))
            != init_time
        ):
            raise FieldUnavailable("forecast unavailable for this run/init")
        return descriptor

    def list_predictions(self, model_id, init_time):
        return self.forecast(model_id, init_time)["lead_minutes"]

    def get_prediction(self, model_id, init_time, lead_minutes):
        descriptor = self.forecast(model_id, init_time)
        try:
            index = descriptor["lead_minutes"].index(lead_minutes)
        except ValueError as exc:
            raise FieldUnavailable("forecast lead unavailable") from exc
        return self.field(descriptor, index)

    def field(self, descriptor, index):
        cube = self.cube(descriptor)
        return {
            "data": cube["values"][index],
            "mask": cube["mask"][index],
            "x": cube["x"],
            "y": cube["y"],
            "grid": descriptor["grid"],
            "unit": descriptor["unit"],
            "variable": descriptor["variable"],
            "valid_time": datetime.fromisoformat(
                descriptor["times"][index].replace("Z", "+00:00")
            ),
        }
