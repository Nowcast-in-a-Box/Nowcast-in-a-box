"""One local diagnostics result viewer for both tabs.

The Handler starts exactly one diagnostics container for a result page. That
container serves the Visualization and Metrics tabs from this process; there is
no second metrics container and no iframe.

The verification API is the merged ``nib_verify.server`` app. This module adds
the current run's visualization artifacts and the result-page route around it.
"""

from __future__ import annotations

import argparse
import atexit
import json
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from nib_verify.catalog.hostconfig import HostConfig
from nib_verify.server import create_app

PAGE_CSP = (
    "default-src 'none'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

CAPTIONS = {
    "comparison.png": "Latest input, observed field and forecast",
    "map.png": "Forecast map",
    "animation.gif": "Forecast animation",
}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _config_directory(verification: Path) -> Path:
    """Give the config library the filename it expects.

    A verification attempt stores its config as ``config.json``. The merged
    server's library scans ``<group_id>.json`` files, so handing it the attempt
    directory directly made the config list visible but every detail request a
    404 -- and the page then had no metric to select. The container is disposable,
    so a temporary copy is enough.
    """
    config = _read_json(verification / "config.json")
    group = config.get("group")
    group_id = group.get("id") if isinstance(group, dict) else None
    if not isinstance(group_id, str) or not group_id.strip():
        return verification
    if any(char in group_id for char in '\\/:*?"<>|') or group_id.startswith("."):
        return verification
    directory = Path(tempfile.mkdtemp(prefix="nib-result-configs-"))
    (directory / f"{group_id}.json").write_text(json.dumps(config), encoding="utf-8")
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    return directory


def _verification_root(run: Path) -> Path:
    """The newest successful verification attempt, or the run itself."""
    manifest = _read_json(run / "manifest.json")
    attempts = manifest.get("diagnostics") or []
    candidates = [
        item
        for item in attempts
        if isinstance(item, dict)
        and item.get("kind") == "verification"
        and item.get("status") in ("succeeded", "partial")
        and isinstance(item.get("path"), str)
    ]
    if candidates:
        candidate = run / candidates[-1]["path"]
        if candidate.is_dir():
            return candidate
    return run


def _visualization_models(run: Path) -> list[dict]:
    manifest = _read_json(run / "manifest.json")
    names = manifest.get("model_names") or {}
    latest: dict[str, dict] = {}
    for attempt in manifest.get("diagnostics") or []:
        if not isinstance(attempt, dict):
            continue
        if attempt.get("kind") != "visualization":
            continue
        if attempt.get("status") not in ("succeeded", "partial"):
            continue
        path = attempt.get("path")
        model_id = attempt.get("model_id")
        if not isinstance(path, str) or not isinstance(model_id, str):
            continue
        # A retry appends another attempt. The result page shows the newest
        # visualization per model, not one card per historical retry.
        latest[model_id] = attempt
    models: list[dict] = []
    for model_id, attempt in latest.items():
        path = attempt.get("path")
        directory = run / path
        if not directory.is_dir():
            continue
        images = []
        for name, caption in CAPTIONS.items():
            if not (directory / name).is_file():
                continue
            images.append(
                {
                    "url": f"/visualization/{quote(path, safe='/')}/{name}",
                    "caption": caption,
                    "alt": f"{names.get(model_id, model_id)}: {caption}",
                }
            )
        for image in sorted(directory.glob("*.svg")):
            images.append(
                {
                    "url": f"/visualization/{quote(path, safe='/')}/{quote(image.name)}",
                    "caption": image.stem.replace("_", " "),
                    "alt": f"{names.get(model_id, model_id)}: {image.stem}",
                }
            )
        if not images:
            continue
        models.append(
            {
                "id": model_id,
                "name": names.get(model_id, model_id),
                "notice": (manifest.get("sample") or {}).get("provenance", {}).get("notice"),
                "images": images,
            }
        )
    return models

def _safe_visualization_file(run: Path, path: str) -> Path:
    candidate = (run / Path(path)).resolve()
    root = run.resolve()
    if root not in candidate.parents:
        raise HTTPException(status_code=404, detail="visualization file unavailable")
    relative = candidate.relative_to(root)
    batch_visualization = (
        len(relative.parts) >= 4
        and relative.parts[0] == "samples"
        and relative.parts[2:4] == ("diagnostics", "visualization")
    )
    if relative.parts[:2] != ("diagnostics", "visualization") and not batch_visualization:
        raise HTTPException(status_code=404, detail="visualization file unavailable")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="visualization file unavailable")
    return candidate


def _visualization_payload(run: Path) -> dict:
    manifest = _read_json(run / "manifest.json")
    models = _visualization_models(run)
    return {
        "run_id": manifest.get("id", run.name),
        "sample_id": (manifest.get("sample") or {}).get("sample_id"),
        "models": models,
        "message": None if models else "No visualization was selected for this run.",
    }


def build_app(run: Path) -> FastAPI:
    verification = _verification_root(run)
    app = create_app(
        root=verification,
        configs=_config_directory(verification),
        packages=[],
        runs=[],
        host=HostConfig(),
        access=None,
        result_visualization=lambda: _visualization_payload(run),
    )

    @app.middleware("http")
    async def result_headers(request, call_next):
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Content-Security-Policy"] = PAGE_CSP
        return response

    @app.get("/health")
    def health():
        return {"ok": True, "run": str(run)}

    @app.get("/visualization/{path:path}")
    def visualization_file(path: str):
        return FileResponse(_safe_visualization_file(run, path))

    @app.get("/v1/runs/{run_id}/files/{file:path}")
    def result_page(run_id: str, file: str):
        if not file.endswith(".html"):
            raise HTTPException(status_code=404, detail="result page unavailable")
        return FileResponse(Path(__file__).parent / "verification" / "nib_verify" / "server" / "static" / "index.html")

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(build_app(args.run), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
