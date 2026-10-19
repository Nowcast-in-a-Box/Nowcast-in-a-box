"""Offline diagnostics container entry; only explicitly requested products run."""

import argparse
from html import escape
import json
import os
from pathlib import Path
import sys
import uuid

from utils.stored_fields import StoredFields, save_manifest


REPORT_STYLE = r"""
:root {
  --bg: #edf1f2;
  --paper: #ffffff;
  --ink: #13282d;
  --muted: #53676d;
  --line: #d6e1e5;
  --accent: #164b58;
  --accent-strong: #005a6c;
  --ok-bg: #e8f5ef;
  --ok-ink: #0b6148;
  --warn-bg: #fff3df;
  --warn-ink: #815200;
  --bad-bg: #ffe9e1;
  --bad-ink: #8f2f1b;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
main { max-width: 1320px; margin: 0 auto; padding: clamp(22px, 4vw, 56px); }
h1 { margin: 0; font-size: clamp(2rem, 3.8vw, 3.2rem); line-height: 1.05; letter-spacing: -0.035em; }
h2 { margin: 0; font-size: 1.35rem; line-height: 1.25; letter-spacing: -0.015em; }
h3 { margin: 0; font-size: 1.05rem; line-height: 1.3; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.86em; }
.result-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; }
.result-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 7px 12px; margin: 14px 0 0; color: var(--muted); font-size: 0.86rem; }
.result-meta code { color: var(--ink); }
.status { display: inline-flex; align-items: center; min-height: 24px; padding: 3px 9px; border-radius: 999px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em; text-transform: uppercase; }
.status-succeeded { color: var(--ok-ink); background: var(--ok-bg); }
.status-partial { color: var(--warn-ink); background: var(--warn-bg); }
.status-failed { color: var(--bad-ink); background: var(--bad-bg); }
.notice { max-width: 82ch; margin: 18px 0 0; color: var(--muted); font-size: 0.9rem; }
.views { display: block; margin: 24px 0 0; padding: 0; border: 0; }
.views legend { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.views > input { position: absolute; opacity: 0; pointer-events: none; }
.views > label { display: inline-block; margin-right: 4px; padding: 9px 18px; border: 1px solid var(--line); border-radius: 8px; background: var(--paper); color: var(--muted); cursor: pointer; font-size: 0.86rem; font-weight: 650; }
.views > input:focus-visible + label { outline: 3px solid var(--accent-strong); outline-offset: 2px; }
.views > input:checked + label { border-color: var(--accent); background: var(--accent); color: #fff; }
.panel { display: none; margin-top: 26px; padding: 28px; border: 1px solid var(--line); border-radius: 14px; background: var(--paper); box-shadow: 0 2px 4px rgb(19 40 45 / 4%); }
#view-visualization:checked ~ .visualization, #view-verification:checked ~ .verification { display: block; }
.panel > h2 { padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.model { margin-top: 22px; }
.model:first-of-type { margin-top: 18px; }
.artifacts { display: grid; grid-template-columns: minmax(0, 1fr); gap: 18px; margin-top: 14px; }
.artifact { margin: 0; overflow: hidden; border: 1px solid var(--line); border-radius: 10px; background: #f7fafb; }
.artifact img { display: block; width: 100%; height: auto; }
.artifact figcaption { padding: 9px 12px; border-top: 1px solid var(--line); color: var(--muted); font-size: 0.78rem; }
.panel p { max-width: 78ch; }
[role=alert] { padding: 12px 14px; border-radius: 8px; background: var(--bad-bg); color: var(--bad-ink); }
@media (max-width: 640px) {
  main { padding: 18px 14px 28px; }
  .result-head { align-items: flex-start; flex-direction: column; }
  .views > label { padding-inline: 14px; }
  .panel { padding: 18px; }
}
"""


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
        artifacts = []
        if result.get("entry"):
            source = directory / result["entry"].rsplit("/", 1)[0]
            names = [
                name
                for name in ("comparison.png", "map.png", "animation.gif")
                if (source / name).is_file()
            ]
            names.extend(image.name for image in sorted(source.glob("*.svg")))
            captions = {
                "comparison.png": "Latest input, observed field and forecast",
                "map.png": "Forecast map",
                "animation.gif": "Forecast animation",
            }
            for name in names:
                target = f"{index}-{name}"
                os.link(source / name, out / target)
                caption = captions.get(name, Path(name).stem.replace("_", " "))
                artifacts.append(
                    '<figure class="artifact">'
                    f'<img src="{escape(target)}" alt="{escape(str(title))}: {escape(caption)}">'
                    f'<figcaption>{escape(caption)}</figcaption></figure>'
                )
        status = result.get("status", "succeeded")
        alerts = []
        if status != "succeeded":
            reason = result.get(
                "error",
                "Some requested fields were unavailable. Inspect this attempt summary and log.",
            )
            alerts.append(
                f'<p role="alert">{escape(status)}: {escape(str(reason))}</p>'
            )
        if not artifacts and not alerts:
            alerts.append(
                "<p>No display artifact was produced. Inspect the diagnostic attempt summary.</p>"
            )
        artifact_html = f'<div class="artifacts">{"".join(artifacts)}</div>' if artifacts else ""
        sections[kind].append(
            f'<section class="model"><h3>{escape(str(title))}</h3>'
            f'{"".join(alerts)}{artifact_html}</section>'
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
    status_class = f"status-{status}"
    notice_html = f'<p class="notice">{escape(str(notice))}</p>' if notice else ""
    (out / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>NiB result</title><style>{REPORT_STYLE}</style></head><body><main>'
        '<header class="result-head"><div><h1>Run result</h1>'
        '<p class="result-meta">'
        f'<code>{escape(str(stored.get("id", directory.name)))}</code>'
        f'<span aria-hidden="true">·</span><time>{escape(str(sample.get("init_time", "")))}</time>'
        f'<span class="status {status_class}">{escape(status)}</span>'
        '</p></div></header>'
        f'{notice_html}'
        '<fieldset class="views"><legend>Result views</legend>'
        '<input id="view-visualization" type="radio" name="view" checked><label id="visualization-label" for="view-visualization">Visualization</label>'
        '<input id="view-verification" type="radio" name="view"><label id="verification-label" for="view-verification">Metrics</label>'
        f'{"".join(panels)}</fieldset></main></body></html>'
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
