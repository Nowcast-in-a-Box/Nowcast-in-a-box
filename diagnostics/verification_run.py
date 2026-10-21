"""Verification execution over immutable stored Forecasts, without inference."""

import json
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from nib_verify.access.stored import RunAccess
from nib_verify.config.schema import Member, MetricGroup, Ruler, VerificationConfig
from nib_verify.query import query_scores
from nib_verify.runner import RunRef, run_tasks
from nib_verify.store import Store

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
    data_id = manifest["data"]["id"]
    field_spec = manifest["observations"]["target"]
    reference = access.get_observation(data_id, initial)
    dx, dy = np.diff(reference.coords.x), np.diff(reference.coords.y)
    if not np.allclose(dx, dx[0]) or not np.allclose(dy, dx[0]):
        raise ValueError("verification currently requires square regular cells")
    cfg = VerificationConfig(
        group_id=manifest["id"],
        name=manifest["sample"]["sample_id"],
        ruler=Ruler(
            data_id,
            field_spec["unit"],
            manifest["data"].get("cadence_minutes", 5),
            reference.coords.extent,
            float(dx[0]),
            reference.coords.crs,
        ),
        members=tuple(
            Member(
                m,
                field_spec["variable"],
                field_spec["unit"],
                tuple(access.list_predictions(m, initial)),
            )
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
        for group in cfg.metrics:
            for method in group.items:
                summary["scores"][method] = query_scores(
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
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n"
    )
    attempt = {
        "kind": "verification",
        "id": attempt_id,
        "status": summary["status"],
        "path": out.relative_to(Path(directory)).as_posix(),
    }
    manifest["diagnostics"].append(attempt)
    save_manifest(directory, manifest)
    return attempt


def verification_batch(directory, samples, metrics, *, attempt_id=None, models=None):
    """Verify every child run into one store, so metrics can average all init times.

    Each child is still opened through its own immutable RunAccess. Only the
    additive store is shared; no child forecast or observation is copied into
    another child.
    """
    parent = Path(directory)
    children = [parent / item["path"] for item in samples]
    if not children:
        raise ValueError("batch verification requires at least one sample")
    first = RunAccess(children[0])
    manifest = first.fields.manifest
    if not manifest["forecasts"]:
        raise ValueError("no saved Forecasts to verify")
    initial = datetime.fromisoformat(
        manifest["sample"]["init_time"].replace("Z", "+00:00")
    )
    model_ids = list(manifest["forecasts"]) if models is None else models
    if not model_ids or any(model not in manifest["forecasts"] for model in model_ids):
        raise ValueError("verification requires selected saved Forecasts")
    data_id = manifest["data"]["id"]
    field_spec = manifest["observations"]["target"]
    reference = first.get_observation(data_id, initial)
    dx, dy = np.diff(reference.coords.x), np.diff(reference.coords.y)
    if not np.allclose(dx, dx[0]) or not np.allclose(dy, dx[0]):
        raise ValueError("verification currently requires square regular cells")
    parent_manifest = json.loads((parent / "manifest.json").read_text())
    group_id = str(parent_manifest.get("id") or parent.name)
    cfg = VerificationConfig(
        group_id=group_id,
        name=samples[0].get("sample_id", group_id),
        ruler=Ruler(
            data_id,
            field_spec["unit"],
            manifest["data"].get("cadence_minutes", 5),
            reference.coords.extent,
            float(dx[0]),
            reference.coords.crs,
        ),
        members=tuple(
            Member(
                m,
                field_spec["variable"],
                field_spec["unit"],
                tuple(first.list_predictions(m, initial)),
            )
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
    out = parent / "diagnostics" / "verification" / attempt_id
    out.mkdir(parents=True, exist_ok=False)
    (out / "config.json").write_text(cfg.to_json() + "\n")
    provenance = {
        "run_id": parent.name,
        "samples": samples,
        "sample": manifest["sample"],
        "ruler_fingerprint": cfg.ruler_fingerprint,
        "children": [item.relative_to(parent).as_posix() for item in children],
        "grid": manifest["observations"]["target"]["grid"],
        "library": "nib-verify",
        "library_version": "0.1.0",
    }
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    totals = {"total": 0, "ran": 0, "skipped": 0, "unavailable": 0, "failed": 0, "obs_missing": 0}
    with Store(out / "verify.sqlite") as store:
        for child in children:
            access = RunAccess(child)
            child_manifest = access.fields.manifest
            child_initial = datetime.fromisoformat(
                child_manifest["sample"]["init_time"].replace("Z", "+00:00")
            )
            stats = run_tasks(
                access,
                store,
                cfg,
                [RunRef(model, child_initial) for model in model_ids],
            )
            for key in totals:
                totals[key] += int(getattr(stats, key, 0))
        summary = {
            "status": (
                "failed"
                if totals["failed"]
                else "partial" if totals["unavailable"] else "succeeded"
            ),
            **totals,
            "scores": {},
        }
        for group in cfg.metrics:
            for method in group.items:
                summary["scores"][method] = query_scores(
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
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n"
    )
    attempt = {
        "kind": "verification",
        "id": attempt_id,
        "status": summary["status"],
        "path": out.relative_to(parent).as_posix(),
    }
    return attempt
