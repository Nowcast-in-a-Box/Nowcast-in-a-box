"""Verification execution over immutable stored Forecasts, without inference."""

from datetime import datetime
from html import escape
import json
from pathlib import Path
import uuid

import numpy as np

from nib_verify.access.stored import RunAccess
from nib_verify.config.schema import VerificationConfig, Ruler, Member, MetricGroup
from nib_verify.runner import run_tasks, RunRef
from nib_verify.store import Store
from nib_verify.query import query_scores
from nib_verify.report import chart_for, line_chart

from utils.stored_fields import save_manifest


def verification(directory, metrics, *, attempt_id=None, models=None):
    access = RunAccess(directory)
    manifest = access.fields.manifest
    if not manifest["forecasts"]:
        raise ValueError("no saved Forecasts to verify")
    initial = datetime.fromisoformat(
        manifest["sample"]["init_time"].replace("Z", "+00:00")
    )
    model_ids = list(manifest["forecasts"]) if models is None else models
    if not model_ids or any(model not in manifest["forecasts"] for model in model_ids):
        raise ValueError("verification requires selected saved Forecasts")
    reference = access.get_observation("sevir", initial)
    dx, dy = np.diff(reference.coords.x), np.diff(reference.coords.y)
    if not np.allclose(dx, dx[0]) or not np.allclose(dy, dx[0]):
        raise ValueError("verification currently requires square regular cells")
    cfg = VerificationConfig(
        group_id=manifest["id"],
        name=manifest["sample"]["sample_id"],
        ruler=Ruler(
            "sevir",
            "raw",
            5,
            reference.coords.extent,
            float(dx[0]),
            reference.coords.crs,
        ),
        members=tuple(
            Member(m, "vil", "raw", tuple(access.list_predictions(m, initial)))
            for m in model_ids
        ),
        metrics=tuple(
            MetricGroup(
                family=g["family"],
                items=tuple(g["items"]),
                levels=tuple(g.get("levels", [])),
                half_windows=tuple(g.get("half_windows", [])),
            )
            for g in metrics
        ),
    )
    attempt_id = attempt_id or uuid.uuid4().hex
    if (
        not isinstance(attempt_id, str)
        or len(attempt_id) != 32
        or any(c not in "0123456789abcdef" for c in attempt_id)
    ):
        raise ValueError("invalid diagnostic attempt ID")
    out = Path(directory) / "diagnostics" / "verification" / attempt_id
    out.mkdir(parents=True, exist_ok=False)
    (out / "config.json").write_text(cfg.to_json() + "\n")
    provenance = {
        "run_id": manifest["id"],
        "sample": manifest["sample"],
        "ruler_fingerprint": cfg.ruler_fingerprint,
        "observation_sha256": {
            k: v["sha256"] for k, v in manifest["observations"].items()
        },
        "forecast_sha256": {k: v["sha256"] for k, v in manifest["forecasts"].items()},
        "grid": manifest["observations"]["target"]["grid"],
        "library": "nib-verify",
        "library_version": "0.1.0",
    }
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    with Store(out / "verify.sqlite") as store:
        stats = run_tasks(access, store, cfg, [RunRef(m, initial) for m in model_ids])
        summary = {
            "status": (
                "failed"
                if stats.failed
                else "partial" if stats.unavailable else "succeeded"
            ),
            **stats.as_dict(),
            "scores": {},
        }
        images = []
        for group in cfg.metrics:
            for method in group.items:
                query = query_scores(
                    store,
                    cfg,
                    method=method,
                    g=[
                        "member",
                        "lead_minutes",
                        *(["level"] if group.levels else []),
                        *(["half_window"] if group.half_windows else []),
                    ],
                )
                summary["scores"][method] = query
                svg = line_chart(
                    chart_for(
                        store,
                        cfg,
                        method=method,
                        series=(
                            "member",
                            *(["level"] if group.levels else []),
                            *(["half_window"] if group.half_windows else []),
                        ),
                    )
                )
                (out / f"{method}.svg").write_text(svg)
                images.append(
                    f'<h2>{escape(method)}</h2><img src="{escape(method)}.svg" alt="{escape(method)} score by lead">'
                )
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n"
    )
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>NiB verification</title>'
        + "<h1>Verification</h1><p>"
        + escape(cfg.name)
        + "</p>"
        + "<p>Status: "
        + summary["status"]
        + "</p>"
        + "".join(images)
    )
    attempt = {
        "kind": "verification",
        "id": attempt_id,
        "status": summary["status"],
        "path": out.relative_to(Path(directory)).as_posix(),
        "entry": (out / "index.html").relative_to(Path(directory)).as_posix(),
    }
    manifest["diagnostics"].append(attempt)
    save_manifest(directory, manifest)
    return attempt
