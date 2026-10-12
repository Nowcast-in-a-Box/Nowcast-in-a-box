"""Run manifest writer (VIS-006).

Builds the machine-readable run manifest defined by the main plan ``§5
验收证据与 manifest`` (the single field definition; this module never restates
a second schema). The manifest records what *actually happened in one run*:

- input digests, spatial/temporal semantics and resolved render parameters are
  read from the validated field and the prepared scene;
- output checksums and the GIF frame count are computed here from the written
  files, never copied from caller claims;
- basemap identity comes from the actually selected provider/loader and its
  source record; an explicitly mismatched record raises instead of producing a
  manifest with a wrong attribution or source identity;
- WMO checksums re-verify every file listed in the mirror's SHA256SUMS.txt in
  this run; OCHA records the actual SHA-256 of the GeoPackage;
- automated checks are only embedded when they were actually executed and
  passed; the visual review entry stays explicitly ``pending`` until a real
  review is recorded — generating files never implies a visual PASS.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
from datetime import UTC, datetime
from math import ceil, floor
from pathlib import Path

import numpy as np
from PIL import Image

from nib_visualization.basemap import CompositeBasemap, LocalBasemap
from nib_visualization.field import VisualizationField
from nib_visualization.render import PreparedScene
from nib_visualization.wmo_basemap import WmoBasemap

MANIFEST_VERSION = "1"

#: packages whose exact versions are recorded in every manifest
KEY_PACKAGES = (
    "cartopy",
    "geopandas",
    "matplotlib",
    "netCDF4",
    "nib-visualization",
    "numpy",
    "pillow",
    "pyogrio",
    "pyproj",
    "shapely",
    "xarray",
)

_MISSING_STYLE = (
    "missing values render fully transparent via the colormap bad color; "
    "valid zeros remain real numeric zeros"
)
_COORD_CONVENTION = (
    "x/y are regular cell centres; pcolormesh cell edges are adjacent-centre "
    "midpoints with half-cell extrapolated ends"
)
_GEOGRAPHIC_EXTENT_ORDER = "west, east, south, north (EPSG:4326)"
_VALUES_DIGEST_METHOD = (
    "SHA-256 over the little-endian float64 C-contiguous bytes of the "
    "(lead_time, y, x) values array in stored axis order"
)
_COORDS_DIGEST_METHOD = (
    "SHA-256 over the concatenated little-endian bytes of lead_time (int64 ns), "
    "y (float64) and x (float64) coordinate arrays in that order"
)


def sha256_file(path: Path | str) -> str:
    """SHA-256 hex digest of one file's actual bytes."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError as err:
        raise ValueError(f"cannot hash file {path}: {err}") from err
    return digest.hexdigest()


def _digest_array(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array, dtype="<f8").tobytes()).hexdigest()


def digest_field_inputs(field: VisualizationField) -> dict:
    """Repeatable digests of the field's values and coordinates.

    Both digests only cover numeric content, so the same synthetic input yields
    the same digests across runs regardless of audit timestamps.
    """
    values = field.values
    lead_ns = values.coords["lead_time"].values.astype("timedelta64[ns]").astype("<i8")
    coords_bytes = b"".join(
        [
            np.ascontiguousarray(lead_ns).tobytes(),
            np.ascontiguousarray(values.coords["y"].values, dtype="<f8").tobytes(),
            np.ascontiguousarray(values.coords["x"].values, dtype="<f8").tobytes(),
        ]
    )
    return {
        "algorithm": "sha256",
        "values": _digest_array(values.values),
        "coords": hashlib.sha256(coords_bytes).hexdigest(),
        "values_method": _VALUES_DIGEST_METHOD,
        "coords_method": _COORDS_DIGEST_METHOD,
    }


def _iso_utc(value: np.datetime64) -> str:
    return f"{np.datetime_as_string(np.datetime64(value, 's'), unit='s')}Z"


def _gif_frame_count(path: Path) -> int:
    with Image.open(path) as image:
        return int(getattr(image, "n_frames", 1))


