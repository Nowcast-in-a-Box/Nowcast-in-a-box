"""Build a real-forecast map bundle from stored fields and a prepared basemap cache.

Scientific NPZ inputs are read and hashed, never rewritten. Weather COGs are
display derivatives under the attempt directory. Shared terrain and WMO tiles
stay in the basemap cache and are referenced, not copied into the run.
"""

import json
import uuid
from pathlib import Path

import matplotlib
import numpy as np

from diagnostics.fields import display_grid, visualization_field
from nib_visualization.cma_demo import reflectivity_colormap
from nib_visualization.viewer_basemap import cache_relative
from nib_visualization.viewer_bundle import export_field_cogs
from utils.stored_fields import StoredFields, save_manifest, sha256

_VIS = Path(__file__).resolve().parent / "visualization"
_REF_COLORS = _VIS / "resources" / "cma_ref_colormap.json"
_MAP_PREFIX = "/map-basemap/"
_PREPARE = "diagnostics image basemap is missing or corrupt; pull the current diagnostics image"
_DEFAULT_NOTICE = "Stored model forecast; this map does not validate forecast skill."
_WORLD = [-180.0, -90.0, 180.0, 90.0]
DEFAULT_BASEMAP = Path("/basemap")


def visualization(
    directory, model_id, basemap_path=DEFAULT_BASEMAP, *, attempt_id=None
):
    """Write viewer.json and weather COGs, then record one succeeded attempt."""
    attempt_id = attempt_id or uuid.uuid4().hex
    if (
        not isinstance(attempt_id, str)
        or len(attempt_id) != 32
        or any(c not in "0123456789abcdef" for c in attempt_id)
    ):
        raise ValueError("invalid diagnostic attempt ID")
    source = StoredFields(directory)
    if model_id not in source.manifest["forecasts"]:
        raise ValueError("visualization selects a model without stored forecasts")
    original = visualization_field(directory, model_id)
    display = display_grid(original)
    shared = _load_shared_basemap(basemap_path)
    out = Path(directory) / "diagnostics" / "visualization" / attempt_id
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError as err:
        raise FileExistsError(
            f"visualization attempt already exists: {attempt_id}"
        ) from err
    bundle = export_field_cogs(display, out)
    names = source.manifest.get("model_names") or {}
    label = names.get(model_id) or model_id
    bundle.update(
        mock=False,
        is_real_forecast=True,
        title=f"{label} · {original.name}",
        notice=_notice(source.manifest),
        native_shape=[
            int(original.values.sizes["y"]),
            int(original.values.sizes["x"]),
        ],
        spatial_sampling=display.provenance.get("display_resampling", "native"),
        weather_style=_weather_style(display),
        map_extent=shared["map_extent"],
        terrain=shared["terrain"],
        boundaries=shared["boundaries"],
    )
    text = json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    (out / "viewer.json").write_text(text)
    access = StoredFields(directory)
    forecast = access.manifest["forecasts"][model_id]
    if sha256(access.root / forecast["path"]) != forecast["sha256"]:
        raise ValueError("forecast artifact changed during visualization")
    attempt = {
        "kind": "visualization",
        "id": attempt_id,
        "model_id": model_id,
        "sample_id": access.manifest["sample"].get("sample_id"),
        "status": "succeeded",
        "path": out.relative_to(Path(directory)).as_posix(),
        "entry": (out / "viewer.json").relative_to(Path(directory)).as_posix(),
    }
    access.manifest["diagnostics"].append(attempt)
    save_manifest(directory, access.manifest)
    return attempt


def _notice(manifest):
    raw = (manifest.get("sample") or {}).get("provenance", {}).get("notice")
    if (
        not isinstance(raw, str)
        or not raw.strip()
        or "observation replay" in raw.lower()
    ):
        return _DEFAULT_NOTICE
    return raw.strip()


def _weather_style(field):
    if field.units == "dBZ":
        cmap = reflectivity_colormap(_REF_COLORS)
        return {
            "min": 10,
            "max": 75,
            "step": 0.5,
            "colors": [_rgb(cmap(i)) for i in range(cmap.N)],
            "over": _rgb(cmap(2.0)),
        }
    cmap = matplotlib.colormaps["turbo"]
    return {
        "min": 0,
        "max": 255,
        "step": 1,
        "colors": [_rgb(cmap(i / 255.0)) for i in range(256)],
        "over": _rgb(cmap(1.0)),
    }


def _rgb(color):
    return [float(channel) for channel in np.asarray(color)[:3]]


def _load_shared_basemap(basemap_path):
    root = Path(basemap_path)
    if not root.is_dir():
        raise FileNotFoundError(_PREPARE)
    viewer = (root / "viewer").resolve()
    root_real = root.resolve()
    if root_real != viewer and root_real not in viewer.parents:
        raise ValueError(_PREPARE)
    manifest = (viewer / "basemap.json").resolve()
    if viewer not in manifest.parents:
        raise ValueError(f"{_PREPARE}: basemap.json escapes the cache")
    if not manifest.is_file():
        raise FileNotFoundError(_PREPARE)
    try:
        record = json.loads(manifest.read_text())
        terrain = dict(record["terrain"])
        boundaries = dict(record["boundaries"])
        extent = [float(value) for value in record["map_extent"]]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
        raise ValueError(f"{_PREPARE}: basemap.json is corrupt") from err
    if extent != _WORLD or not terrain.get("colors") or not boundaries.get("styles"):
        raise ValueError(f"{_PREPARE}: basemap.json is corrupt")
    terrain_url = _cached_file(viewer, terrain.get("url"))
    if sha256(viewer / terrain_url) != terrain.get("sha256"):
        raise ValueError(f"{_PREPARE}: terrain checksum mismatch")
    boundary_url = _cached_dir(viewer, boundaries.get("url"))
    tiles = boundaries.get("tiles") or {}
    keys = tiles.get("keys") or []
    if "0/0/0" not in keys:
        raise ValueError(f"{_PREPARE}: global parent tile is missing")
    for key in keys:
        _cached_file(viewer, f"{boundary_url}/{key}.pbf")
    terrain["url"] = _MAP_PREFIX + terrain_url
    boundaries["url"] = _MAP_PREFIX + boundary_url
    for layer in (terrain, boundaries):
        for leaked in ("path", "source_record", "local_path"):
            layer.pop(leaked, None)
    return {"map_extent": extent, "terrain": terrain, "boundaries": boundaries}


def _cached_file(viewer, url):
    relative = cache_relative(url)
    path = (viewer / relative).resolve()
    if viewer not in path.parents:
        raise ValueError(f"{_PREPARE}: basemap asset escapes the cache")
    if not path.is_file():
        raise FileNotFoundError(f"{_PREPARE}: basemap asset is missing")
    return relative


def _cached_dir(viewer, url):
    relative = cache_relative(url)
    path = (viewer / relative).resolve()
    if viewer not in path.parents:
        raise ValueError(f"{_PREPARE}: basemap asset escapes the cache")
    if not path.is_dir():
        raise FileNotFoundError(f"{_PREPARE}: basemap asset is missing")
    return relative
