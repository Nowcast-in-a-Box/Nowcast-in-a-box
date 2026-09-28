"""Additive quantities.

The module persists *counts and sums*, never ratios.  That single choice is what
makes a threshold change a re-aggregation instead of a re-run: every metric in
:mod:`nib_verify.core.metrics` is a pure function of the numbers produced here.

Four families, each with its own key shape:

===========  =========================================  =========================
family       columns                                    feeds
===========  =========================================  =========================
``cat``      H, F, M, C                                 CSI, TS, bias, ETS, POD, FAR
``cont``     T, E, A, S                                 ME, MAE, RMSE
``mom``      T, MX, MY, SX, SY, SXY                      correlation
``spatial``  pob, pfo, fbs, n_valid                      FSS
===========  =========================================  =========================

Rows are shared inside a family: adding ``ets`` next to ``csi`` costs no new
row, because both read the same ``H F M C``.  Adding a *level* does add rows —
a level is a threshold.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

FAMILIES: Mapping[str, tuple[str, ...]] = {
    "cat": ("H", "F", "M", "C"),
    "cont": ("T", "E", "A", "S"),
    "mom": ("T", "MX", "MY", "SX", "SY", "SXY"),
    "spatial": ("pob", "pfo", "fbs", "n_valid"),
}

#: families whose key includes a threshold level
LEVEL_FAMILIES = ("cat", "spatial")
#: families whose key includes a neighbourhood half-width
WINDOW_FAMILIES = ("spatial",)


def categorical(
    truth: np.ndarray, prediction: np.ndarray, level: float, valid: np.ndarray
) -> dict[str, float]:
    """Contingency counts at one threshold: ``H F M C``.

    ``H`` hits, ``F`` false alarms, ``M`` misses, ``C`` correct negatives.
    Only points where both sides are valid are counted.
    """
    t = np.asarray(truth)[valid] >= level
    p = np.asarray(prediction)[valid] >= level
    return {
        "H": float(np.count_nonzero(t & p)),
        "F": float(np.count_nonzero(~t & p)),
        "M": float(np.count_nonzero(t & ~p)),
        "C": float(np.count_nonzero(~t & ~p)),
    }


def continuous(
    truth: np.ndarray, prediction: np.ndarray, valid: np.ndarray
) -> dict[str, float]:
    """Error sums: ``T`` count, ``E`` signed sum, ``A`` absolute sum, ``S`` squares."""
    t = np.asarray(truth, dtype=float)[valid]
    p = np.asarray(prediction, dtype=float)[valid]
    if t.size == 0:
        return {"T": 0.0, "E": 0.0, "A": 0.0, "S": 0.0}
    d = p - t
    return {
        "T": float(t.size),
        "E": float(d.sum()),
        "A": float(np.abs(d).sum()),
        "S": float((d * d).sum()),
    }


def moments(
    truth: np.ndarray, prediction: np.ndarray, valid: np.ndarray
) -> dict[str, float]:
    """Sums needed for correlation: counts, first and second moments, cross term."""
    t = np.asarray(truth, dtype=float)[valid]
    p = np.asarray(prediction, dtype=float)[valid]
    if t.size == 0:
        return {"T": 0.0, "MX": 0.0, "MY": 0.0, "SX": 0.0, "SY": 0.0, "SXY": 0.0}
    return {
        "T": float(t.size),
        "MX": float(t.sum()),
        "MY": float(p.sum()),
        "SX": float((t * t).sum()),
        "SY": float((p * p).sum()),
        "SXY": float((t * p).sum()),
    }


def spatial(
    truth: np.ndarray,
    prediction: np.ndarray,
    level: float,
    half_window: int,
    valid: np.ndarray,
) -> dict[str, float]:
    """Neighbourhood fractions for FSS: ``pob pfo fbs n_valid``.

    ``FSS = 1 - sum(fbs) / sum(pob + pfo)``, so the numerator and the two
    denominators are what has to survive storage.  A neighbourhood is used only
    when every cell inside it is valid on both sides.
    """
    if half_window < 0:
        raise ValueError("half_window must be >= 0")

    t_bin = (np.asarray(truth, dtype=float) >= level).astype(float)
    p_bin = (np.asarray(prediction, dtype=float) >= level).astype(float)

    p_o, full_o = window_mean(t_bin, half_window, valid)
    p_f, full_f = window_mean(p_bin, half_window, valid)

    ok = full_o & full_f
    if not np.any(ok):
        return {"pob": 0.0, "pfo": 0.0, "fbs": 0.0, "n_valid": 0.0}

    o = p_o[ok]
    f = p_f[ok]
    return {
        "pob": float(np.sum(o * o)),
        "pfo": float(np.sum(f * f)),
        "fbs": float(np.sum((o - f) ** 2)),
        "n_valid": float(np.count_nonzero(ok)),
    }


def window_mean(
    values: np.ndarray, half_window: int, valid: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Mean of ``values`` over each ``(2h+1) x (2h+1)`` window.

    Returns ``(mean, fully_valid)`` where ``fully_valid`` is true only for
    windows entirely inside ``valid``.
    """
    k = 2 * half_window + 1
    v = np.asarray(values, dtype=float)
    if valid is None:
        w = np.ones_like(v)
    else:
        w = np.asarray(valid, dtype=float)

    num = box_sum(v * w, half_window)
    den = box_sum(w, half_window)
    mean = np.divide(num, den, out=np.zeros_like(num), where=den > 0)
    fully = den >= (k * k) - 1e-9
    return mean, fully


def box_sum(a: np.ndarray, half_window: int) -> np.ndarray:
    """Sum over each ``(2h+1) x (2h+1)`` window, via a 2-D prefix sum."""
    a = np.asarray(a, dtype=float)
    if a.ndim != 2:
        raise ValueError("box_sum expects a 2-D array")
    if half_window <= 0:
        return a.copy()

    h = int(half_window)
    k = 2 * h + 1
    padded = np.pad(a, h, mode="constant", constant_values=0.0)
    cum = np.cumsum(np.cumsum(padded, axis=0), axis=1)
    cum = np.pad(cum, ((1, 0), (1, 0)))
    ny, nx = a.shape
    return (
        cum[k:k + ny, k:k + nx]
        - cum[0:ny, k:k + nx]
        - cum[k:k + ny, 0:nx]
        + cum[0:ny, 0:nx]
    )


def accumulate(
    truth: np.ndarray,
    prediction: np.ndarray,
    valid: np.ndarray,
    *,
    families: tuple[str, ...],
    levels: tuple[float, ...] = (),
    half_windows: tuple[int, ...] = (),
) -> dict:
    """Run every requested family for one frame pair.

    Returns ``{family: {quantities}}`` for the key-less families and
    ``{family: {(level, half_window): {quantities}}}`` for the keyed ones.
    """
    out: dict = {}
    for family in families:
        if family == "cont":
            out["cont"] = continuous(truth, prediction, valid)
        elif family == "mom":
            out["mom"] = moments(truth, prediction, valid)
        elif family == "cat":
            out["cat"] = {
                float(lv): categorical(truth, prediction, lv, valid) for lv in levels
            }
        elif family == "spatial":
            out["spatial"] = {
                (float(lv), int(hw)): spatial(truth, prediction, lv, hw, valid)
                for lv in levels
                for hw in half_windows
            }
        else:
            raise ValueError(f"unknown family {family!r}")
    return out
