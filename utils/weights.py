"""Resolve a pinned weight file. A matching local sha256 skips download."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.request import urlopen

from interfaces.errors import WeightsNotReady
from interfaces.model import WeightSpec


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def huggingface_url(repo: str, revision: str, filename: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"


def artifacts_root() -> Path:
    env = os.environ.get("NIB_ARTIFACTS", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "artifacts"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    try:
        with urlopen(url, timeout=120) as response, partial.open("wb") as handle:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
        partial.replace(dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def ensure_weights(spec: WeightSpec, root: Path | None = None) -> Path:
    """Return a local file whose sha256 matches. Download only on a miss."""
    if not spec.sha256 or not spec.artifacts_relpath:
        raise WeightsNotReady(
            "weight pin is incomplete; "
            f"download_url={spec.download_url!r} sha256={spec.sha256!r}"
        )
    dest = (root or artifacts_root()) / spec.artifacts_relpath
    if dest.is_file() and file_sha256(dest) == spec.sha256:
        return dest

    url = spec.download_url
    if not url and spec.hf:
        url = huggingface_url(spec.hf["repo"], spec.hf["revision"], spec.hf["filename"])
    if not url:
        raise WeightsNotReady(
            "local weight sha256 did not match and download_url is null; "
            f"sha256={spec.sha256}"
        )
    try:
        _download(url, dest)
    except Exception as exc:
        raise WeightsNotReady(
            f"Hugging Face download failed: {exc}; download_url={url!r}"
        ) from exc
    if not dest.is_file() or file_sha256(dest) != spec.sha256:
        dest.unlink(missing_ok=True)
        raise WeightsNotReady(
            "downloaded weight sha256 does not match; "
            f"download_url={url!r} sha256={spec.sha256}"
        )
    return dest
