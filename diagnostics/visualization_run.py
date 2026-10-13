"""Render stored fields using the delivered Core and an explicitly prepared WMO cache."""

from pathlib import Path
import sys
import uuid

from nib_visualization import (
    RenderConfig,
    prepare_scene,
    render_frame,
    write_animation,
    write_manifest,
    write_gallery,
)
from nib_visualization.artifacts import (
    axes_pixel_box,
    inspect_outputs,
    verify_source_identity,
)
from nib_visualization.wmo_basemap import load_wmo_basemap
import matplotlib.pyplot as plt

from diagnostics.fields import visualization_field, display_grid
from utils.stored_fields import StoredFields, save_manifest

ROOT = Path(__file__).resolve().parent / "visualization"


def visualization(directory, model_id, basemap_path, *, attempt_id=None):
    field = display_grid(visualization_field(directory, model_id))
    source_record = ROOT / "basemap_sources/wmo-source.json"
    acquisition_record = Path(basemap_path) / "acquisition.json"
    if not acquisition_record.is_file():
        raise FileNotFoundError(
            "prepare the WMO basemap cache before offline execution"
        )
    basemap = load_wmo_basemap(Path(basemap_path), source_record)
    verify_source_identity("wmo", basemap, source_record)
    scene = prepare_scene(
        field,
        RenderConfig(
            vmin=0, vmax=255, notice="VIL raw encoding; display resampling only"
        ),
        basemap,
    )
    attempt_id = attempt_id or uuid.uuid4().hex
    if (
        not isinstance(attempt_id, str)
        or len(attempt_id) != 32
        or any(c not in "0123456789abcdef" for c in attempt_id)
    ):
        raise ValueError("invalid diagnostic attempt ID")
    out = Path(directory) / "diagnostics" / "visualization" / attempt_id
    out.mkdir(parents=True, exist_ok=False)
    outputs = {
        "map": out / "map.png",
        "animation": out / "animation.gif",
        "manifest": out / "manifest.json",
    }
    try:
        frames = out / "frames"
        frames.mkdir()
        for index in range(field.values.sizes["lead_time"]):
            figure = render_frame(scene, field, index)
            figure.savefig(frames / f"frame_{index:03d}.png", dpi=scene.config.dpi)
            if index == 0:
                figure.savefig(outputs["map"], dpi=scene.config.dpi)
        write_animation(scene, field, outputs["animation"], fps=2.0)
        checks = inspect_outputs(
            outputs["map"],
            outputs["animation"],
            field.values.sizes["lead_time"],
            expected_duration_ms=500,
            colorbar_box=axes_pixel_box(scene.figure, scene.colorbar.ax),
        )
        write_manifest(
            field,
            scene,
            provider="wmo",
            basemap=basemap,
            source_record_path=source_record,
            acquisition_record_path=acquisition_record,
            outputs=outputs,
            command=[sys.executable, *sys.argv],
            fps=2.0,
            checks=checks,
            lockfile_path=ROOT / "requirements-dev.lock.txt",
        )
        gallery = write_gallery(outputs["manifest"])
    finally:
        plt.close(scene.figure)
    access = StoredFields(directory)
    attempt = {
        "kind": "visualization",
        "id": attempt_id,
        "model_id": model_id,
        "status": "succeeded",
        "path": out.relative_to(Path(directory)).as_posix(),
        "entry": gallery.relative_to(Path(directory)).as_posix(),
    }
    access.manifest["diagnostics"].append(attempt)
    save_manifest(directory, access.manifest)
    return attempt