def verify_wmo_checksum_manifest(cache_root: Path | str) -> dict:
    """Actually re-verify every file listed in the WMO mirror's SHA256SUMS.txt.

    Hashes each listed file from its bytes and compares against the listed
    digest, so the manifest records a verification executed in this run rather
    than the acquisition-time result.
    """
    root = Path(cache_root)
    manifest_path = root / "SHA256SUMS.txt"
    if not manifest_path.is_file():
        raise ValueError(f"WMO checksum manifest missing: {manifest_path}")
    verified = 0
    mismatched: list[str] = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        expected, _separator, relative = line.partition("  ")
        target = root / relative.strip()
        if not target.is_file():
            mismatched.append(f"{relative.strip()} (missing)")
            continue
        if sha256_file(target) != expected:
            mismatched.append(relative.strip())
        else:
            verified += 1
    return {
        "checksum_object": "SHA256SUMS.txt",
        "checksum_algorithm": "sha256",
        "manifest_sha256": sha256_file(manifest_path),
        "listed_files": verified + len(mismatched),
        "verified": verified,
        "mismatched": mismatched,
        "method": "sha256 of every listed file recomputed from bytes in this run and compared",
    }


def axes_pixel_box(figure, axes) -> tuple[int, int, int, int]:
    """Axes interior in saved image coordinates, for static-legend acceptance."""
    figure.canvas.draw()
    width, height = figure.canvas.get_width_height()
    x0, y0, x1, y1 = axes.get_window_extent().extents
    return (
        max(0, ceil(x0) + 2), max(0, ceil(height - y1) + 2),
        min(width, floor(x1) - 2), min(height, floor(height - y0) - 2),
    )


def inspect_outputs(
    map_path: Path | str, animation_path: Path | str, expected_frames: int, *,
    expected_duration_ms: int | None = None, colorbar_box=None,
) -> list[dict]:
    """Decode the freshly written PNG/GIF and report what was actually found.

    Returns the check results for the manifest and raises ``ValueError`` on any
    mismatch, so a failing output never reaches a successful manifest.
    """
    checks: list[dict] = []
    with Image.open(map_path) as png:
        png.load()
        png_ok = png.format == "PNG"
        png_size = png.size
    if not png_ok:
        raise ValueError(f"map output is not a decodable PNG: {map_path}")
    checks.append(
        {
            "name": "map_png_decodes",
            "result": "pass",
            "detail": f"{png_size[0]}x{png_size[1]}",
        }
    )

    with Image.open(animation_path) as gif:
        gif_ok = gif.format == "GIF"
        gif_size = gif.size
        frames = int(getattr(gif, "n_frames", 1))
        bars = set()
        for index in range(frames):
            gif.seek(index)
            gif.load()
            if (
                expected_duration_ms is not None
                and gif.info.get("duration") != expected_duration_ms
            ):
                raise ValueError(f"GIF duration mismatch at frame {index}")
            if colorbar_box is not None:
                with gif.convert("RGB") as rgb:
                    bars.add(hashlib.sha256(rgb.crop(colorbar_box).tobytes()).hexdigest())
    if not gif_ok:
        raise ValueError(f"animation output is not a decodable GIF: {animation_path}")
    checks.append(
        {
            "name": "animation_gif_decodes",
            "result": "pass",
            "detail": f"{frames} frames, {gif_size[0]}x{gif_size[1]}",
        }
    )
    if frames != expected_frames:
        raise ValueError(
            f"animation GIF has {frames} frames but the field has {expected_frames} leads"
        )
    checks.append(
        {
            "name": "gif_frame_count_matches_leads",
            "result": "pass",
            "detail": f"{frames} == {expected_frames}",
        }
    )
    if colorbar_box is not None:
        if len(bars) != 1:
            raise ValueError(f"GIF colorbar changed across {len(bars)} decoded versions")
        checks.append({
            "name": "static_colorbar_across_gif", "result": "pass",
            "detail": f"one pixel-identical colorbar across {frames} fully decoded frames",
        })
    if expected_duration_ms is not None:
        checks.append({
            "name": "gif_frame_duration", "result": "pass",
            "detail": f"{expected_duration_ms} ms for each decoded frame",
        })
    return checks


