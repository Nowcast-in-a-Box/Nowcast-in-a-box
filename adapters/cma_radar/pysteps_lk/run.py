"""Container entrypoint for the deterministic CMA pySTEPS LK baseline."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import numpy as np

from adapters.cma_radar.pysteps_lk.adapter import ADAPTER
from interfaces.fields import Forecast
from interfaces.model import PreparedInput
from utils.model_contract import utc
from utils.stored_fields import StoredFields, add_forecast, save_manifest, sha256


def _check_algorithm_manifest(path: str) -> str:
    manifest = Path(path)
    if not manifest.is_file():
        raise ValueError("embedded pySTEPS algorithm manifest is missing")
    digest = sha256(manifest)
    expected = ADAPTER.weights().sha256
    if digest != expected:
        raise ValueError("embedded pySTEPS algorithm manifest checksum mismatch")
    try:
        contents = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("embedded pySTEPS algorithm manifest is not JSON") from exc
    if contents.get("schema") != "nib.pysteps-lk.v1":
        raise ValueError("unsupported pySTEPS algorithm manifest")
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--algorithm-manifest", required=True)
    args = parser.parse_args()

    algorithm_digest = _check_algorithm_manifest(args.algorithm_manifest)
    access = StoredFields(args.run)
    context = access.cube(access.manifest["observations"]["context"])
    descriptor = access.manifest["observations"]["context"]
    values = np.ascontiguousarray(context["values"], dtype=np.float32)
    if descriptor["variable"] != "CREF" or descriptor["unit"] != "dBZ":
        raise ValueError("pySTEPS LK requires CMA CREF values in dBZ")
    if context["mask"].any():
        raise ValueError("pySTEPS LK cannot extrapolate missing-coverage pixels")
    if values.ndim != 3 or values.shape[0] < 3:
        raise ValueError("pySTEPS LK requires at least three context frames")

    try:
        prepared = PreparedInput(
            descriptor["variable"],
            descriptor["unit"],
            "float32",
            tuple(values.shape),
            values,
            {"times": descriptor["times"]},
        )
        forecast = ADAPTER.predict(prepared, weights_path=args.algorithm_manifest)
        output = np.asarray(forecast.values, dtype=np.float32)
        if output.shape != (1, 20, *values.shape[-2:]) or not np.isfinite(output).all():
            raise ValueError("pySTEPS LK produced an invalid forecast cube")
        init_time = utc(descriptor["times"][-1])
        leads = tuple(forecast.coords["lead_minutes"])
        valid_times = tuple(
            (init_time + timedelta(minutes=lead)).isoformat().replace("+00:00", "Z")
            for lead in leads
        )
        add_forecast(
            args.run,
            ADAPTER.capabilities().id,
            ADAPTER.capabilities().version,
            algorithm_digest,
            Forecast(
                "CREF",
                "dBZ",
                output[0],
                valid_times,
                {**descriptor["grid"], "x": context["x"], "y": context["y"]},
                ~np.isfinite(output[0]),
                descriptor["times"][-1],
                leads,
            ),
        )
    except Exception as exc:
        access.manifest.update(status="failed", error=str(exc))
        save_manifest(access.root, access.manifest)
        raise
    print(ADAPTER.capabilities().id)


if __name__ == "__main__":
    main()
