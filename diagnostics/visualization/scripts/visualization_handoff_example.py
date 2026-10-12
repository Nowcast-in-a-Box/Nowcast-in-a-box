"""Show BT how to call the existing Core; synthetic data, no Runtime reader.

Run from the project root after installing nib-visualization and preparing
the local WMO cache. This example does not download data or compute metrics.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from nib_visualization import (
    RenderConfig,
    make_fixture,
    prepare_scene,
    render_frame,
    validate_field,
    write_animation,
    write_manifest,
)
from nib_visualization.artifacts import axes_pixel_box, inspect_outputs, verify_source_identity
from nib_visualization.wmo_basemap import load_wmo_basemap

# Import after the Core has selected the headless Matplotlib backend.
import matplotlib.pyplot as plt  # isort: skip

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--basemap", type=Path, default=ROOT / "data/cache/basemap/wmo")
    parser.add_argument(
        "--source-record", type=Path, default=ROOT / "basemap_sources/wmo-source.json"
    )
    parser.add_argument(
        "--acquisition-record", type=Path, default=ROOT / "basemap_sources/wmo-acquisition.json"
    )
    args = parser.parse_args()

    # Replace this fixture with a source adapter's validated VisualizationField.
    # The three leads are example data, not a limit or a Runtime contract.
    fixture = make_fixture("latlon", n_leads=3)
    field = validate_field(replace(
        fixture, provenance={**fixture.provenance, "is_real_forecast": False}
    ))
    basemap = load_wmo_basemap(args.basemap, args.source_record)
    verify_source_identity("wmo", basemap, args.source_record)
    scene = prepare_scene(
        field, RenderConfig(notice="SYNTHETIC EXAMPLE — not a forecast"), basemap
    )
    fps = 2.0
    outputs = {
        "map": args.output / "map.png",
        "animation": args.output / "animation.gif",
        "manifest": args.output / "manifest.json",
    }
    frames = args.output / "frames"
    try:
        frames.mkdir(parents=True, exist_ok=True)
        for index in range(field.values.sizes["lead_time"]):
            figure = render_frame(scene, field, index)
            figure.savefig(frames / f"frame_{index:03d}.png", dpi=scene.config.dpi)
            if index == 0:
                figure.savefig(outputs["map"], dpi=scene.config.dpi)
        write_animation(scene, field, outputs["animation"], fps=fps)
        checks = inspect_outputs(
            outputs["map"], outputs["animation"], field.values.sizes["lead_time"],
            expected_duration_ms=500,
            colorbar_box=axes_pixel_box(scene.figure, scene.colorbar.ax),
        )
        write_manifest(
            field, scene, provider="wmo", basemap=basemap,
            source_record_path=args.source_record,
            acquisition_record_path=args.acquisition_record,
            outputs=outputs, command=[sys.executable, *sys.argv], fps=fps, checks=checks,
            lockfile_path=ROOT / "requirements-dev.lock.txt",
        )
    finally:
        plt.close(scene.figure)
    print(f"Synthetic example: {field.values.sizes['lead_time']} frames in {args.output}")


if __name__ == "__main__":
    main()
