"""Visualize a pySTEPS forecast run against its CMA verifying observations.

Example (run from the repository root)::

    .\\.venv312\\Scripts\\python.exe -m scripts.visualize_pysteps_run \\
      --run .\\runs\\pysteps-cma-20240705-manual1
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from utils.stored_fields import StoredFields


def _extent(cube: dict) -> tuple[float, float, float, float]:
    """Return a geographic image extent from a StoredFields cube."""
    x = np.asarray(cube["x"], dtype=float)
    y = np.asarray(cube["y"], dtype=float)
    return float(x[0]), float(x[-1]), float(y[0]), float(y[-1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="NiB run directory")
    parser.add_argument("--model", default="pysteps_lk", help="Forecast model ID")
    parser.add_argument(
        "--leads",
        type=int,
        nargs="+",
        default=[6, 30, 60, 120],
        help="Forecast lead times in minutes to display",
    )
    parser.add_argument(
        "--output",
        help="PNG path; default is <run>/diagnostics/<model>_comparison.png",
    )
    args = parser.parse_args()

    access = StoredFields(args.run)
    try:
        forecast_descriptor = access.manifest["forecasts"][args.model]
    except KeyError as exc:
        raise ValueError(
            f"Run has no forecast for {args.model!r}; run the container first"
        ) from exc
    observation_descriptor = access.manifest["observations"]["target"]
    forecast = access.cube(forecast_descriptor)
    target = access.cube(observation_descriptor)
    leads_available = list(forecast_descriptor["lead_minutes"])
    lead_indices = []
    for lead in args.leads:
        if lead not in leads_available:
            raise ValueError(f"Lead +{lead} min is unavailable; have {leads_available}")
        lead_indices.append(leads_available.index(lead))
    if forecast["values"].shape != target["values"].shape:
        raise ValueError("forecast and verifying target must share the same cube shape")

    values = np.concatenate((target["values"], forecast["values"]))
    vmin = float(np.nanpercentile(values, 1))
    vmax = float(np.nanpercentile(values, 99.5))
    vmin, vmax = max(0.0, vmin), max(1.0, vmax)
    extent = _extent(target)
    figure, axes = plt.subplots(
        2,
        len(lead_indices),
        figsize=(3.5 * len(lead_indices), 6.2),
        constrained_layout=True,
        sharex=True,
        sharey=True,
    )
    if len(lead_indices) == 1:
        axes = np.asarray(axes).reshape(2, 1)
    image = None
    for column, (lead, index) in enumerate(zip(args.leads, lead_indices)):
        for row, (label, cube) in enumerate(
            (("CMA observation", target), ("pySTEPS LK forecast", forecast))
        ):
            image = axes[row, column].imshow(
                cube["values"][index],
                origin="lower",
                extent=extent,
                cmap="turbo",
                vmin=vmin,
                vmax=vmax,
                aspect="equal",
            )
            axes[row, column].set_title(f"{label}: +{lead} min", fontsize=10)
            axes[row, column].set_xlabel("Longitude (°E)")
            if column == 0:
                axes[row, column].set_ylabel("Latitude (°N)")
    colorbar = figure.colorbar(image, ax=axes, shrink=0.82, pad=0.02)
    colorbar.set_label("CREF reflectivity (dBZ)")
    figure.suptitle(
        f"CMA CREF verification — {args.model}", fontsize=14, fontweight="bold"
    )

    output = (
        Path(args.output)
        if args.output
        else Path(args.run) / "diagnostics" / f"{args.model}_comparison.png"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(figure)
    print(f"Saved visual comparison: {output.resolve()}")


if __name__ == "__main__":
    main()
