#!/usr/bin/env python3
"""Build, validate offline, and optionally push ONE model/platform candidate."""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def execute(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def declaration_for(data_id, model_id):
    from interfaces.model import DeclaredModelAdapter

    class Declaration(DeclaredModelAdapter):
        declaration_path = (
            ROOT / "atlas/contracts/models" / data_id / f"{model_id}.json"
        )

        def prepare(self, observations):
            raise NotImplementedError

        def predict(self, prepared, *, weights_path):
            raise NotImplementedError

    declaration = Declaration()
    identity = declaration.capabilities()
    if (identity.data_adapter_id, identity.id) != (data_id, model_id):
        raise ValueError("selected model disagrees with declaration")
    return declaration


def check_run(directory, declaration, *, completed=False):
    from interfaces.fields import Forecast
    from utils.model_contract import validate_forecast
    from utils.stored_fields import StoredFields

    access = StoredFields(directory)
    manifest = access.manifest
    identity = declaration.capabilities()
    if manifest.get("status") == "failed":
        raise ValueError("failed run; prepare a fresh release run")
    if manifest["data"]["id"] != identity.data_adapter_id:
        raise ValueError("run data source disagrees with selected model")
    observations = access.observations(declaration.input_requirements())
    declaration.validate_input(observations)
    access.cube(manifest["observations"]["target"])
    if not completed:
        if manifest["forecasts"] or (Path(directory) / "forecasts").exists():
            raise ValueError("release requires a fresh run without forecasts")
        return manifest
    if set(manifest["forecasts"]) != {identity.id}:
        raise ValueError("candidate must produce exactly the selected forecast")
    descriptor = manifest["forecasts"][identity.id]
    if (
        descriptor["model_id"] != identity.id
        or descriptor["model_version"] != identity.version
        or descriptor["weight_sha256"] != declaration.weights().sha256
    ):
        raise ValueError("saved forecast identity or weight digest disagrees")
    cube = access.cube(descriptor)
    forecast = Forecast(
        descriptor["variable"],
        descriptor["unit"],
        cube["values"],
        tuple(descriptor["times"]),
        {**descriptor["grid"], "x": cube["x"], "y": cube["y"]},
        cube["mask"],
        descriptor["init_time"],
        tuple(descriptor["lead_minutes"]),
    )
    validate_forecast(forecast, declaration.output_specification(), observations)
    return manifest


def check_reader(directory, model_id):
    from datetime import timedelta

    import numpy as np

    from utils.model_contract import utc
    from utils.stored_fields import StoredFields

    fields = StoredFields(directory)
    manifest = fields.manifest
    data_id = manifest["data"]["id"]
    initial = utc(manifest["sample"]["init_time"])
    descriptor = manifest["forecasts"][model_id]
    leads = fields.list_predictions(model_id, initial)
    if not leads or leads != descriptor["lead_minutes"]:
        raise ValueError("public reader did not expose selected forecast leads")
    times = fields.list_observations(
        data_id,
        initial + timedelta(minutes=leads[0]),
        initial + timedelta(minutes=leads[-1]),
    )
    for lead in leads:
        valid_time = initial + timedelta(minutes=lead)
        if valid_time not in times:
            raise ValueError("public reader is missing truth for a forecast lead")
        forecast = fields.get_prediction(model_id, initial, lead)
        truth = fields.get_observation(data_id, valid_time)
        if (
            forecast["valid_time"] != valid_time
            or truth["valid_time"] != valid_time
            or truth["data"].shape != forecast["data"].shape
            or truth["grid"] != forecast["grid"]
            or truth["unit"] != forecast["unit"]
            or truth["variable"] != forecast["variable"]
            or not np.array_equal(truth["x"], forecast["x"])
            or not np.array_equal(truth["y"], forecast["y"])
            or not np.any(
                ~truth["mask"]
                & ~forecast["mask"]
                & np.isfinite(truth["data"])
                & np.isfinite(forecast["data"])
            )
        ):
            raise ValueError("public reader truth/forecast fields are incompatible")
    print(f"StoredFields public reader validated: {data_id}/{model_id}")
    if data_id != "cma_radar":
        print(
            f"Diagnostic handoff unsupported for {data_id}; mandatory field checks passed"
        )
        return

    from diagnostics.run_source import RunSource

    source = RunSource(
        directory,
        models=[model_id],
        version=1,
        metrics=[{"family": "continuous", "items": ["mae"]}],
    )
    group = source.get_group(source.list_groups()[0])
    (member,) = group["forecasts"]
    if member["model_id"] != model_id or not member["leads_minutes"]:
        raise ValueError("public reader did not expose selected forecast")
    for lead in member["leads_minutes"]:
        forecast = source.read_forecast(
            member["name"], group["time"]["init_from"], lead
        )
        truth = source.read_truth(group["truth"]["name"], forecast.attrs["valid_time"])
        if (
            truth.dims != forecast.dims
            or truth.shape != forecast.shape
            or truth.attrs["crs"] != forecast.attrs["crs"]
            or truth.attrs["units"] != forecast.attrs["units"]
            or truth.attrs["variable"] != forecast.attrs["variable"]
            or not np.any(np.isfinite(truth.values) & np.isfinite(forecast.values))
        ):
            raise ValueError("public reader truth/forecast fields are incompatible")
        for coordinate in truth.coords:
            np.testing.assert_array_equal(truth[coordinate], forecast[coordinate])


def build_context(destination, data_id, model_id):
    result = execute(
        [
            "git",
            "ls-files",
            "--recurse-submodules",
            "-z",
            "--",
            "adapters",
            "atlas/contracts/models",
            "interfaces",
            "utils",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    files = set(filter(None, result.stdout.split("\0")))
    # Keep new shared contract modules available before the integration is staged.
    files.update({"interfaces/fields.py", "utils/model_contract.py"})
    for name in sorted(files):
        relative = Path(name)
        if relative.name != "Dockerfile" and relative.suffix not in {
            ".py",
            ".json",
            ".txt",
        }:
            continue
        source = ROOT / relative
        if (
            source.is_symlink()
            or not source.is_file()
            or not source.resolve().is_relative_to(ROOT)
        ):
            raise ValueError(
                f"build source is missing or outside the checkout: {relative}"
            )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def release(args):
    from utils.stored_fields import sha256

    if not re.fullmatch(r"[a-z0-9_-]+/[a-z0-9_-]+", args.model):
        raise ValueError("model must be data-id/model-id")
    if args.platform not in ("linux/amd64", "linux/arm64"):
        raise ValueError("select one supported platform")
    if not re.fullmatch(
        r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}",
        args.image,
    ):
        raise ValueError("image must be an explicit ghcr.io/owner/name:tag")
    data_id, model_id = args.model.split("/")
    declaration = declaration_for(data_id, model_id)
    weights = Path(args.weights).resolve(strict=True)
    expected = declaration.weights().sha256
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("declaration needs a pinned SHA256")
    if not weights.is_file() or sha256(weights) != expected:
        raise ValueError(
            "offline weight checksum mismatch; prepare verified weights first"
        )
    directory = Path(args.run).resolve(strict=True)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", directory.name) or "," in str(directory):
        raise ValueError("run path needs a safe ID and must not contain commas")
    before = check_run(directory, declaration)
    if before["id"] != directory.name:
        raise ValueError("run directory name must equal manifest ID")
    dockerfile = Path("adapters") / data_id / model_id / "Dockerfile"
    if not (ROOT / dockerfile).is_file():
        raise ValueError("selected model Dockerfile is missing")
    with tempfile.TemporaryDirectory(prefix="nib-release-") as temporary:
        staging = Path(temporary)
        context = staging / "source"
        context.mkdir()
        build_context(context, data_id, model_id)
        weight_dir = staging / "weights"
        weight_dir.mkdir()
        filename = declaration.weights().filename
        if (
            not filename
            or Path(filename).name != filename
            or filename in (".", "..")
            or "," in filename
        ):
            raise ValueError("declaration needs a plain weight filename")
        frozen_weight = weight_dir / filename
        shutil.copyfile(weights, frozen_weight)
        if sha256(frozen_weight) != expected:
            raise ValueError("weight changed while preparing candidate")
        iidfile = staging / "image-id"
        execute(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                args.platform,
                "--load",
                "--build-context",
                f"weights={weight_dir}",
                "--iidfile",
                str(iidfile),
                "-f",
                str(context / dockerfile),
                "-t",
                args.image,
                str(context),
            ]
        )
        image_id = iidfile.read_text().strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("build did not return a local image digest")
        execute(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--platform",
                args.platform,
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--mount",
                f"type=bind,src={directory},dst=/workspace/{directory.name}",
                "--mount",
                f"type=bind,src={weight_dir},dst=/weights,readonly",
                *[
                    argument
                    for descriptor in before["observations"].values()
                    for argument in (
                        "--mount",
                        f"type=bind,src={directory / descriptor['path']},"
                        f"dst=/workspace/{directory.name}/{descriptor['path']},readonly",
                    )
                ],
                image_id,
                "--run",
                f"/workspace/{directory.name}",
                "--weights",
                f"/weights/{filename}",
            ]
        )
        after = check_run(directory, declaration, completed=True)
        for key in ("id", "data", "sample", "observations"):
            if after[key] != before[key]:
                raise ValueError("candidate changed prepared run inputs")
        check_reader(directory, model_id)
        if args.push:
            # Retag the exact tested image, even if another local build reused its tag.
            execute(["docker", "tag", image_id, args.image])
            execute(["docker", "push", args.image])
    print(f"Validated {args.model} on {args.platform}: {args.image}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="data-id/model-id")
    parser.add_argument(
        "--platform", required=True, choices=["linux/amd64", "linux/arm64"]
    )
    parser.add_argument("--image", required=True, help="explicit GHCR candidate tag")
    parser.add_argument(
        "--run", required=True, help="fresh, locally prepared run directory"
    )
    parser.add_argument("--weights", required=True, help="local pinned weight file")
    parser.add_argument(
        "--push", action="store_true", help="push only after every check passes"
    )
    release(parser.parse_args())


if __name__ == "__main__":
    main()
