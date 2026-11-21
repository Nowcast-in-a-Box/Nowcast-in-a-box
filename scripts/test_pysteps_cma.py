"""Run the official pySTEPS-LK CMA baseline without Docker, Handler, or Hub.

This is a *developer experiment*, not a NiB model runtime or Hub product.

From the repository root:
    python -m scripts.test_pysteps_cma \
        --data-dir /path/to/CREF/05 --start 2024-07-05T00:00:00Z

Runs the existing CMA Data Adapter on 6 historical + 20 verifying CREF frames,
then calls the version-pinned official pySTEPS algorithm.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

CADENCE_MINUTES = 6
CONTEXT_STEPS = 6
LEAD_STEPS = 20
FLOW_FRAMES = 3


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("--start must include UTC timezone, e.g. 2024-07-05T00:00:00Z")
    if parsed.second or parsed.microsecond or parsed.minute % CADENCE_MINUTES:
        raise ValueError("--start must align to the CMA 6-minute cadence")
    return parsed


def _stamp(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_cref(raw: np.ndarray) -> np.ndarray:
    """Reuse NiB CMA ingestion's no-echo/invalid-value semantics.

    The original Data Adapter preserves raw CREF values; model input maps
    -128 dBZ sentinel (no echo) to 0 and masked <= -100 values to NaN.
    Negative *valid* CREF is clipped to zero for this demo, as in
    ingestion.cma_radar.regrid, without altering the original adapter.
    """
    values = np.asarray(raw, dtype=np.float32)
    no_echo = values == -128.0
    valid = (values > -100.0) | no_echo
    out = np.where(valid, np.maximum(np.where(no_echo, 0, values), 0), np.nan)
    return out.astype(np.float32)


def load_window(
    data_dir: str | Path,
    start: str,
    bbox: tuple[float, float, float, float],
    *,
    context_steps: int = CONTEXT_STEPS,
    lead_steps: int = LEAD_STEPS,
) -> dict:
    """Read 2024 or other CMA dates via the *unchanged* NiB Data Adapter."""
    from adapters.cma_radar.source import ADAPTER
    from interfaces.data import DataRequest

    root = Path(data_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"CMA data directory not found: {root}")
    if context_steps < 3 or lead_steps < 1:
        raise ValueError("context_steps must be >=3 and lead_steps >=1")
    first = _parse_utc(start)
    count = context_steps + lead_steps
    last = first + timedelta(minutes=CADENCE_MINUTES * (count - 1))
    request = DataRequest(
        source="cma_radar",
        data_dir=str(root),
        data_root=None,
        initial_time=_stamp(last),
        history_steps=count,
        interval_minutes=CADENCE_MINUTES,
        channels=("CREF",),
        bbox=tuple(float(x) for x in bbox),
    )
    bundle = ADAPTER.load(request)
    expected_times = [
        _stamp(first + timedelta(minutes=CADENCE_MINUTES * i)) for i in range(count)
    ]
    if sorted(bundle["data"]) != expected_times:
        raise ValueError("CMA DataBundle timestamps do not match requested window")

    arrays = []
    last_frame = None
    source_files = []
    for time_key in expected_times:
        field = bundle["data"][time_key]["CREF"]
        arrays.append(clean_cref(field.values))
        source_files.extend(bundle["source_files"][time_key]["CREF"])
        last_frame = field
    frames = np.stack(arrays)
    if frames.ndim != 3 or frames.shape[1] < 3 or frames.shape[2] < 3:
        raise ValueError(f"invalid CMA frame shape: {frames.shape}")

    return {
        "context": frames[:context_steps],
        "target": frames[context_steps:],
        "longitude": np.asarray(last_frame.longitude.values),
        "latitude": np.asarray(last_frame.latitude.values),
        "context_times": expected_times[:context_steps],
        "target_times": expected_times[context_steps:],
        "init_time": expected_times[context_steps - 1],
        "lead_minutes": np.arange(1, lead_steps + 1) * CADENCE_MINUTES,
        "source_files": source_files,
    }


def per_lead_scores(
    forecast: np.ndarray, target: np.ndarray, threshold_dbz: float = 20.0
) -> dict[str, list[float | None]]:
    """Simple local sanity metrics; this does not replace NiB Diagnostics."""
    if forecast.shape != target.shape or forecast.ndim != 3:
        raise ValueError("forecast and target must match (lead,y,x) dimensions")
    metrics = {"mae_dbz": [], "rmse_dbz": [], "csi": [], "valid_pixels": []}
    for pred, obs in zip(forecast, target):
        valid = np.isfinite(pred) & np.isfinite(obs)
        count = int(valid.sum())
        metrics["valid_pixels"].append(count)
        if not count:
            for name in ("mae_dbz", "rmse_dbz", "csi"):
                metrics[name].append(None)
            continue
        delta = pred[valid].astype(np.float64) - obs[valid].astype(np.float64)
        metrics["mae_dbz"].append(float(np.mean(np.abs(delta))))
        metrics["rmse_dbz"].append(float(np.sqrt(np.mean(delta**2))))
        hits = int(
            np.count_nonzero(
                (pred[valid] >= threshold_dbz) & (obs[valid] >= threshold_dbz)
            )
        )
        false_alarm = int(
            np.count_nonzero(
                (pred[valid] >= threshold_dbz) & (obs[valid] < threshold_dbz)
            )
        )
        misses = int(
            np.count_nonzero(
                (pred[valid] < threshold_dbz) & (obs[valid] >= threshold_dbz)
            )
        )
        denom = hits + false_alarm + misses
        metrics["csi"].append(float(hits / denom) if denom else None)
    return metrics


def plot_results(
    output_dir: Path,
    context: np.ndarray,
    target: np.ndarray,
    forecast: np.ndarray,
    velocity: np.ndarray,
    leads: np.ndarray,
    scores: dict,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image, ImageDraw

    cmap = plt.get_cmap("turbo")
    plot_indices = sorted(
        set(
            [
                0,
                max(0, len(leads) // 4 - 1),
                max(0, len(leads) // 2 - 1),
                len(leads) - 1,
            ]
        )
    )
    fig, axes = plt.subplots(
        2, len(plot_indices), figsize=(3.7 * len(plot_indices), 7), squeeze=False
    )
    for j, idx in enumerate(plot_indices):
        for row, (label, frames) in enumerate(
            (("Observation", target), ("pySTEPS LK", forecast))
        ):
            ax = axes[row, j]
            image = ax.imshow(frames[idx], origin="upper", cmap=cmap, vmin=0, vmax=60)
            ax.set_title(f"{label}: +{int(leads[idx])} min")
            ax.axis("off")
    fig.colorbar(
        image, ax=axes.ravel().tolist(), fraction=0.02, pad=0.02, label="CREF (dBZ)"
    )
    fig.savefig(output_dir / "comparison.png", dpi=145, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.5, 6))
    ax.imshow(context[-1], origin="upper", cmap=cmap, vmin=0, vmax=60)
    interval = max(1, int(min(context.shape[1:]) // 18))
    yy, xx = np.mgrid[0 : context.shape[1], 0 : context.shape[2]]
    view = (slice(None, None, interval), slice(None, None, interval))
    if np.any(np.abs(velocity) > 1e-8):
        ax.quiver(
            xx[view],
            yy[view],
            velocity[0][view],
            velocity[1][view],
            color="white",
            scale=None,
            width=0.003,
        )
    else:
        ax.text(
            0.5,
            0.05,
            "No detectable motion",
            color="white",
            transform=ax.transAxes,
            ha="center",
            va="bottom",
            bbox={"facecolor": "black", "alpha": 0.7},
        )
    ax.set_title("Official pySTEPS LK: estimated motion (pixels / 6 min)")
    ax.axis("off")
    fig.savefig(output_dir / "motion.png", dpi=145, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    axes[0].plot(
        leads, [np.nan if x is None else x for x in scores["mae_dbz"]], label="MAE"
    )
    axes[0].plot(
        leads, [np.nan if x is None else x for x in scores["rmse_dbz"]], label="RMSE"
    )
    axes[0].set(ylabel="dBZ", xlabel="Lead time (min)", title="Deterministic error")
    axes[0].legend()
    axes[0].grid(alpha=0.2)
    axes[1].plot(leads, [np.nan if x is None else x for x in scores["csi"]])
    axes[1].set(
        xlabel="Lead time (min)", ylabel="CSI", ylim=(0, 1), title="CSI @ 20 dBZ"
    )
    axes[1].grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(output_dir / "metrics.png", dpi=145)
    plt.close(fig)

    gif_frames = []
    for index, (frame, lead) in enumerate(zip(forecast, leads)):
        rgba = cmap(np.clip(np.nan_to_num(frame, nan=0.0) / 60.0, 0, 1), bytes=True)
        image = Image.fromarray(np.asarray(rgba[..., :3]), mode="RGB")
        # Enlarge small radar crops for visibility, no change to saved data.
        if image.width < 320:
            factor = max(1, int(np.ceil(320 / image.width)))
            image = image.resize(
                (image.width * factor, image.height * factor), Image.Resampling.NEAREST
            )
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 160, 22), fill="black")
        draw.text((7, 5), f"pySTEPS +{int(lead)} min", fill="white")
        gif_frames.append(image)
    gif_frames[0].save(
        output_dir / "forecast.gif",
        save_all=True,
        append_images=gif_frames[1:],
        duration=250,
        loop=0,
        optimize=False,
    )


def run(args: argparse.Namespace) -> Path:
    from adapters.cma_radar.pysteps_lk.model import MODEL_VERSION, predict

    window = load_window(args.data_dir, args.start, tuple(args.bbox))
    context = window["context"]
    if not np.isfinite(context).all():
        missing = np.count_nonzero(~np.isfinite(context))
        raise ValueError(
            f"CMA history contains {missing} missing-coverage pixels. "
            "Change --bbox or --start; do not turn no-data into zeros."
        )
    print(
        f"CMA DataBundle OK: context={context.shape}, target={window['target'].shape}, "
        f"init={window['init_time']}, unit=dBZ"
    )
    if args.check_data:
        return Path(".")

    forecast, velocity = predict(
        context, lead_steps=window["target"].shape[0], flow_frames=FLOW_FRAMES
    )
    scores = per_lead_scores(forecast, window["target"])
    dest = Path(args.output_dir)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"output directory is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        dest / "forecast.npz",
        forecast=forecast,
        velocity=velocity,
        context=context,
        target=window["target"],
        lead_minutes=window["lead_minutes"],
        longitude=window["longitude"],
        latitude=window["latitude"],
    )
    metadata = {
        "model": "official pySTEPS Lucas-Kanade + semi-Lagrangian extrapolation",
        "version": MODEL_VERSION,
        "source": "NiB existing CMA Data Adapter / CREF",
        "data_dir": str(Path(args.data_dir).expanduser().resolve()),
        "sample_start": args.start,
        "init_time": window["init_time"],
        "context_times": window["context_times"],
        "target_times": window["target_times"],
        "lead_minutes": window["lead_minutes"].tolist(),
        "input_frames": int(context.shape[0]),
        "lk_frames": FLOW_FRAMES,
        "unit": "dBZ",
        "cadence_minutes": CADENCE_MINUTES,
        "bbox_lonlat": [float(x) for x in args.bbox],
        "missing_policy": "-128 no echo -> 0; <=-100 other sentinels -> NaN; valid negatives -> 0",
        "warning": "Reflectivity advection in dBZ, NOT rainfall-rate estimation in mm/h",
        "evaluation": "Local verification preview only; not NiB Diagnostics",
        "csi_threshold_dbz": 20.0,
        "scores": scores,
    }
    (dest / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    plot_results(
        dest,
        context,
        window["target"],
        forecast,
        velocity,
        window["lead_minutes"],
        scores,
    )
    print(f"Saved official pySTEPS forecast and figures: {dest.resolve()}")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NiB CMA DataBundle -> official pySTEPS 1.21.5 LK -> 20-step CREF extrapolation (no Docker/Hub)"
    )
    parser.add_argument(
        "--data-dir", required=True, help="Directory with CMA CREF .bin files"
    )
    parser.add_argument(
        "--start",
        default="2024-07-05T00:00:00Z",
        help="First of 6 context frames (UTC)",
    )
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("W", "S", "E", "N"),
        default=(117.0, 31.0, 119.0, 33.0),
        help="Radar crop [west south east north] in degrees",
    )
    parser.add_argument(
        "--output-dir",
        default="runs/local-pysteps-lk-cma-20240705",
        help="Result directory",
    )
    parser.add_argument(
        "--check-data",
        action="store_true",
        help="Read/validate 26 CMA frames; do not require pySTEPS",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
