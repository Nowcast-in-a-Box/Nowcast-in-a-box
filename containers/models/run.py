"""One model image consumes stored context and publishes a checked Forecast."""

import argparse
import importlib
import os
from pathlib import Path

import numpy as np

from interfaces.model import PreparedInput
from utils.stored_fields import StoredFields, add_forecast, sha256, save_manifest

MODELS = ("nowcastnet", "exprecast", "wadepre")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--weights", required=True)
    args = parser.parse_args()
    model_id = os.environ.get("NIB_MODEL")
    if model_id not in MODELS:
        raise ValueError("image must select one supported SEVIR algorithm")
    adapter = importlib.import_module(f"adapters.sevir.{model_id}.adapter").ADAPTER
    weights = Path(args.weights)
    spec = adapter.weights()
    if not weights.is_file() or sha256(weights) != spec.sha256:
        raise ValueError(
            "offline weight checksum mismatch; prepare pinned weights first"
        )
    access = StoredFields(args.run)
    context = access.cube(access.manifest["observations"]["context"])
    if context["values"].shape != (6, 128, 128) or context["mask"].any():
        raise ValueError("model requires six unmasked 128x128 context frames")
    # Each paired model declares five latest context frames; preserve its native ONNX call.
    values = np.ascontiguousarray(context["values"][-5:][None], dtype=np.float32)
    prepared = PreparedInput(
        "vil",
        "raw",
        "float32",
        tuple(values.shape),
        values,
        {"times": access.manifest["observations"]["context"]["times"][-5:]},
    )
    try:
        forecast = adapter.predict(prepared, weights_path=str(weights))
        if not np.isfinite(forecast.values).all():
            raise ValueError("model produced non-finite values")
        add_forecast(
            args.run,
            model_id,
            adapter.capabilities().version,
            spec.sha256,
            forecast.values[0],
            forecast.coords["lead_minutes"],
        )
    except Exception as exc:
        access.manifest.update(status="failed", error=str(exc))
        save_manifest(access.root, access.manifest)
        raise
    print(model_id)


if __name__ == "__main__":
    main()
