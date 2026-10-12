"""Acquire the global basemap master from the verified UN-derived source (VIS-001).

Reads ``basemap_sources/selected-source.json``, exports the configured global
layers from the OCHA-hosted ArcGIS Feature Service (UN Geodata Simplified
lineage) via REST queries in WGS84, verifies per-layer completeness against
the service's own feature counts, converts to a single GeoPackage and records
provenance, hashes and counts in ``basemap_sources/acquisition.json``.

Network access happens ONLY in this script; the ``nib_visualization`` package
itself is fully offline.

Usage:
    python scripts/acquire_basemap.py \
        --source basemap_sources/selected-source.json \
        --output data/cache/basemap/global.gpkg
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd

USER_AGENT = "nib-visualization/0.1 (VIS-001 local development acquisition)"
PAGE_SIZE = 2000  # service maxRecordCount observed 2026-09-22


class AcquisitionError(RuntimeError):
    """Raised when the service response fails completeness or structure checks."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_json(base_url: str, params: dict[str, str], timeout: int = 120) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{base_url}?{query}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, dict) and "error" in payload:
        raise AcquisitionError(f"service error from {base_url}: {payload['error']}")
    return payload


def official_count(service_url: str, layer_id: int) -> int:
    payload = fetch_json(
        f"{service_url}/{layer_id}/query",
        {"where": "1=1", "returnCountOnly": "true", "f": "json"},
    )
    count = payload.get("count")
    if not isinstance(count, int):
        raise AcquisitionError(f"no count returned for layer {layer_id}: {payload}")
    return count


def object_id_field(service_url: str, layer_id: int) -> str:
    payload = fetch_json(f"{service_url}/{layer_id}", {"f": "pjson"})
    field = payload.get("objectIdField")
    if not field:
        raise AcquisitionError(f"layer {layer_id} exposes no objectIdField")
    return field


def export_layer(service_url: str, layer_id: int, order_field: str) -> tuple[list[dict], int]:
    """Paged geojson export; returns (features, pages) ordered by the OID field."""
    features: list[dict] = []
    pages = 0
    while True:
        payload = fetch_json(
            f"{service_url}/{layer_id}/query",
            {
                "where": "1=1",
                "outFields": "*",
                "f": "geojson",
                "outSR": "4326",
                "resultOffset": str(len(features)),
                "resultRecordCount": str(PAGE_SIZE),
                "orderByFields": order_field,
            },
        )
        page = payload.get("features", [])
        pages += 1
        features.extend(page)
        if len(page) < PAGE_SIZE:
            break
    return features, pages


def check_unique_ids(features: list[dict], id_field: str) -> int:
    ids = [feature["properties"].get(id_field) for feature in features]
    unique = len(set(ids))
    if unique != len(ids):
        raise AcquisitionError(
            f"duplicate ids in '{id_field}': {len(ids)} rows, {unique} unique"
        )
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        default="basemap_sources/selected-source.json",
        help="path to the selected-source.json record",
    )
    parser.add_argument(
        "--output",
        default="data/cache/basemap/global.gpkg",
        help="path of the GeoPackage master to write",
    )
    parser.add_argument(
        "--raw-dir",
        default=None,
        help="directory for raw exported geojson evidence (default: <output>/../raw)",
    )
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    record = json.loads(Path(args.source).read_text(encoding="utf-8"))
    service_url = record["service_url"]
    output_path = Path(args.output)
    raw_dir = Path(args.raw_dir) if args.raw_dir else output_path.parent / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    access_date = datetime.now(UTC).date().isoformat()
    layer_records: dict[str, dict] = {}

    for name, spec in record["layers"].items():
        layer_id = spec["service_layer_id"]
        gpkg_layer = spec.get("gpkg_layer", name)
        print(f"[{name}] layer {layer_id}: fetching metadata + official count ...")

        count = official_count(service_url, layer_id)
        oid_field = object_id_field(service_url, layer_id)
        features, pages = export_layer(service_url, layer_id, oid_field)
        unique = check_unique_ids(features, oid_field)
        if unique != count:
            raise AcquisitionError(
                f"layer {name}: collected {unique} unique features but service count "
                f"is {count}; refusing to write an incomplete master"
            )

        raw_path = raw_dir / f"{name}.geojson"
        raw_path.write_text(
            json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
            encoding="utf-8",
        )

        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        gdf.to_file(output_path, layer=gpkg_layer, driver="GPKG")

        layer_record = {
            "service_layer_id": layer_id,
            "service_layer_name": spec.get("service_layer_name"),
            "gpkg_layer": gpkg_layer,
            "official_count": count,
            "collected_count": unique,
            "pages": pages,
            "page_size": PAGE_SIZE,
            "order_field": oid_field,
            "raw_file": str(raw_path),
            "raw_sha256": sha256_file(raw_path),
            "features_written": len(gdf),
            "crs": "EPSG:4326",
            "bounds_wgs84": [round(v, 6) for v in gdf.total_bounds],
        }
        type_field = spec.get("type_field")
        if type_field and type_field in gdf.columns:
            layer_record["type_distribution_observed"] = {
                str(k): int(v) for k, v in gdf[type_field].value_counts().items()
            }
        layer_records[name] = layer_record
        print(f"[{name}] OK: {unique}/{count} features, {pages} page(s), bounds "
              f"{layer_record['bounds_wgs84']}")

    lines = gpd.read_file(output_path, layer=record["layers"]["boundary_lines"]["gpkg_layer"])
    bounds = lines.total_bounds
    near_global = bool(
        (bounds[2] - bounds[0]) > 300 and bounds[3] > 70 and bounds[1] < -50
    )

    acquisition = {
        "acquired_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_id": record["source_id"],
        "service_url": service_url,
        "item_info_url": record.get("item_info_url"),
        "source_record_path": str(Path(args.source)),
        "access_date": access_date,
        "output_gpkg": str(output_path),
        "output_gpkg_sha256": sha256_file(output_path),
        "conversion": {
            "method": (
                "ArcGIS REST query (f=geojson, outSR=4326, orderByFields=<objectIdField>, "
                "resultOffset pagination) -> geopandas.GeoDataFrame.from_features -> "
                "GeoPackage via geopandas to_file (pyogrio)"
            ),
            "commands": [
                f"python scripts/acquire_basemap.py --source {args.source} --output {args.output}"
            ],
            "geopandas_version": gpd.__version__,
        },
        "layers": layer_records,
        "completeness": {
            "rule": (
                "per layer, official returnCountOnly count == unique collected ids "
                "== features written"
            ),
            "passed": all(
                rec["official_count"] == rec["collected_count"] == rec["features_written"]
                for rec in layer_records.values()
            ),
        },
        "observed_global_coverage": {
            "boundary_lines_bounds_wgs84": [round(v, 6) for v in bounds],
            "near_global": near_global,
            "check": "lon span > 300 deg and lat max > 70 and lat min < -50 (generalized dataset)",
        },
    }
    if not acquisition["completeness"]["passed"]:
        raise AcquisitionError("completeness check failed; not writing acquisition.json")

    acquisition_path = Path(args.source).parent / "acquisition.json"
    acquisition_path.write_text(
        json.dumps(acquisition, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"acquisition record written: {acquisition_path}")
    print(f"global master written: {output_path} (sha256 {acquisition['output_gpkg_sha256']})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AcquisitionError as err:
        print(f"acquisition failed: {err}", file=sys.stderr)
        sys.exit(1)
