"""Package a prepared display basemap, not raw ETOPO/WMO acquisition data."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def package(source: Path, output: Path):
    source = source.resolve()
    viewer = source / "viewer"
    bundle = json.loads((viewer / "basemap.json").read_text())
    terrain = bundle["terrain"]
    boundaries = bundle["boundaries"]

    def asset(relative):
        path = (viewer / relative).resolve()
        if not path.is_relative_to(viewer) or not path.is_file():
            raise ValueError(f"missing or escaping basemap asset: {relative}")
        return path

    terrain_file = asset(terrain["url"])
    if hashlib.sha256(terrain_file.read_bytes()).hexdigest() != terrain["sha256"]:
        raise ValueError("terrain checksum mismatch")
    keys = boundaries["tiles"]["keys"]
    if "0/0/0" not in keys:
        raise ValueError("WMO parent tile is missing")
    files = [asset("basemap.json"), terrain_file]
    files.extend(asset(f"{boundaries['url']}/{key}.pbf") for key in keys)
    # Preserve original source attribution/disclaimers, not an inferred MIT license.
    metadata = [
        "acquisition.json",
        "vector-item.json",
        "webmap-item.json",
        "webmap-data.json",
    ]
    for name in metadata:
        if not (source / name).is_file():
            raise ValueError(f"missing source provenance: {name}")
    output.mkdir(parents=True, exist_ok=False)
    for path in files:
        target = output / "viewer" / path.relative_to(viewer)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for name in metadata:
        target = output / "metadata" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    notices = {
        "terrain_attribution": terrain["attribution"],
        "terrain_disclaimer": terrain["disclaimer"],
        "wmo_attribution": json.loads((source / "vector-item.json").read_text())[
            "accessInformation"
        ],
        "wmo_terms": json.loads((source / "vector-item.json").read_text())[
            "licenseInfo"
        ],
        "scope": "Derived display assets. No endorsement or forecast-skill claim. Third-party source terms are separate from NiB code licensing.",
    }
    (output / "metadata" / "notices.json").write_text(
        json.dumps(notices, indent=2) + "\n"
    )
    manifest = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            manifest.append(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output).as_posix()}\n"
            )
    (output / "SHA256SUMS").write_text("".join(manifest))
    print(
        json.dumps(
            {
                "files": len(manifest),
                "tiles": len(keys),
                "bytes": sum(
                    p.stat().st_size for p in output.rglob("*") if p.is_file()
                ),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    package(args.source, args.output)
