"""Replay provider — reads fields from files on disk.

Second mock.  When a real sample file exists (a HDF5 cube, a NetCDF frame, a
saved ``.npy``), this provider lets the rest of the stack be exercised against
it without waiting for the platform.  Layout is intentionally simple and is not
meant to become a new standard:

    <root>/
      manifest.json          {"cadence_minutes": 10, "unit": "dBZ",
                              "variable": "reflectivity",
                              "observations": {"radar-composite": "obs"},
                              "models": {"mlcast-det": "pred"}}
      obs/20260101T0000.npy  one 2-D float array per cadence step
      obs/coords.npz         lon=..., lat=...
      pred/mlcast-det/20260101T0000.npz
                             keys: lon, lat, leads (int minutes), f_<lead>
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .base import Capabilities, Coords, Field, FieldUnavailable

UTC = timezone.utc
STAMP = "%Y%m%dT%H%M"


class ReplayAccess:
    """Serves fields from a directory of ``.npy`` / ``.npz`` files."""

    def __init__(self, root: str | Path, *, t_start: datetime | None = None) -> None:
        self.root = Path(root)
        meta_path = self.root / "manifest.json"
        if not meta_path.is_file():
            raise FileNotFoundError(f"missing replay manifest: {meta_path}")
        # utf-8-sig tolerates a BOM, which Windows editors like to add
        self.meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        self.cadence_minutes = int(self.meta.get("cadence_minutes", 10))
        self.unit = str(self.meta.get("unit", ""))
        self.variable = str(self.meta.get("variable", ""))
        self.observations: dict[str, str] = dict(self.meta.get("observations", {}))
        self.models: dict[str, str] = dict(self.meta.get("models", {}))
        self.retrospective = bool(self.meta.get("retrospective", True))
        self.t_start = t_start or _first_time(self.root)

    # -- capabilities ---------------------------------------------------
    def capabilities(self) -> Capabilities:
        units = {n: self.unit for n in self.observations}
        units.update({m: self.unit for m in self.models})
        return Capabilities(
            cadence_minutes=self.cadence_minutes,
            observation_names=tuple(self.observations),
            model_ids=tuple(self.models),
            retrospective=self.retrospective,
            units=units,
        )

    # -- observations ---------------------------------------------------
    def list_observations(self, name, t0, t1) -> list[datetime]:
        sub = self._obs_dir(name)
        if not sub.is_dir():
            raise FieldUnavailable(f"unknown observation {name!r}")
        out = []
        for t in _walk_times(sub):
            if t0 <= t <= t1:
                out.append(t)
        return sorted(out)

    def get_observation(self, name, valid_time) -> Field:
        path = self._obs_dir(name) / f"{valid_time.strftime(STAMP)}.npy"
        if not path.is_file():
            raise FieldUnavailable(f"no observation file for {valid_time.isoformat()}")
        data = np.load(path)
        coords = self._coords(self._obs_dir(name))
        return Field(data=data, coords=coords, valid_time=valid_time,
                     unit=self.unit, variable=self.variable, name=name)

    # -- predictions ----------------------------------------------------
    def list_predictions(self, model_id, init_time) -> list[int]:
        path = self._pred_file(model_id, init_time)
        if not path.is_file():
            raise FieldUnavailable(f"no prediction file for {init_time.isoformat()}")
        with np.load(path) as z:
            return [int(x) for x in z["leads"]]

    def get_prediction(self, model_id, init_time, lead_minutes) -> Field:
        path = self._pred_file(model_id, init_time)
        if not path.is_file():
            raise FieldUnavailable(f"no prediction file for {init_time.isoformat()}")
        with np.load(path) as z:
            key = f"f_{int(lead_minutes)}"
            if key not in z:
                raise FieldUnavailable(f"lead {lead_minutes} not in {path.name}")
            data = z[key]
            coords = Coords(lon=z["lon"], lat=z["lat"])
        valid_time = init_time + timedelta(minutes=int(lead_minutes))
        return Field(data=data, coords=coords, valid_time=valid_time,
                     unit=self.unit, variable=self.variable, name=model_id)

    # -- internals ------------------------------------------------------
    def _obs_dir(self, name: str) -> Path:
        if name not in self.observations:
            raise FieldUnavailable(f"unknown observation {name!r}")
        return self.root / self.observations[name]

    def _pred_file(self, model_id: str, init_time: datetime) -> Path:
        if model_id not in self.models:
            raise FieldUnavailable(f"unknown model {model_id!r}")
        return self.root / self.models[model_id] / f"{init_time.strftime(STAMP)}.npz"

    def _coords(self, directory: Path) -> Coords:
        cpath = directory / "coords.npz"
        if not cpath.is_file():
            raise FileUnavailable(f"missing coords file: {cpath}")
        with np.load(cpath) as z:
            return Coords(lon=z["lon"], lat=z["lat"])


class FileUnavailable(FieldUnavailable):
    """A replay file the manifest promised is not there."""


def _walk_times(directory: Path):
    for p in directory.glob("*.npy"):
        try:
            yield datetime.strptime(p.stem, STAMP).replace(tzinfo=UTC)
        except ValueError:
            continue


def _first_time(root: Path) -> datetime:
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        times = list(_walk_times(sub))
        if times:
            return min(times)
    return datetime(2026, 1, 1, tzinfo=UTC)
