"""Compare two IRENE runs that use different but overlapping DPC crop windows."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from utils.stored_fields import StoredFields


def _indices(outer: np.ndarray, inner: np.ndarray, axis: str) -> np.ndarray:
    found = []
    for value in inner:
        matches = np.flatnonzero(np.isclose(outer, value, atol=1e-6, rtol=0))
        if len(matches) != 1:
            raise ValueError(
                f"{axis} coordinate {value} is not uniquely shared by both runs"
            )
        found.append(matches[0])
    return np.asarray(found)


def _leads(count: int) -> list[int]:
    return sorted({0, round((count - 1) / 3), round(2 * (count - 1) / 3), count - 1})


def _metrics(
    prediction: np.ndarray, truth: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(prediction) & np.isfinite(truth)
    mae, rmse = [], []
    for index in range(prediction.shape[0]):
        error = prediction[index][valid[index]] - truth[index][valid[index]]
        mae.append(float(np.mean(np.abs(error))))
        rmse.append(float(np.sqrt(np.mean(error**2))))
    return np.asarray(mae), np.asarray(rmse)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-a", required=True, type=Path, help="First IRENE run (normally 256 px)"
    )
    parser.add_argument(
        "--run-b",
        required=True,
        type=Path,
        help="Second IRENE run (normally a larger crop)",
    )
    parser.add_argument("--model", default="irene")
    args = parser.parse_args()

    first, second = StoredFields(args.run_a), StoredFields(args.run_b)

    def fields(access: StoredFields):
        context = access.cube(access.manifest["observations"]["context"])
        forecast = access.cube(access.manifest["forecasts"][args.model])
        target = access.cube(access.manifest["observations"]["target"])
        return context, forecast, target

    context_a, forecast_a, target_a = fields(first)
    context_b, forecast_b, target_b = fields(second)
    if first.manifest["sample"]["init_time"] != second.manifest["sample"]["init_time"]:
        raise ValueError("runs must have the same IRENE initial time")
    if forecast_a["values"].shape[1] != forecast_b["values"].shape[1]:
        raise ValueError("runs must have the same forecast lead count")

    # Select the smaller grid as the common spatial comparison region.
    if (
        context_a["values"].shape[-1] * context_a["values"].shape[-2]
        <= context_b["values"].shape[-1] * context_b["values"].shape[-2]
    ):
        small, large = (
            (context_a, forecast_a, target_a),
            (context_b, forecast_b, target_b),
        )
        small_access, large_access = first, second
    else:
        small, large = (
            (context_b, forecast_b, target_b),
            (context_a, forecast_a, target_a),
        )
        small_access, large_access = second, first
    context_s, forecast_s, target_s = small
    context_l, forecast_l, target_l = large
    y_index = _indices(forecast_l["y"], forecast_s["y"], "y")
    x_index = _indices(forecast_l["x"], forecast_s["x"], "x")

    def common(values: np.ndarray) -> np.ndarray:
        return values[..., y_index, :][..., :, x_index]

    mean_s = np.nanmean(forecast_s["values"], axis=0)
    mean_l = np.nanmean(common(forecast_l["values"]), axis=0)
    truth_s = target_s["values"]
    truth_l = common(target_l["values"])
    input_difference = np.nanmax(
        np.abs(context_s["values"] - common(context_l["values"]))
    )
    diff = np.abs(mean_l - mean_s)
    lead_minutes = np.asarray(
        small_access.manifest["forecasts"][args.model]["lead_minutes"]
    )
    leads = _leads(len(lead_minutes))
    shared = np.concatenate((mean_s.ravel(), mean_l.ravel(), truth_s.ravel()))
    rain_scale = max(0.1, float(np.nanpercentile(shared[np.isfinite(shared)], 99.5)))
    diff_scale = max(0.1, float(np.nanpercentile(diff[np.isfinite(diff)], 99.5)))

    output = large_access.root / "diagnostics" / "irene_window_comparison"
    output.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(3, len(leads), figsize=(14, 9), constrained_layout=True)
    for column, lead in enumerate(leads):
        for row, (values, label, cmap, vmax) in enumerate(
            (
                (
                    mean_s[lead],
                    f"{small_access.root.name}  +{lead_minutes[lead]} min",
                    "turbo",
                    rain_scale,
                ),
                (
                    mean_l[lead],
                    f"{large_access.root.name} (common area)  +{lead_minutes[lead]} min",
                    "turbo",
                    rain_scale,
                ),
                (
                    diff[lead],
                    f"Absolute difference  +{lead_minutes[lead]} min",
                    "magma",
                    diff_scale,
                ),
            )
        ):
            image = axes[row, column].imshow(
                values, origin="lower", cmap=cmap, vmin=0, vmax=vmax, aspect="equal"
            )
            axes[row, column].set_title(label, fontsize=8.5)
            axes[row, column].set_xticks([])
            axes[row, column].set_yticks([])
    rain_bar = figure.colorbar(
        axes[0, 0].images[0], ax=axes[:2, :], shrink=0.82, pad=0.02
    )
    rain_bar.set_label("Rain rate (mm/h)")
    diff_bar = figure.colorbar(image, ax=axes[2, :], shrink=0.82, pad=0.02)
    diff_bar.set_label("Forecast difference (mm/h)")
    figure.suptitle(
        f"IRENE crop comparison — shared inputs max difference: {input_difference:.3g} mm/h",
        fontsize=14,
        fontweight="bold",
    )
    fields_png = output / "forecast_common_region_difference.png"
    figure.savefig(fields_png, dpi=180, bbox_inches="tight")
    plt.close(figure)

    mae_s, rmse_s = _metrics(mean_s, truth_s)
    mae_l, rmse_l = _metrics(mean_l, truth_l)
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.2), constrained_layout=True)
    for axis, metric_a, metric_b, title, unit in (
        (axes[0], mae_s, mae_l, "MAE on shared 256×256 region", "MAE (mm/h)"),
        (axes[1], rmse_s, rmse_l, "RMSE on shared 256×256 region", "RMSE (mm/h)"),
    ):
        axis.plot(lead_minutes, metric_a, marker="o", label=small_access.root.name)
        axis.plot(lead_minutes, metric_b, marker="o", label=large_access.root.name)
        axis.set(xlabel="Lead time (min)", ylabel=unit, title=title)
        axis.grid(alpha=0.3)
        axis.legend(fontsize=8)
    metrics_png = output / "shared_region_error_comparison.png"
    figure.savefig(metrics_png, dpi=180, bbox_inches="tight")
    plt.close(figure)

    print(f"Shared input maximum absolute difference: {input_difference:.6g} mm/h")
    print(f"Saved forecast comparison: {fields_png.resolve()}")
    print(f"Saved error comparison: {metrics_png.resolve()}")


if __name__ == "__main__":
    main()
