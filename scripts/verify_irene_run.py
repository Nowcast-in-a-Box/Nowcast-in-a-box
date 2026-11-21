"""Verify an IRENE ensemble run after ``inputs/target.npz`` has been attached."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from utils.stored_fields import StoredFields


def _indices(count: int) -> list[int]:
    if count <= 4:
        return list(range(count))
    return sorted({0, round((count - 1) / 3), round(2 * (count - 1) / 3), count - 1})


def _image(axis, field: np.ndarray, title: str, *, cmap: str, vmax: float):
    item = axis.imshow(
        field, origin="lower", cmap=cmap, vmin=0, vmax=vmax, aspect="equal"
    )
    axis.set_title(title, fontsize=10)
    axis.set_xticks([])
    axis.set_yticks([])
    return item


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--model", default="irene")
    args = parser.parse_args()

    access = StoredFields(args.run)
    try:
        target_descriptor = access.manifest["observations"]["target"]
        forecast_descriptor = access.manifest["forecasts"][args.model]
    except KeyError as exc:
        raise ValueError(
            "This run needs target.npz and a converted IRENE forecast"
        ) from exc
    target = np.asarray(access.cube(target_descriptor)["values"], dtype=float)
    members = np.asarray(access.cube(forecast_descriptor)["values"], dtype=float)
    if members.ndim != 4 or target.ndim != 3:
        raise ValueError("expected forecast [member,time,y,x] and target [time,y,x]")
    if members.shape[1:] != target.shape:
        raise ValueError(
            f"forecast/target shapes disagree: {members.shape} vs {target.shape}"
        )
    mean = np.nanmean(members, axis=0)
    absolute_error = np.abs(mean - target)
    valid = np.isfinite(mean) & np.isfinite(target)
    mae = np.array(
        [
            np.mean(absolute_error[index][valid[index]])
            for index in range(target.shape[0])
        ]
    )
    rmse = np.array(
        [
            np.sqrt(
                np.mean((mean[index][valid[index]] - target[index][valid[index]]) ** 2)
            )
            for index in range(target.shape[0])
        ]
    )
    spread = np.nanstd(members, axis=0)
    lead_minutes = np.asarray(forecast_descriptor["lead_minutes"], dtype=int)
    chosen = _indices(target.shape[0])
    scale_values = np.concatenate((target.ravel(), mean.ravel()))
    scale = max(
        0.1, float(np.nanpercentile(scale_values[np.isfinite(scale_values)], 99.5))
    )
    error_scale = max(
        0.1, float(np.nanpercentile(absolute_error[np.isfinite(absolute_error)], 99.5))
    )
    directory = args.run / "diagnostics" / f"{args.model}_verification"
    directory.mkdir(parents=True, exist_ok=True)

    figure, axes = plt.subplots(
        3, len(chosen), figsize=(14, 9), constrained_layout=True
    )
    if len(chosen) == 1:
        axes = np.asarray(axes).reshape(3, 1)
    for column, index in enumerate(chosen):
        _image(
            axes[0, column],
            target[index],
            f"DPC truth  +{lead_minutes[index]} min",
            cmap="turbo",
            vmax=scale,
        )
        _image(
            axes[1, column],
            mean[index],
            f"IRENE ensemble mean  +{lead_minutes[index]} min",
            cmap="turbo",
            vmax=scale,
        )
        error_image = _image(
            axes[2, column],
            absolute_error[index],
            f"Absolute error  +{lead_minutes[index]} min",
            cmap="magma",
            vmax=error_scale,
        )
    rain_bar = figure.colorbar(
        axes[0, 0].images[0], ax=axes[:2, :], shrink=0.82, pad=0.02
    )
    rain_bar.set_label("Rain rate (mm/h)")
    error_bar = figure.colorbar(error_image, ax=axes[2, :], shrink=0.82, pad=0.02)
    error_bar.set_label("Absolute error (mm/h)")
    figure.suptitle(
        "IRENE / ConvGRU retrospective verification", fontsize=15, fontweight="bold"
    )
    comparison = directory / "truth_mean_absolute_error.png"
    figure.savefig(comparison, dpi=180, bbox_inches="tight")
    plt.close(figure)

    figure, axes = plt.subplots(1, 2, figsize=(12, 4.3), constrained_layout=True)
    axes[0].plot(lead_minutes, mae, marker="o", label="MAE")
    axes[0].plot(lead_minutes, rmse, marker="o", label="RMSE")
    axes[0].set(
        xlabel="Lead time (min)",
        ylabel="Rain rate error (mm/h)",
        title="Ensemble-mean error by lead",
    )
    axes[0].grid(alpha=0.3)
    axes[0].legend()
    axes[1].plot(
        lead_minutes, np.nanmean(spread, axis=(1, 2)), marker="o", color="#8b3a8c"
    )
    axes[1].set(
        xlabel="Lead time (min)",
        ylabel="Mean member std. (mm/h)",
        title="Ensemble spread by lead",
    )
    axes[1].grid(alpha=0.3)
    metrics = directory / "error_and_spread_by_lead.png"
    figure.savefig(metrics, dpi=180, bbox_inches="tight")
    plt.close(figure)

    print(f"Saved verification comparison: {comparison.resolve()}")
    print(f"Saved lead metrics: {metrics.resolve()}")
    for lead, one_mae, one_rmse in zip(lead_minutes, mae, rmse):
        print(f"+{lead:02d} min  MAE={one_mae:.4f} mm/h  RMSE={one_rmse:.4f} mm/h")


if __name__ == "__main__":
    main()
