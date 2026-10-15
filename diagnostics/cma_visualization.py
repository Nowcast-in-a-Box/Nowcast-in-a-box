"""Regional CREF comparison on the stored 3 km grid, with no basemap download."""

from html import escape
from pathlib import Path
import uuid

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
import numpy as np

from utils.stored_fields import StoredFields, save_manifest


def visualization(directory, model_id, *, attempt_id=None):
    source = StoredFields(directory)
    manifest = source.manifest
    forecast = manifest["forecasts"][model_id]
    target = manifest["observations"]["target"]
    if forecast["times"] != target["times"]:
        raise ValueError(
            "CREF comparison requires matching forecast and observed times"
        )
    predicted, observed = source.cube(forecast), source.cube(target)
    context = source.cube(manifest["observations"]["context"])
    if not np.array_equal(predicted["x"], observed["x"]) or not np.array_equal(
        predicted["y"], observed["y"]
    ):
        raise ValueError("CREF comparison requires identical grids")
    attempt_id = attempt_id or uuid.uuid4().hex
    if len(attempt_id) != 32 or any(c not in "0123456789abcdef" for c in attempt_id):
        raise ValueError("invalid diagnostic attempt ID")
    out = Path(directory) / "diagnostics" / "visualization" / attempt_id
    out.mkdir(parents=True, exist_ok=False)
    figure, axes = plt.subplots(1, 3, figsize=(12, 4.7), constrained_layout=True)
    extent = [
        predicted["x"][0] / 1000 - 1.5,
        predicted["x"][-1] / 1000 + 1.5,
        predicted["y"][0] / 1000 - 1.5,
        predicted["y"][-1] / 1000 + 1.5,
    ]
    panels = []

    def masked(cube, index):
        return np.ma.array(cube["values"][index], mask=cube["mask"][index])

    for ax, values, title in zip(
        axes,
        [masked(context, -1), masked(observed, 0), masked(predicted, 0)],
        ["Latest input", "Observed", "NowcastNet demo"],
    ):
        panels.append(
            ax.imshow(
                values,
                origin="lower",
                extent=extent,
                vmin=0,
                vmax=75,
                cmap="turbo",
                interpolation="nearest",
            )
        )
        ax.set(title=title, xlabel="LAEA x (km)", ylabel="LAEA y (km)")
    figure.colorbar(panels[-1], ax=list(axes), label="CREF (dBZ)", shrink=0.75)
    heading = figure.suptitle("", fontsize=10)

    def frame(index):
        panels[1].set_data(masked(observed, index))
        panels[2].set_data(masked(predicted, index))
        heading.set_text(
            f"{manifest['sample']['init_time']} · +{forecast['lead_minutes'][index]} min\n3 km · 128×128 · SEVIR pretrained cross-domain demo; skill unvalidated"
        )
        return panels

    try:
        frame(0)
        figure.savefig(out / "comparison.png", dpi=120)
        animation = FuncAnimation(
            figure, frame, frames=len(forecast["times"]), interval=500
        )
        animation.save(out / "animation.gif", writer=PillowWriter(fps=2), dpi=90)
    finally:
        plt.close(figure)
    notice = manifest["sample"]["provenance"]["notice"]
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>CMA radar demo</title><style>body{font-family:sans-serif;max-width:1400px;margin:24px auto}img{width:100%}</style><h1>CMA Radar / NowcastNet demo</h1><p>'
        + escape(notice)
        + '</p><img src="comparison.png" alt="Latest input, observed target and forecast"><img src="animation.gif" alt="Twenty lead forecast and observation comparison">'
    )
    attempt = {
        "kind": "visualization",
        "id": attempt_id,
        "model_id": model_id,
        "status": "succeeded",
        "path": out.relative_to(Path(directory)).as_posix(),
        "entry": (out / "index.html").relative_to(Path(directory)).as_posix(),
    }
    manifest["diagnostics"].append(attempt)
    save_manifest(directory, manifest)
    return attempt
