"""Offline diagnostics container entry; only explicitly requested products run."""

import argparse
from html import escape
import json
import os
from pathlib import Path
import sys
import uuid

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
    }:
        raise ValueError("diagnostics requires a version 2 stored-run request")
    directory = Path(request["run"])
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
        from diagnostics.visualization_run import visualization

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
                        directory, model, config["basemap"], attempt_id=attempt_id
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
        except Exception as exc:
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
    if not failed and len(results) > 1:
        report = write_report(directory, results)
        source.manifest["diagnostics"].append(report)
        results.append(report)
    save_manifest(directory, source.manifest)
    print(json.dumps({"run_id": source.manifest["id"], "diagnostics": results}))
    return 1 if failed else 0


def write_report(directory, results):
    """One page for a completed diagnostic request. Separate entries stay available."""
    report_id = uuid.uuid4().hex
    out = directory / "diagnostics" / "report" / report_id
    out.mkdir(parents=True)
    sections = []
    manifest_path = directory / "manifest.json"
    model_names = (
        json.loads(manifest_path.read_text()).get("model_names", {})
        if manifest_path.is_file()
        else {}
    )
    for index, result in enumerate(results):
        source = directory / result["entry"].rsplit("/", 1)[0]
        images = []
        names = [
            name for name in ("map.png", "animation.gif") if (source / name).is_file()
        ]
        names.extend(image.name for image in sorted(source.glob("*.svg")))
        for name in names:
            target = f"{index}-{name}"
            os.link(source / name, out / target)
            images.append(
                f'<img src="{escape(target)}" alt="{escape(Path(name).stem)}">'
            )
        if images:
            model = result.get("model_id")
            label = model_names.get(model, model)
            title = f"{label} — Maps and animation" if model else "Metrics"
            sections.append(
                f'<section><h2>{escape(title)}</h2>{ "".join(images) }</section>'
            )
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>NiB result</title>'
        "<style>body{margin:0;font-family:sans-serif;background:#edf1f2}"
        "main{max-width:1480px;margin:auto;padding:32px}img{width:100%;height:auto;background:#fff}</style>"
        f'<main><h1>Run result</h1>{ "".join(sections) }</main>'
    )
    return {
        "kind": "report",
        "id": report_id,
        "status": "succeeded",
        "path": out.relative_to(directory).as_posix(),
        "entry": (out / "index.html").relative_to(directory).as_posix(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
