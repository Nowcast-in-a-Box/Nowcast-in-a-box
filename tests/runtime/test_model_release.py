"""Release gates use synthetic fields and mocked Docker, never samples or weights."""

import importlib.util
import json
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from interfaces.fields import Forecast
from tests.field_fixtures import observations
from utils.model_contract import utc
from utils.stored_fields import (
    StoredFields,
    add_forecast,
    create_run,
    save_manifest,
    sha256,
)

ROOT = Path(__file__).resolve().parents[2]
IMAGE_ID = "sha256:" + "a" * 64


def load_module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    release = load_module(ROOT / "scripts/validate-model-release.py")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(release, "ROOT", checkout)

    def prepare(data_id="sevir", model_id="nowcastnet"):
        # A deliberately different physical variable prevents dataset constants passing.
        obs = observations(
            source=data_id, variable="precipitation_rate", units="mm/h", shape=(2, 2, 2)
        )
        initial = utc(obs.times[-1])
        times = tuple(
            (initial + timedelta(minutes=lead)).isoformat() for lead in (5, 10)
        )
        prediction = Forecast(
            obs.variable_id,
            obs.units,
            np.full((2, 2, 2), 3.5, dtype=np.float32),
            times,
            obs.grid,
            np.zeros((2, 2, 2), dtype=bool),
            obs.times[-1],
            (5, 10),
        )
        window = dict(
            data_id=data_id,
            cadence_minutes=5,
            variable=obs.variable_id,
            unit=obs.units,
            context=obs.values,
            target=prediction.values + 1,
            context_times=obs.times,
            target_times=times,
            context_start_time=obs.times[0],
            init_time=prediction.init_time,
            sample_id="synthetic",
            event_id="synthetic",
            row=0,
        )
        directory = create_run(
            tmp_path, "release-test", window, obs.grid, atlas_revision="test"
        )
        weights = tmp_path / "synthetic.bin"
        weights.write_bytes(b"not model weights")
        declaration = json.loads(
            (ROOT / "docs/templates/model/declaration.json").read_text()
        )
        declaration.update(id=model_id, data_adapter_id=data_id)
        declaration["input"].update(context_frames=2, spatial=[2, 2])
        declaration["output"].update(spatial=[2, 2])
        declaration["weights"].update(filename="model.bin", sha256=sha256(weights))
        contract = f"atlas/contracts/models/{data_id}/{model_id}.json"
        dockerfile = f"adapters/{data_id}/{model_id}/Dockerfile"
        for name, content in {
            contract: json.dumps(declaration),
            dockerfile: "FROM scratch\n",
            "interfaces/fields.py": "# new shared fields\n",
            "utils/model_contract.py": "# new shared checks\n",
        }.items():
            path = checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        args = SimpleNamespace(
            model=f"{data_id}/{model_id}",
            platform="linux/amd64",
            image="ghcr.io/example/candidate:test-amd64",
            run=str(directory),
            weights=str(weights),
            push=True,
        )
        events = []

        def infer():
            add_forecast(directory, model_id, "0.1.0", sha256(weights), prediction)

        state = SimpleNamespace(
            release=release,
            args=args,
            directory=directory,
            weights=weights,
            prediction=prediction,
            events=events,
            infer=infer,
            checkout=checkout,
        )

        def execute(command, **kwargs):
            events.append(command)
            if command[0] == "git":
                assert "--recurse-submodules" in command
                # New shared modules are intentionally absent from the tracked list.
                return SimpleNamespace(stdout="\0".join((contract, dockerfile)) + "\0")
            if command[:3] == ["docker", "buildx", "build"]:
                Path(command[command.index("--iidfile") + 1]).write_text(IMAGE_ID)
                context = Path(command[-1])
                assert (context / contract).is_file()
                assert not (context / "contracts").exists()
                assert (context / "interfaces/fields.py").is_file()
                assert (context / "utils/model_contract.py").is_file()
                assert not list(context.rglob("*.npz"))
                assert not list(context.rglob("*.bin"))
            elif command[:2] == ["docker", "run"]:
                assert command[command.index("--network") + 1] == "none"
                assert command[command.index("--pull") + 1] == "never"
                assert "--read-only" in command
                for descriptor in (
                    StoredFields(directory).manifest["observations"].values()
                ):
                    assert (
                        f"type=bind,src={directory / descriptor['path']},"
                        f"dst=/workspace/{directory.name}/{descriptor['path']},readonly"
                    ) in command
                assert "--entrypoint" not in command
                assert command[command.index(IMAGE_ID) + 1] == "--run"
                state.infer()
            return SimpleNamespace(stdout="")

        monkeypatch.setattr(release, "execute", execute)
        return state

    return prepare


@pytest.mark.parametrize(
    "data_id,model_id",
    [
        ("sevir", "nowcastnet"),
        ("sevir", "exprecast"),
        ("sevir", "wadepre"),
        ("cma_radar", "nowcastnet"),
        ("another_source", "another_model"),
    ],
)
def test_release_checks_then_pushes_exact_tested_image(
    candidate, monkeypatch, capsys, data_id, model_id
):
    state = candidate(data_id, model_id)
    check_run, check_reader = state.release.check_run, state.release.check_reader

    def checked_run(*args, **kwargs):
        result = check_run(*args, **kwargs)
        state.events.append(
            ["checked", "output" if kwargs.get("completed") else "input"]
        )
        return result

    def checked_reader(*args):
        check_reader(*args)
        state.events.append(["checked", "reader"])

    monkeypatch.setattr(state.release, "check_run", checked_run)
    monkeypatch.setattr(state.release, "check_reader", checked_reader)
    state.release.release(state.args)
    assert state.events[-4:] == [
        ["checked", "output"],
        ["checked", "reader"],
        ["docker", "tag", IMAGE_ID, state.args.image],
        ["docker", "push", state.args.image],
    ]
    output = capsys.readouterr().out
    assert "StoredFields public reader validated" in output
    assert ("Diagnostic handoff unsupported" in output) == (data_id != "cma_radar")


