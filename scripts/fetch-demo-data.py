#!/usr/bin/env python3
"""Fetch the adapter demo samples into gitignored testdata/demo-data/.

Samples live in a separate Git LFS repository so this one stays small. This
script is the only thing that touches the network; tests read whatever is on
disk and skip when it is absent.

  scripts/fetch-demo-data.py            # download or repair
  scripts/fetch-demo-data.py --check    # verify only, exit 1 when incomplete
  scripts/fetch-demo-data.py --dest DIR # download somewhere else
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "Nowcast-in-a-Box/nib-data-samples"
REV = "172d4fc85e0abba5a7c3a047617ef6288692f373"
DEFAULT_DEST = ROOT / "testdata" / "demo-data"
MANIFEST = f"https://raw.githubusercontent.com/{REPO}/{REV}/manifest.json"
MEDIA = f"https://media.githubusercontent.com/media/{REPO}/{REV}"

# A mirror can serve manifest and files from one root, e.g. a local
# `python3 -m http.server` over a clone of the samples repository.
MIRROR = os.environ.get("NIB_DEMO_DATA_BASE", "").rstrip("/")


def manifest_url() -> str:
    return f"{MIRROR}/manifest.json" if MIRROR else MANIFEST


def file_url(path: str) -> str:
    return f"{MIRROR}/{path}" if MIRROR else f"{MEDIA}/{path}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest() -> dict:
    request = urllib.request.Request(
        manifest_url(), headers={"User-Agent": "nib-fetch-demo-data"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def download(path: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    request = urllib.request.Request(
        file_url(path), headers={"User-Agent": "nib-fetch-demo-data"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        with partial.open("wb") as handle:
            shutil.copyfileobj(response, handle, 1 << 20)
    partial.replace(target)


def verify(dest: Path, manifest: dict) -> list[str]:
    broken = []
    for entry in (f for d in manifest["datasets"].values() for f in d["files"]):
        path = dest / entry["path"]
        if not path.is_file() or path.stat().st_size != entry["bytes"]:
            broken.append(entry["path"])
        elif sha256(path) != entry["sha256"]:
            broken.append(entry["path"])
    return broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--check", action="store_true", help="verify without downloading")
    args = parser.parse_args(argv)
    dest: Path = args.dest

    manifest = read_manifest()
    entries = [f for d in manifest["datasets"].values() for f in d["files"]]

    if args.check:
        broken = verify(dest, manifest)
        if broken:
            print(f"{len(broken)} of {len(entries)} samples missing or changed in {dest}")
            return 1
        print(f"{len(entries)} samples verified in {dest}")
        return 0

    marker = dest / ".rev"
    if marker.is_file() and marker.read_text().strip() != REV and dest.is_dir():
        shutil.rmtree(dest)
    for entry in entries:
        target = dest / entry["path"]
        if target.is_file() and target.stat().st_size == entry["bytes"]:
            if sha256(target) == entry["sha256"]:
                continue
        print(f"fetching {entry['path']}")
        download(entry["path"], target)

    broken = verify(dest, manifest)
    if broken:
        print(f"checksum mismatch after download: {broken}", file=sys.stderr)
        return 1
    marker.write_text(REV + "\n")
    total = sum(f["bytes"] for f in entries)
    print(f"{len(entries)} samples, {total / 1e6:.1f} MB, ready in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
