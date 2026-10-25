"""Offline diagnostics container entry; only explicitly requested products run."""

import argparse
import json
import sys
import uuid
from pathlib import Path

from utils.stored_fields import StoredFields, save_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.config).read_text())
    if request.get("version") != 2 or set(request) - {
        "version",
        "run",
        "metrics",
        "verification_models",
        "visualization",
        "operation_id",
    }:
        raise ValueError("diagnostics requires a version 2 stored-run request")
    operation_id = request.get("operation_id")
    if operation_id is not None and (
        not isinstance(operation_id, str)
        or not operation_id
        or len(operation_id) > 80
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
            for c in operation_id
        )
    ):
        raise ValueError("invalid diagnostics operation identity")
    directory = Path(request["run"])
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("samples"):
        return _batch_main(directory, manifest, request)
    source = StoredFields(directory)
    results = []
    failed = False
    jobs = []
    if request.get("metrics"):
        from diagnostics.verification_run import verification

        attempt_id = uuid.uuid4().hex
        jobs.append(
            (
                "verification",
                None,
                attempt_id,
                lambda attempt_id=attempt_id: verification(
                    directory,
                    request["metrics"],
                    attempt_id=attempt_id,
                    models=request.get("verification_models"),
                ),
            )
        )
    if request.get("visualization"):
        from diagnostics.map_visualization import DEFAULT_BASEMAP, visualization

        config = request["visualization"]
        for model in config["models"]:
            if model not in source.manifest["forecasts"]:
                raise ValueError(
                    "visualization selects a model without stored forecasts"
                )
            attempt_id = uuid.uuid4().hex
            jobs.append(
                (
                    "visualization",
                    model,
                    attempt_id,
                    lambda model=model, attempt_id=attempt_id: visualization(
                        directory,
                        model,
                        config.get("basemap", DEFAULT_BASEMAP),
                        attempt_id=attempt_id,
                    ),
                )
            )
    if not jobs:
        raise ValueError("select at least one diagnostic product")
    for kind, model_id, attempt_id, job in jobs:
        try:
            result = job()
            results.append(result)
            failed = failed or result["status"] != "succeeded"
        except Exception as exc:  # noqa: BLE001 - one attempt failure is data
            failed = True
            out = directory / "diagnostics" / kind / attempt_id
            out.mkdir(parents=True, exist_ok=True)
            error = {
                "kind": kind,
                "id": attempt_id,
                "model_id": model_id,
                "path": out.relative_to(directory).as_posix(),
                "status": "failed",
                "error": str(exc),
            }
            (out / "summary.json").write_text(json.dumps(error, indent=2) + "\n")
            current = StoredFields(directory)
            current.manifest["diagnostics"].append(error)
            save_manifest(directory, current.manifest)
            results.append(error)
            print(json.dumps(error), file=sys.stderr)
    source = StoredFields(directory)
    source.manifest["status"] = "partial" if failed else "succeeded"
    # Each report contains exactly this request's attempts, including failures.
    for result in results:
        result["operation_id"] = operation_id
        for recorded in source.manifest["diagnostics"]:
            if recorded.get("id") == result.get("id"):
                recorded["operation_id"] = operation_id
    if results:
        report = write_report(directory, results, operation_id=operation_id)
        source.manifest["diagnostics"].append(report)
        results.append(report)
    save_manifest(directory, source.manifest)
    print(json.dumps({"run_id": source.manifest["id"], "diagnostics": results}))
    return 1 if failed else 0


def _batch_main(directory, manifest, request):
    results = []
    failed = False
    jobs = []
    samples = manifest.get("samples") or []
    children = [directory / item["path"] for item in samples]
    if not children:
        raise ValueError("batch run has no sample windows")
    if request.get("metrics"):
        from diagnostics.verification_run import verification_batch

        attempt_id = uuid.uuid4().hex
        jobs.append(
            (
                "verification",
                None,
                None,
                attempt_id,
                lambda attempt_id=attempt_id: verification_batch(
                    directory,
                    samples,
                    request["metrics"],
                    attempt_id=attempt_id,
                    models=request.get("verification_models"),
                ),
            )
        )
    if request.get("visualization"):
        from diagnostics.map_visualization import DEFAULT_BASEMAP, visualization

        config = request["visualization"]
        for sample, child in zip(samples, children):
            child_fields = StoredFields(child)
            for model in config["models"]:
                if model not in child_fields.manifest["forecasts"]:
                    raise ValueError(
                        "visualization selects a model without stored forecasts"
                    )
                attempt_id = uuid.uuid4().hex
                jobs.append(
                    (
                        "visualization",
                        model,
                        sample.get("sample_id"),
                        attempt_id,
                        lambda model=model, child=child, attempt_id=attempt_id: visualization(
                            child,
                            model,
                            config.get("basemap", DEFAULT_BASEMAP),
                            attempt_id=attempt_id,
                        ),
                    )
                )
    if not jobs:
        raise ValueError("select at least one diagnostic product")
    for kind, model_id, sample_id, attempt_id, job in jobs:
        try:
            result = job()
            if kind == "visualization":
                result = dict(result)
                case = next(
                    (item for item in samples if item.get("sample_id") == sample_id),
                    samples[0],
                )
                result["sample_id"] = sample_id
                result["path"] = (Path(case["path"]) / result["path"]).as_posix()
                result["entry"] = (
                    (Path(case["path"]) / result["entry"]).as_posix()
                    if result.get("entry")
                    else None
                )
            results.append(result)
            failed = failed or result["status"] != "succeeded"
        except Exception as exc:  # noqa: BLE001
            failed = True
            out = directory / "diagnostics" / kind / attempt_id
            out.mkdir(parents=True, exist_ok=True)
            error = {
                "kind": kind,
                "id": attempt_id,
                "model_id": model_id,
                "sample_id": sample_id,
                "path": out.relative_to(directory).as_posix(),
                "status": "failed",
                "error": str(exc),
            }
            (out / "summary.json").write_text(json.dumps(error, indent=2) + "\n")
            manifest["diagnostics"].append(error)
            results.append(error)
            print(json.dumps(error), file=sys.stderr)
    manifest["status"] = "partial" if failed else "succeeded"
    recorded_ids = {item.get("id") for item in manifest.get("diagnostics", [])}
    for result in results:
        if result.get("id") not in recorded_ids:
            manifest["diagnostics"].append(result)
    operation_id = request.get("operation_id")
    for result in results:
        result["operation_id"] = operation_id
        for recorded in manifest["diagnostics"]:
            if recorded.get("id") == result.get("id"):
                recorded["operation_id"] = operation_id
    if results:
        report = write_report(directory, results, operation_id=operation_id)
        manifest["diagnostics"].append(report)
        results.append(report)
    save_manifest(directory, manifest)
    print(json.dumps({"run_id": manifest["id"], "diagnostics": results}))
    return 1 if failed else 0


def write_report(directory, results, *, operation_id=None):
    """Register the result page route without writing a second HTML implementation.

    The Handler serves the page from the diagnostics viewer; this record is the
    stable entry Hub and the Handler already use.
    """
    report_id = uuid.uuid4().hex
    out = Path(directory) / "diagnostics" / "report" / report_id
    out.mkdir(parents=True)
    status = (
        "partial"
        if any(result.get("status", "succeeded") != "succeeded" for result in results)
        else "succeeded"
    )
    return {
        "kind": "report",
        "id": report_id,
        "operation_id": operation_id,
        "status": status,
        "attempt_ids": [result.get("id") for result in results],
        "path": out.relative_to(Path(directory)).as_posix(),
        "entry": (out / "index.html").relative_to(Path(directory)).as_posix(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
