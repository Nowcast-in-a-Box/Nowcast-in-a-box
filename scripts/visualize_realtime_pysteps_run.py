"""Visualize a real-time VMI pySTEPS run without pretending forecasts are observations."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
import numpy as np

from utils.stored_fields import StoredFields


def _label(timestamp: str, prefix: str) -> str:
    value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return f"{prefix}\n{value:%Y-%m-%d %H:%M UTC}"


def _limits(context: np.ndarray, forecast: np.ndarray) -> tuple[float, float]:
    all_values = np.concatenate((context.ravel(), forecast.ravel()))
    low, high = np.percentile(all_values, (1, 99))
    return float(min(0, low)), float(max(1, high))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Real-time NiB run directory")
    parser.add_argument("--model", default="pysteps_lk")
    parser.add_argument("--leads", nargs="+", type=int, default=[5, 25, 50, 100])
    args = parser.parse_args()

    access = StoredFields(args.run)
    context_descriptor = access.manifest["observations"]["context"]
    forecast_descriptor = access.manifest["forecasts"].get(args.model)
    if forecast_descriptor is None:
        raise ValueError(f"Run has no {args.model!r} forecast")
    context = access.cube(context_descriptor)
    forecast = access.cube(forecast_descriptor)
    leads = list(forecast_descriptor["lead_minutes"])
    if any(lead not in leads for lead in args.leads):
        raise ValueError(f"Requested leads must be among {leads}")
    indexes = [leads.index(lead) for lead in args.leads]
    vmin, vmax = _limits(context["values"], forecast["values"])
    extent = (float(context["x"][0]), float(context["x"][-1]), float(context["y"][0]), float(context["y"][-1]))

    figure, axes = plt.subplots(2, 4, figsize=(15, 7.5), constrained_layout=True)
    image = None
    for column in range(4):
        ax = axes[0, column]
        if column < context["values"].shape[0]:
            image = ax.imshow(context["values"][column], origin="lower", extent=extent, cmap="turbo", vmin=vmin, vmax=vmax, aspect="auto")
            ax.set_title(_label(context_descriptor["times"][column], "Observed VMI"), fontsize=10)
        else:
            ax.axis("off")
        ax.set_xlabel("Native x")
        if column == 0:
            ax.set_ylabel("Native y")
    for column, (lead, index) in enumerate(zip(args.leads, indexes)):
        ax = axes[1, column]
        image = ax.imshow(forecast["values"][index], origin="lower", extent=extent, cmap="turbo", vmin=vmin, vmax=vmax, aspect="auto")
        ax.set_title(_label(forecast_descriptor["times"][index], f"pySTEPS forecast +{lead} min"), fontsize=10)
        ax.set_xlabel("Native x")
        if column == 0:
            ax.set_ylabel("Native y")
    colorbar = figure.colorbar(image, ax=axes, shrink=0.82, pad=0.02)
    colorbar.set_label("VMI reflectivity (dBZ)")
    figure.suptitle(
        "Real-time OPERA + Italy DPC VMI nowcast\n"
        "Top: downloaded observations · Bottom: optical-flow forecasts (no future observations yet)",
        fontsize=14,
        fontweight="bold",
    )
    output = Path(args.run) / "diagnostics"
    output.mkdir(parents=True, exist_ok=True)
    png = output / f"{args.model}_realtime_overview.png"
    figure.savefig(png, dpi=180, bbox_inches="tight")
    plt.close(figure)

    frames = list(context["values"]) + list(forecast["values"])
    labels = [_label(value, "Observed VMI") for value in context_descriptor["times"]] + [_label(value, f"pySTEPS forecast +{lead} min") for value, lead in zip(forecast_descriptor["times"], leads)]
    figure, axis = plt.subplots(figsize=(7, 5.5), constrained_layout=True)
    image = axis.imshow(frames[0], origin="lower", extent=extent, cmap="turbo", vmin=vmin, vmax=vmax, aspect="auto")
    axis.set_xlabel("Native x")
    axis.set_ylabel("Native y")
    title = axis.set_title(labels[0])
    figure.colorbar(image, ax=axis, label="VMI reflectivity (dBZ)")

    def update(index: int):
        image.set_data(frames[index])
        title.set_text(labels[index])
        return image, title

    gif = output / f"{args.model}_realtime_nowcast.gif"
    FuncAnimation(figure, update, frames=len(frames), interval=350, blit=False).save(gif, writer=PillowWriter(fps=3))
    plt.close(figure)
    print(f"Overview: {png}")
    print(f"Animation: {gif}")


if __name__ == "__main__":
    main()