def _load_record_json(source_record_path: Path) -> dict:
    """Parse a source record for identity cross-checks.

    Deliberately not ``basemap.load_source_record``: that structural contract
    (a ``layers`` mapping) is specific to OCHA GeoPackage records, while the
    WMO record carries item ids instead.
    """
    try:
        return json.loads(Path(source_record_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ValueError(f"source record unreadable at {source_record_path}: {err}") from err


def verify_source_identity(
    provider: str, basemap: object, source_record_path: Path | str
) -> dict:
    """Cross-check the selected loader, provider name and source record.

    The provider identity comes from the loader that was actually invoked, and
    a WMO source record must carry the same webmap/vector-tile item ids as the
    cache's own acquisition record. An explicitly mismatched record raises
    instead of producing a manifest with a wrong attribution. Callers that
    render before writing a manifest should verify *before* rendering, so a
    mismatched record never leaves wrongly-attributed artifacts behind.
    """
    if isinstance(basemap, CompositeBasemap):
        basemap = basemap.vector
    if provider == "wmo":
        if not isinstance(basemap, WmoBasemap):
            raise ValueError(
                "provider 'wmo' requires a basemap opened by load_wmo_basemap, "
                f"got {type(basemap).__name__}"
            )
        record = _load_record_json(source_record_path)
        cache_acquisition = json.loads(
            (Path(basemap.path) / "acquisition.json").read_text(encoding="utf-8")
        )
        if "source_id" not in record:
            raise ValueError(f"WMO source record {source_record_path} lacks 'source_id'")
        # the in-cache acquisition record names the webmap id "source_webmap_item_id";
        # the committable acquisition summary names it "webmap_item_id"
        cache_ids = {
            "webmap_item_id": cache_acquisition.get(
                "webmap_item_id", cache_acquisition.get("source_webmap_item_id")
            ),
            "vector_tile_item_id": cache_acquisition.get("vector_tile_item_id"),
        }
        for key in ("webmap_item_id", "vector_tile_item_id"):
            if key not in record:
                raise ValueError(
                    f"WMO source record {source_record_path} lacks '{key}'; a record that "
                    "cannot be bound to this WMO cache must not produce a manifest"
                )
            if record[key] != cache_ids[key]:
                raise ValueError(
                    f"source record {source_record_path} {key}={record[key]!r} does not match "
                    f"the WMO cache acquisition record ({cache_ids[key]!r})"
                )
        return {
            "source_id": record["source_id"],
            "title": record.get("title"),
            "source_date": cache_acquisition.get("acquired_at_utc"),
        }
    if provider == "ocha":
        if not isinstance(basemap, LocalBasemap):
            raise ValueError(
                "provider 'ocha' requires a basemap opened by load_basemap, "
                f"got {type(basemap).__name__}"
            )
        record = _load_record_json(source_record_path)
        if "source_id" not in record:
            raise ValueError(f"OCHA source record {source_record_path} lacks 'source_id'")
        return {
            "source_id": record["source_id"],
            "title": record.get("title"),
            "source_date": record.get("verified_at"),
        }
    raise ValueError(f"provider must be 'ocha' or 'wmo', got {provider!r}")


def _basemap_section(
    provider: str,
    basemap: LocalBasemap | WmoBasemap | CompositeBasemap,
    scene: PreparedScene,
    source_record_path: Path,
    acquisition_record_path: Path,
) -> dict:
    if isinstance(basemap, CompositeBasemap):
        section = _basemap_section(
            provider, basemap.vector, scene, source_record_path, acquisition_record_path
        )
        section["raster"] = dict(basemap.raster.metadata)
        section["raster"]["views"] = [
            {
                "extent_wgs84": list(view.raster.extent_wgs84),
                "display_shape": list(view.raster.values.shape),
                "color_stops": view.raster.color_stops,
            }
            for view in scene.basemap_views if view.raster is not None
        ]
        return section
    identity = verify_source_identity(provider, basemap, source_record_path)
    layer_counts: dict[str, int] = {}
    for view in scene.basemap_views:
        for name, layer in view.layers.items():
            layer_counts[name] = layer_counts.get(name, 0) + len(layer)

    section: dict = {
        "provider": provider,
        "cache_kind": "vector_tile_mirror" if provider == "wmo" else "geopackage",
        "cache_path": str(Path(basemap.path)),
        "source_id": identity["source_id"],
        "title": identity["title"],
        "source_date": identity["source_date"],
        "layers": [
            {"name": name, "feature_count_in_view": count}
            for name, count in sorted(layer_counts.items())
        ],
        "attribution": basemap.attribution,
        "disclaimer": basemap.disclaimer,
        "attribution_boundary": (
            "attribution/disclaimer shown in the figure footer come from the source "
            "record; public redistribution or packaging of the third-party cache is "
            "not asserted by this manifest"
        ),
        "source_record": {
            "path": str(source_record_path),
            "sha256": sha256_file(source_record_path),
        },
        "acquisition_record": {
            "path": str(acquisition_record_path),
            "sha256": sha256_file(acquisition_record_path),
        },
    }

    if provider == "wmo":
        verification = verify_wmo_checksum_manifest(basemap.path)
        if verification["mismatched"]:
            raise ValueError(
                "WMO cache checksum verification failed for "
                f"{len(verification['mismatched'])} listed file(s): "
                f"{verification['mismatched'][:5]}"
            )
        section["checksum"] = verification
        section["wmo"] = {
            "cache_max_lod": basemap.max_lod,
            "lod_selection": (
                "LOD estimated from the viewport width for a ~1000 px output canvas "
                "and clamped to cache_max_lod (wmo_basemap crop implementation)"
            ),
            "parent_fallback": (
                "missing tiles fall back to the nearest physical ancestor tile "
                "(ArcGIS resample semantics)"
            ),
            "rendered_lod": {
                "requested": None,
                "effective": None,
                "status": "not_recorded",
                "note": (
                    "crop_basemap does not expose the LOD chosen for this crop; "
                    "cache_max_lod is deliberately not substituted for the rendered LOD"
                ),
            },
            "geometry_only": (
                "renders ocean/land/boundary geometry only; the official style's "
                "place-name labels and the separate disclaimer raster layer are not reproduced"
            ),
        }
    else:
        section["checksum"] = {
            "object": str(Path(basemap.path)),
            "algorithm": "sha256",
            "value": sha256_file(basemap.path),
            "method": "sha256 of the single-file GeoPackage cache computed in this run",
        }
    return section


def _environment_section(command: list[str], lockfile_path: Path | None) -> dict:
    package_root = Path(__file__).resolve().parent
    lockfile = lockfile_path or Path("requirements-dev.lock.txt")
    return {
        "command": list(command),
        "python": platform.python_version(),
        "packages": {
            name: importlib.metadata.version(name) for name in KEY_PACKAGES if _installed(name)
        },
        "lockfile": {"path": str(lockfile), "sha256": sha256_file(lockfile)},
        "code_digests": {
            "package_root": str(package_root),
            "files": {
                path.relative_to(package_root).as_posix(): sha256_file(path)
                for path in sorted(package_root.rglob("*.py"))
            },
        },
    }


def _installed(name: str) -> bool:
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def write_manifest(
    field: VisualizationField,
    scene: PreparedScene,
    *,
    provider: str,
    basemap: LocalBasemap | WmoBasemap | CompositeBasemap,
    source_record_path: Path | str,
    acquisition_record_path: Path | str,
    outputs: dict[str, Path | str],
    command: list[str],
    fps: float,
    checks: list[dict],
    visual_review: dict | None = None,
    lockfile_path: Path | str | None = None,
) -> dict:
    """Build and write one run manifest; return the manifest dict.

    ``outputs`` needs ``map``/``animation``/``manifest`` paths. ``checks`` are
    the automated checks actually executed for this run (e.g. from
    :func:`inspect_outputs`); any non-pass entry aborts the manifest. The
    manifest never contains its own checksum.
    """
    failed = [c for c in checks if c.get("result") != "pass"]
    if failed:
        raise ValueError(f"refusing to write a manifest with non-pass checks: {failed}")

    map_path = Path(outputs["map"])
    animation_path = Path(outputs["animation"])
    manifest_path = Path(outputs["manifest"])

    values = field.values
    n_leads = int(values.sizes["lead_time"])
    lead_seconds = (values.coords["lead_time"].values / np.timedelta64(1, "s")).astype("<i8")
    # the contract guarantees valid_time == init_time + lead_time per lead (a
    # provided array is checked item-by-item by validate_field), so record the
    # field's own valid_time when present and the equivalent derivation otherwise
    valid_times = (
        field.valid_time
        if field.valid_time is not None
        else scene.init_time + values.coords["lead_time"].values
    )

    provenance = dict(field.provenance) if field.provenance else None
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "input": {
            "profile": "Visualization-local deterministic gridded field (lead_time, y, x)",
            "synthetic": bool(provenance and provenance.get("synthetic")),
            "mock": bool(provenance and provenance.get("mock")),
            "fixture": (
                {
                    "grid": provenance.get("grid"),
                    "n_leads": n_leads,
                    "generator": provenance.get("generator"),
                    "fixture_version": provenance.get("fixture_version"),
                }
                if provenance and provenance.get("synthetic")
                else None
            ),
            "digest": digest_field_inputs(field),
            "provenance": provenance,
        },
        "space": {
            "data_crs": scene.data_crs.to_string(),
            "display_crs": f"PlateCarree(central_longitude={scene.central_longitude})",
            "field_extent_wgs84": list(scene.field_footprint),
            "viewport_extent_wgs84": list(scene.viewport),
            "crop_extents_wgs84": [list(extent) for extent in scene.crop_extents_wgs84],
            "buffer_fraction": scene.config.buffer_fraction,
            "extent_order": _GEOGRAPHIC_EXTENT_ORDER,
            "coord_convention": _COORD_CONVENTION,
        },
        "variable": {
            "name": scene.variable_name,
            "units": scene.units,
            "temporal_statistic": scene.temporal_statistic,
        },
        "time": {
            "init_time_utc": _iso_utc(scene.init_time),
            "interval": (
                None
                if scene.interval_start is None
                else {
                    "start_utc": [_iso_utc(t) for t in scene.interval_start],
                    "end_utc": [_iso_utc(t) for t in scene.interval_end],
                }
            ),
            "frames": [
                {
                    "index": index,
                    "lead_time_seconds": int(seconds),
                    "valid_time_utc": _iso_utc(valid_times[index]),
                }
                for index, seconds in enumerate(lead_seconds)
            ],
        },
        "render": {
            "colormap": scene.cmap.name,
            "resolved_colormap": (
                f"{scene.cmap.name} with a fully transparent bad color for missing values"
            ),
            "colormap_rgba_sha256": _digest_array(scene.cmap(np.linspace(0, 1, scene.cmap.N))),
            "display_min": scene.config.display_min,
            "notice": scene.config.notice,
            "colorbar_extend": scene.config.colorbar_extend,
            "vmin": float(scene.norm.vmin),
            "vmax": float(scene.norm.vmax),
            "missing_style": _MISSING_STYLE,
            "figure_size": [float(size) for size in scene.config.figsize],
            "dpi": int(scene.config.dpi),
            "fps": float(fps),
            "frame_count": _gif_frame_count(animation_path),
        },
        "basemap": _basemap_section(
            provider,
            basemap,
            scene,
            Path(source_record_path),
            Path(acquisition_record_path),
        ),
        "outputs": {
            "map_png": {
                "path": os.path.relpath(map_path, manifest_path.parent),
                "media_type": "image/png",
                "sha256": sha256_file(map_path),
            },
            "animation_gif": {
                "path": os.path.relpath(animation_path, manifest_path.parent),
                "media_type": "image/gif",
                "sha256": sha256_file(animation_path),
            },
        },
        "environment": _environment_section(command, lockfile_path),
        "checks": {
            "automated": list(checks),
            "visual_review": visual_review
            or {
                "status": "pending",
                "note": (
                    "visual review of this run's artifacts has not been recorded yet; "
                    "artifact generation alone does not assert a visual PASS"
                ),
            },
        },
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return manifest
