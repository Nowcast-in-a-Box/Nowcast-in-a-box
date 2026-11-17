"""Run one declared model against one stored observation bundle, offline."""

import argparse
import importlib
import json
import os
from pathlib import Path

from utils.model_contract import validate_forecast
from utils.stored_fields import StoredFields, add_forecast, save_manifest, sha256


def run(directory, model_id, module, weights_path):
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text())
    stage = "declaration"
    try:
        if not model_id or not module:
            raise ValueError(
                "model image must declare NIB_MODEL and NIB_ADAPTER_MODULE"
            )
        adapter = importlib.import_module(module).ADAPTER
        capabilities = adapter.capabilities()
        if model_id != capabilities.id:
            raise ValueError("model image identity disagrees with declaration")
        requirements = adapter.input_requirements()
        output = adapter.output_specification()
        spec = adapter.weights()
        stage = "weights"
        weights = Path(weights_path)
        if not weights.is_file() or sha256(weights) != spec.sha256:
            raise ValueError(
                "offline weight checksum mismatch; prepare pinned weights first"
            )
        stage = "input"
        access = StoredFields(root)
        observations = access.observations(requirements)
        adapter.validate_input(observations)
        stage = "prepare"
        prepared = adapter.prepare(observations)
        stage = "predict"
        forecast = adapter.predict(prepared, weights_path=str(weights))
        stage = "output"
        validate_forecast(forecast, output, observations)
        stage = "store"
        add_forecast(root, model_id, capabilities.version, spec.sha256, forecast)
    except Exception as exc:
        manifest.update(status="failed", error=str(exc), failure_stage=stage)
        save_manifest(root, manifest)
        raise
    return forecast


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--weights", required=True)
    args = parser.parse_args()
    model_id = os.environ.get("NIB_MODEL")
    run(args.run, model_id, os.environ.get("NIB_ADAPTER_MODULE"), args.weights)
    print(model_id)


if __name__ == "__main__":
    main()