@pytest.mark.parametrize(
    "failure",
    [
        "weights",
        "inference",
        "reader",
        "missing-run",
        "missing-manifest",
        "stale",
        "failed",
        "missing-output",
        "bad-output",
    ],
)
def test_failed_gate_never_pushes(candidate, failure):
    state = candidate()
    manifest = StoredFields(state.directory).manifest
    if failure == "weights":
        state.weights.write_bytes(b"wrong digest")
    elif failure == "inference":

        def fail():
            raise subprocess.CalledProcessError(1, ["docker", "run"])

        state.infer = fail
    elif failure == "reader":
        # Valid physical fields, but incompatible truth metadata must fail at the reader.
        manifest["observations"]["target"]["unit"] = "other-unit"
        save_manifest(state.directory, manifest)
    elif failure == "missing-run":
        state.args.run = str(state.directory / "missing")
    elif failure == "missing-manifest":
        (state.directory / "manifest.json").unlink()
    elif failure == "stale":
        (state.directory / "forecasts").mkdir()
    elif failure == "failed":
        manifest["status"] = "failed"
        save_manifest(state.directory, manifest)
    elif failure == "missing-output":
        state.infer = lambda: None
    elif failure == "bad-output":
        state.infer = lambda: add_forecast(
            state.directory,
            "nowcastnet",
            "0.1.0",
            sha256(state.weights),
            replace(state.prediction, units="other-unit"),
        )
    with pytest.raises((ValueError, FileNotFoundError, subprocess.CalledProcessError)):
        state.release.release(state.args)
    assert not any(
        command[:2] in (["docker", "push"], ["docker", "tag"])
        for command in state.events
    )


@pytest.mark.parametrize("change", ["grid", "coordinates", "time", "variable", "mask"])
def test_public_reader_rejects_incompatible_fields(candidate, monkeypatch, change):
    state = candidate()
    state.infer()
    original = StoredFields.get_observation

    def incompatible(self, *args):
        field = dict(original(self, *args))
        if change == "grid":
            field["grid"] = {**field["grid"], "axis_units": "degrees"}
        elif change == "coordinates":
            field["x"] = field["x"] + 1
        elif change == "time":
            field["valid_time"] += timedelta(minutes=1)
        elif change == "variable":
            field["variable"] = "different-variable"
        else:
            field["mask"] = np.ones_like(field["mask"])
        return field

    monkeypatch.setattr(StoredFields, "get_observation", incompatible)
    with pytest.raises(ValueError, match="incompatible"):
        state.release.check_reader(state.directory, "nowcastnet")


def test_reader_requires_truth_for_every_output_lead(candidate):
    state = candidate()
    state.infer()
    manifest = StoredFields(state.directory).manifest
    manifest["observations"]["target"]["times"][-1] = "2026-07-01T00:20:00Z"
    save_manifest(state.directory, manifest)
    with pytest.raises(ValueError, match="missing truth"):
        state.release.check_reader(state.directory, "nowcastnet")


def test_cma_diagnostic_handoff_failure_never_pushes(candidate, monkeypatch):
    import diagnostics.run_source

    state = candidate("cma_radar")

    def unsupported(*args, **kwargs):
        raise ValueError("diagnostic reader failed")

    monkeypatch.setattr(diagnostics.run_source, "RunSource", unsupported)
    with pytest.raises(ValueError, match="diagnostic reader failed"):
        state.release.release(state.args)
    assert not any(command[:2] == ["docker", "push"] for command in state.events)


def test_build_context_rejects_external_symlink(candidate, tmp_path):
    state = candidate()
    source = state.checkout / "interfaces/fields.py"
    source.unlink()
    source.symlink_to(state.weights)
    with pytest.raises(ValueError, match="outside the checkout"):
        state.release.build_context(tmp_path / "context", "sevir", "nowcastnet")


def test_template_uses_single_declaration_and_raises(tmp_path):
    adapter_dir = tmp_path / "adapters/example_data/example_model"
    adapter_dir.mkdir(parents=True)
    adapter = adapter_dir / "adapter.py"
    adapter.write_text((ROOT / "docs/templates/model/adapter.py").read_text())
    contract = tmp_path / "atlas/contracts/models/example_data/example_model.json"
    contract.parent.mkdir(parents=True)
    contract.write_text((ROOT / "docs/templates/model/declaration.json").read_text())
    module = load_module(adapter)
    assert module.ADAPTER.declaration_path == contract
    obs = observations(
        source="example_data", variable="precipitation_rate", units="mm/h"
    )
    with pytest.raises(NotImplementedError, match="input preparation"):
        module.ADAPTER.prepare(obs)
    with pytest.raises(NotImplementedError, match="offline inference"):
        module.ADAPTER.predict(None, weights_path="unused")
    dockerfile = (ROOT / "docs/templates/model/Dockerfile").read_text()
    assert "COPY atlas/contracts/models/example_data/example_model.json " in dockerfile
    assert not (adapter_dir / "declaration.json").exists()
