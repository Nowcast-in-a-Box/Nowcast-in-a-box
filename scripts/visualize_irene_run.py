"""Create a compact visual check for an IRENE / ConvGRU ensemble run.

The script reads only the NiB run products: the standard ``context.npz``
created by the DPC adapter and the converter's ``fields.npz``.  It does not
read or modify the native GeoTIFF outputs.

Example (from the repository root)::

    .\\.venv312\\Scripts\\python.exe -m scripts.visualize_irene_run \\
      --run .\\runs\\irene-dpc-manual-20261119-0350
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from utils.stored_fields import StoredFields


def _short_time(value: str) -> str:
    """Return a readable UTC timestamp without depending on date libraries."""
    return value.replace("T", " ").replace("Z", " UTC")


def _plot_image(ax, field: np.ndarray, title: str, *, vmax: float):
    image = ax.imshow(
        field, origin="lower", cmap="turbo", vmin=0.0, vmax=vmax,
        interpolation="nearest", aspect="equal",
    )
    ax.set_title(title, fontsize=8.5)
    ax.set_xticks([])
    ax.set_yticks([])
    return image


def _select_lead_indices(count: int) -> list[int]:
    """Pick four well-spaced output times, including first and final lead."""
    if count <= 4:
        return list(range(count))
    return sorted({0, round((count - 1) / 3), round(2 * (count - 1) / 3), count - 1})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="NiB run directory")
    parser.add_argument("--model", default="irene", help="Forecast model ID")
    parser.add_argument(
        "--output-dir", help="Default: <run>/diagnostics/irene_ensemble"
    )
    args = parser.parse_args()

    run = Path(args.run)
    access = StoredFields(run)
    try:
        context_descriptor = access.manifest["observations"]["context"]
        forecast_descriptor = access.manifest["forecasts"][args.model]
    except KeyError as exc:
        raise ValueError(
            "Run must contain observations.context and a converted forecast for "
            f"model {args.model!r}. Run the IRENE converter first."
        ) from exc

    context = np.asarray(access.cube(context_descriptor)["values"], dtype=float)
    forecast = np.asarray(access.cube(forecast_descriptor)["values"], dtype=float)
    if context.ndim != 3:
        raise ValueError(f"Expected context [time, y, x], got {context.shape}")
    if forecast.ndim != 4:
        raise ValueError(
            f"Expected ensemble forecast [member, time, y, x], got {forecast.shape}"
        )

    members, leads, _, _ = forecast.shape
    lead_minutes = list(forecast_descriptor["lead_minutes"])
    if len(lead_minutes) != leads:
        raise ValueError("Forecast lead metadata does not match its data cube")

    # A shared robust scale makes intensities comparable across input and members.
    all_values = np.concatenate((context.ravel(), forecast.ravel()))
    finite = all_values[np.isfinite(all_values)]
    vmax = max(0.1, float(np.nanpercentile(finite, 99.5)))
    selected = _select_lead_indices(leads)

    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else run / "diagnostics" / f"{args.model}_ensemble"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    domain = access.manifest["sample"].get("provenance", {}).get("model_domain", {})
    bbox = domain.get("bbox_wgs84") if isinstance(domain, dict) else None
    location = ""
    if bbox:
        location = (
            f"  |  WGS84 bbox: {bbox['west']:.3f}–{bbox['east']:.3f}°E, "
            f"{bbox['south']:.3f}–{bbox['north']:.3f}°N"
        )

    # Page 1: all six input scans, then four members and their mean for four leads.
    figure = plt.figure(figsize=(15, 14), constrained_layout=True)
    grid = figure.add_gridspec(5, 7, width_ratios=[1, 1, 1, 1, 1, 1, 0.09])
    image = None
    context_times = list(context_descriptor["times"])
    for index in range(context.shape[0]):
        axis = figure.add_subplot(grid[0, index])
        image = _plot_image(
            axis, context[index], f"Input {index + 1}\\n{_short_time(context_times[index])}",
            vmax=vmax,
        )
    # The seventh position is reserved for the colour bar.
    for row, lead_index in enumerate(selected, start=1):
        for member in range(members):
            axis = figure.add_subplot(grid[row, member])
            image = _plot_image(
                axis, forecast[member, lead_index],
                f"Member {member + 1}  +{lead_minutes[lead_index]} min", vmax=vmax,
            )
        axis = figure.add_subplot(grid[row, 4])
        image = _plot_image(
            axis, np.nanmean(forecast[:, lead_index], axis=0),
            f"Ensemble mean  +{lead_minutes[lead_index]} min", vmax=vmax,
        )
        axis = figure.add_subplot(grid[row, 5])
        axis.axis("off")

    color_axis = figure.add_subplot(grid[0, 6])
    colorbar = figure.colorbar(image, cax=color_axis)
    colorbar.set_label("Rain rate (mm/h)", fontsize=9)
    figure.suptitle(
        "IRENE / ConvGRU — six DPC input scans and ensemble forecast\n"
        f"Init: {_short_time(access.manifest['sample']['init_time'])}  |  "
        f"{members} members × {leads} leads  |  shared scale: 0–{vmax:.2f} mm/h"
        f"{location}",
        fontsize=15, fontweight="bold",
    )
    overview_path = output_dir / "input_and_ensemble_overview.png"
    figure.savefig(overview_path, dpi=180, bbox_inches="tight")
    plt.close(figure)

    # Page 2: mean and spread make member agreement visible at the same leads.
    figure, axes = plt.subplots(2, len(selected), figsize=(14, 6), constrained_layout=True)
    if len(selected) == 1:
        axes = np.asarray(axes).reshape(2, 1)
    spread_max = max(0.1, float(np.nanpercentile(np.nanstd(forecast, axis=0), 99.5)))
    for column, lead_index in enumerate(selected):
        _plot_image(
            axes[0, column], np.nanmean(forecast[:, lead_index], axis=0),
            f"Mean  +{lead_minutes[lead_index]} min", vmax=vmax,
        )
        spread_image = axes[1, column].imshow(
            np.nanstd(forecast[:, lead_index], axis=0), origin="lower", cmap="magma",
            vmin=0.0, vmax=spread_max, interpolation="nearest", aspect="equal",
        )
        axes[1, column].set_title(f"Member spread  +{lead_minutes[lead_index]} min", fontsize=9)
        axes[1, column].set_xticks([])
        axes[1, column].set_yticks([])
    mean_bar = figure.colorbar(axes[0, 0].images[0], ax=axes[0, :], shrink=0.8, pad=0.02)
    mean_bar.set_label("Mean rain rate (mm/h)")
    spread_bar = figure.colorbar(spread_image, ax=axes[1, :], shrink=0.8, pad=0.02)
    spread_bar.set_label("Member standard deviation (mm/h)")
    figure.suptitle("IRENE ensemble summary — mean and member spread", fontsize=14, fontweight="bold")
    summary_path = output_dir / "ensemble_mean_and_spread.png"
    figure.savefig(summary_path, dpi=180, bbox_inches="tight")
    plt.close(figure)

    print(f"Saved input/member overview: {overview_path.resolve()}")
    print(f"Saved ensemble mean/spread: {summary_path.resolve()}")


if __name__ == "__main__":
    main()
