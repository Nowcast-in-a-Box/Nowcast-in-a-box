"""Metrics, as pure functions of stored quantities.

Nothing here reads a field or a file.  That is the whole point: a metric is a
formula over the additive blocks, so it can be applied at query time to any
subset of rows.  ``g`` (the expansion dimensions) never appears in this module —
grouping happens in SQL, by summing rows.

The names follow the MetEva middle-function vocabulary so the two can be talked
about with one set of words.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np

NAN = float("nan")


# ----------------------------------------------------------------------
# categorical
# ----------------------------------------------------------------------
def csi(H: float, F: float, M: float, C: float) -> float:
    """Critical success index — the threat score."""
    denom = H + F + M
    return H / denom if denom > 0 else NAN


def bias(H: float, F: float, M: float, C: float) -> float:
    denom = H + M
    return (H + F) / denom if denom > 0 else NAN


def ets(H: float, F: float, M: float, C: float) -> float:
    """Equitable threat score."""
    n = H + F + M + C
    if n <= 0:
        return NAN
    hits_random = (H + F) * (H + M) / n
    denom = H + F + M - hits_random
    return (H - hits_random) / denom if denom > 0 else NAN


def pod(H: float, F: float, M: float, C: float) -> float:
    denom = H + M
    return H / denom if denom > 0 else NAN


def far(H: float, F: float, M: float, C: float) -> float:
    denom = H + F
    return F / denom if denom > 0 else NAN


def frequency_bias(H: float, F: float, M: float, C: float) -> float:
    denom = H + M
    return (H + F) / denom if denom > 0 else NAN


# ----------------------------------------------------------------------
# continuous
# ----------------------------------------------------------------------
def me(T: float, E: float, A: float, S: float) -> float:
    """Mean error — the signed bias."""
    return E / T if T > 0 else NAN


def mae(T: float, E: float, A: float, S: float) -> float:
    return A / T if T > 0 else NAN


def rmse(T: float, E: float, A: float, S: float) -> float:
    return math.sqrt(S / T) if T > 0 else NAN


# ----------------------------------------------------------------------
# moments
# ----------------------------------------------------------------------
def corr(T: float, MX: float, MY: float, SX: float, SY: float, SXY: float) -> float:
    """Pearson correlation from sums."""
    if T <= 0:
        return NAN
    cov = SXY - MX * MY / T
    vx = SX - MX * MX / T
    vy = SY - MY * MY / T
    if vx <= 0 or vy <= 0:
        return NAN
    return cov / math.sqrt(vx * vy)


# ----------------------------------------------------------------------
# spatial
# ----------------------------------------------------------------------
def fss(pob: float, pfo: float, fbs: float, n_valid: float = 0.0) -> float:
    """Fractions skill score."""
    denom = pob + pfo
    return 1.0 - fbs / denom if denom > 0 else NAN


# ----------------------------------------------------------------------
# registry
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class MetricSpec:
    """Everything the query layer needs to know about one metric."""

    name: str
    family: str
    columns: tuple[str, ...]
    fn: Callable[..., float]
    label: str
    needs_level: bool = False
    needs_window: bool = False

    def compute(self, row: Mapping[str, float]) -> float:
        args = [row.get(c, 0.0) for c in self.columns]
        try:
            return float(self.fn(*args))
        except (TypeError, ValueError, ZeroDivisionError):
            return NAN


_CAT = ("H", "F", "M", "C")
_CONT = ("T", "E", "A", "S")
_MOM = ("T", "MX", "MY", "SX", "SY", "SXY")
_SPA = ("pob", "pfo", "fbs", "n_valid")


REGISTRY: dict[str, MetricSpec] = {
    # categorical — shared level, one row per level
    "ts": MetricSpec("ts", "cat", _CAT, csi, "Threat score (CSI)", needs_level=True),
    "bias": MetricSpec("bias", "cat", _CAT, bias, "Frequency bias", needs_level=True),
    "ets": MetricSpec("ets", "cat", _CAT, ets, "Equitable threat score", needs_level=True),
    "pod": MetricSpec("pod", "cat", _CAT, pod, "Probability of detection", needs_level=True),
    "far": MetricSpec("far", "cat", _CAT, far, "False alarm ratio", needs_level=True),
    # continuous — no threshold
    "me": MetricSpec("me", "cont", _CONT, me, "Mean error"),
    "mae": MetricSpec("mae", "cont", _CONT, mae, "Mean absolute error"),
    "rmse": MetricSpec("rmse", "cont", _CONT, rmse, "Root mean square error"),
    # moments
    "corr": MetricSpec("corr", "mom", _MOM, corr, "Correlation"),
    # spatial
    "fss": MetricSpec("fss", "spatial", _SPA, fss, "Fractions skill score",
                      needs_level=True, needs_window=True),
}

#: families that need a level in the storage key
LEVEL_METRICS = tuple(sorted({s.name for s in REGISTRY.values() if s.needs_level}))
#: families that need a half-window in the storage key
WINDOW_METRICS = tuple(sorted({s.name for s in REGISTRY.values() if s.needs_window}))

STORAGE_FAMILIES = ("cat", "cont", "mom", "spatial")


#: other names for the same formula.  ``ts`` and ``csi`` are one metric, and
#: saying so here is cheaper than letting two entries drift apart.
ALIASES: dict[str, str] = {
    "csi": "ts",
    "threat_score": "ts",
    "critical_success_index": "ts",
    "frequency_bias": "bias",
    "correlation": "corr",
}


def spec_for(method: str) -> MetricSpec:
    key = (method or "").strip().lower()
    key = ALIASES.get(key, key)
    if key not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        raise KeyError(f"unknown metric {method!r}; known metrics: {known}")
    return REGISTRY[key]


def list_metrics() -> list[dict]:
    """The ``metrics list`` payload: name, family, parameters, label."""
    out = []
    for name in sorted(REGISTRY):
        s = REGISTRY[name]
        params = []
        if s.needs_level:
            params.append("level")
        if s.needs_window:
            params.append("half_window")
        out.append(
            {
                "method": name,
                "family": s.family,
                "label": s.label,
                "params": params,
            }
        )
    return out
