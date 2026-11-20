"""Container entry point for dynamic-grid OPERA+DPC VMI pySTEPS LK."""

from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import numpy as np

from interfaces.fields import Forecast
from utils.model_contract import utc, validate_field
from utils.stored_fields import StoredFields, add_forecast, save_manifest

from .model import MODEL_ID, MODEL_VERSION, predict

_LEADS = tuple(range(5, 101, 5))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--algorithm-manifest", required=True)
    args = parser.parse_args()
    algorithm = Path(args.algorithm_manifest)
    if not algorithm.is_file():
        raise ValueError("embedded pySTEPS algorithm manifest is missing")
    metadata = json.loads(algorithm.read_text(encoding="utf-8"))
    if metadata.get("schema") != "nib.pysteps-lk.v1":
        raise ValueError("unsupported pySTEPS algorithm manifest")
    digest = hashlib.sha256(algorithm.read_bytes()).hexdigest()
    access = StoredFields(args.run)
    descriptor = access.manifest["observations"]["context"]
    context = access.cube(descriptor)
    values = np.ascontiguousarray(context["values"], dtype=np.float32)
    if descriptor["variable"] != "vmi" or descriptor["unit"] != "dBZ" or context["mask"].any():
        raise ValueError("pySTEPS LK requires complete OPERA+DPC VMI/dBZ context")
    if values.shape[0] != 3:
        raise ValueError("pySTEPS LK requires exactly three five-minute VMI frames")
    try:
        result = predict(values, lead_steps=len(_LEADS), flow_frames=3)
        initial = descriptor["times"][-1]
        grid = {**descriptor["grid"], "x": context["x"], "y": context["y"]}
        forecast = Forecast(
            "vmi",
            "dBZ",
            result,
            tuple(
                (utc(initial) + timedelta(minutes=lead)).isoformat().replace("+00:00", "Z")
                for lead in _LEADS
            ),
            grid,
            ~np.isfinite(result),
            initial,
            _LEADS,
        )
        validate_field(forecast)
        add_forecast(args.run, MODEL_ID, MODEL_VERSION, digest, forecast)
    except Exception as exc:
        access.manifest.update(status="failed", error=str(exc), failure_stage="pysteps_lk")
        save_manifest(access.root, access.manifest)
        raise
    print(MODEL_ID)


if __name__ == "__main__":
    main()
