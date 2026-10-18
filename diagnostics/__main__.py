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


def write_report(directory, results, *, operation_id=None):
    """Read-only native radio tabs keep strict CSP and keyboard navigation."""
    report_id = uuid.uuid4().hex
    out = directory / "diagnostics" / "report" / report_id
    out.mkdir(parents=True)
    manifest_path = directory / "manifest.json"
    stored = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    model_names = stored.get("model_names", {})
    sample = stored.get("sample", {})
    notice = sample.get("provenance", {}).get("notice", "")
    sections = {"visualization": [], "verification": []}
    for index, result in enumerate(results):
        kind = result.get("kind")
        if kind not in sections:
            continue
        model = result.get("model_id")
        title = model_names.get(model, model) if model else "Metrics"
        content = []
        if result.get("entry"):
            source = directory / result["entry"].rsplit("/", 1)[0]
            names = [
                name
                for name in ("comparison.png", "map.png", "animation.gif")
                if (source / name).is_file()
            ]
            names.extend(image.name for image in sorted(source.glob("*.svg")))
            for name in names:
                target = f"{index}-{name}"
                os.link(source / name, out / target)
                content.append(
                    f'<img src="{escape(target)}" alt="{escape(str(title))}: {escape(Path(name).stem)}">'
                )
        status = result.get("status", "succeeded")
        if status != "succeeded":
            reason = result.get(
                "error",
                "Some requested fields were unavailable. Inspect this attempt summary and log.",
            )
            content.append(
                f'<p role="alert">{escape(status)}: {escape(str(reason))}</p>'
            )
        if not content:
            content.append(
                "<p>No display artifact was produced. Inspect the diagnostic attempt summary.</p>"
            )
        sections[kind].append(
            f'<section><h3>{escape(str(title))}</h3>{"".join(content)}</section>'
        )
    panels = []
    for kind, label in (
        ("visualization", "Visualization"),
        ("verification", "Metrics"),
    ):
        content = (
            "".join(sections[kind])
            or "<p>This diagnostic was not selected for this request.</p>"
        )
        panels.append(
            f'<section class="panel {kind}" aria-labelledby="{kind}-label"><h2>{label}</h2>{content}</section>'
        )
    status = (
        "partial"
        if any(result.get("status", "succeeded") != "succeeded" for result in results)
        else "succeeded"
    )
    (out / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>NiB result</title><style>"
        "body{margin:0;font-family:system-ui,sans-serif;background:#edf1f2;color:#13282d}"
        "main{max-width:1480px;margin:auto;padding:24px}img{width:100%;height:auto;background:#fff}"
        ".views{border:0;margin:20px 0;padding:0}.views legend{margin-bottom:12px}"
        ".views>input{position:absolute;opacity:0}.views>label{display:inline-block;padding:12px 20px;background:white;cursor:pointer}"
        ".views>input:focus-visible+label{outline:3px solid #005a6c;outline-offset:2px}"
        ".views>input:checked+label{background:#164b58;color:white}.panel{display:none;padding-top:16px}"
        "#view-visualization:checked~.visualization,#view-verification:checked~.verification{display:block}"
        "[role=alert]{padding:12px;background:#ffe6da}section{margin-bottom:24px}"
        "</style><main><h1>Run result</h1>"
        f'<p>Run {escape(str(stored.get("id", directory.name)))} · {escape(str(sample.get("init_time", "")))} · {status}</p>'
        f'<p>{escape(str(notice))}</p><fieldset class="views"><legend>Result views</legend>'
        '<input id="view-visualization" type="radio" name="view" checked><label id="visualization-label" for="view-visualization">Visualization</label>'
        '<input id="view-verification" type="radio" name="view"><label id="verification-label" for="view-verification">Metrics</label>'
        f'{"".join(panels)}</fieldset></main></html>'
    )
    return {
        "kind": "report",
        "id": report_id,
        "operation_id": operation_id,
        "status": status,
        "attempt_ids": [result.get("id") for result in results],
        "path": out.relative_to(directory).as_posix(),
        "entry": (out / "index.html").relative_to(directory).as_posix(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
