"""Source-specific orchestration through the shared field/render/animation APIs."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap

from nib_visualization.adapters.cma_mock import read_cma_mock
from nib_visualization.animation import write_animation
from nib_visualization.artifacts import (
    axes_pixel_box,
    inspect_outputs,
    sha256_file,
    verify_source_identity,
    write_manifest,
)
from nib_visualization.basemap import CompositeBasemap
from nib_visualization.render import RenderConfig, prepare_scene, render_frame
from nib_visualization.terrain import load_etopo
from nib_visualization.wmo_basemap import load_wmo_basemap

DEFAULT_INPUT = Path("data/radar_data_example_npy/shared_numpy")
DEFAULT_TERRAIN = Path("data/cache/terrain/etopo2022/cma-domain.nc")
DEFAULT_TERRAIN_RECORD = Path("basemap_sources/etopo2022-source.json")
DEFAULT_CROP_RECORD = Path("basemap_sources/etopo2022-cma-preview.json")
DEFAULT_COLOR_FILE = Path("data/02_雷达拼图读取_看输入输出，绘制回波图/color_map_v202408.json")


def reflectivity_colormap(path: Path) -> ListedColormap:
    """Use the supplied uniform 0.5 dBZ REF bins with Core's fixed normalization."""
    record = json.loads(path.read_text(encoding="utf-8"))["REF"]
    all_levels = np.asarray(record["levels"], dtype=float)
    levels = all_levels[(all_levels >= 10) & (all_levels <= 75)]
    if not np.array_equal(levels, np.arange(10, 75.5, 0.5)):
        raise ValueError("CMA demo requires the supplied uniform REF bins from 10 to 75 dBZ")
    first = int(np.flatnonzero(all_levels == 10)[0])
    return ListedColormap(
        record["colors"][first:first + len(levels) - 1], name="CMA_REF_10_75"
    ).with_extremes(over=record["colors"][-1])


def run_cma_mock(
    *, input_dir: Path, output_dir: Path, basemap_path: Path, source_record: Path,
    acquisition_record: Path, terrain_path: Path, terrain_record: Path,
    crop_record: Path, color_file: Path, display_width: int, fps: float, command: list[str],
) -> dict:
    """Render every supplied observation time, without a fixed frame limit."""
    if not math.isfinite(fps) or not 0 < fps <= 100:
        raise ValueError("GIF fps must be finite, positive and at most 100 (10 ms precision)")
    vector = load_wmo_basemap(basemap_path, source_record)
    verify_source_identity("wmo", vector, source_record)
    cache_record = json.loads(crop_record.read_text())
    terrain = load_etopo(
        terrain_path, terrain_record, expected_sha256=cache_record["crop"]["sha256"]
    )
    terrain.metadata["cache_record"] = {
        "path": str(crop_record.resolve()), "sha256": sha256_file(crop_record),
    }
    cmap = reflectivity_colormap(color_file)
    print(f"Reading all CMA observation times from {input_dir} ...", flush=True)
    field = read_cma_mock(input_dir, crs="EPSG:4326", display_width=display_width)
    field = replace(field, provenance={
        **field.provenance,
        "color_file": {"path": str(color_file.resolve()), "sha256": sha256_file(color_file)},
    })
    n_leads = int(field.values.sizes["lead_time"])
    print(f"Rendering {n_leads} frames through Core; display grid {field.values.shape[1:]} ...",
          flush=True)
    basemap = CompositeBasemap(vector, terrain)
    scene = prepare_scene(field, RenderConfig(
        cmap=cmap, vmin=10, vmax=75, buffer_fraction=0, figsize=(12, 9),
        field_alpha=0.96, display_min=10, colorbar_extend="max",
        notice="MOCK FORECAST — observed radar sequence, not a forecast",
    ), basemap)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = {
            "map": output_dir / "map.png", "animation": output_dir / "animation.gif",
            "manifest": output_dir / "manifest.json",
        }
        render_frame(scene, field, 0).savefig(outputs["map"], dpi=scene.config.dpi)
        bar_box = axes_pixel_box(scene.figure, scene.colorbar.ax)
        write_animation(scene, field, outputs["animation"], fps=fps)
        # GIF stores centiseconds, truncating any fractional 10 ms interval.
        checks = inspect_outputs(
            outputs["map"], outputs["animation"], n_leads,
            expected_duration_ms=int(1000 / fps) // 10 * 10, colorbar_box=bar_box,
        )
        return write_manifest(
            field, scene, provider="wmo", basemap=basemap,
            source_record_path=source_record, acquisition_record_path=acquisition_record,
            outputs=outputs, command=command, fps=fps, checks=checks,
        )
    finally:
        plt.close(scene.figure)
