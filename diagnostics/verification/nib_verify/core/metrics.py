"""Metrics, as pure functions of stored quantities.

Nothing here reads a field or a file.  That is the whole point: a metric is a
formula over the additive blocks, so it can be applied at query time to any
subset of rows.  ``g`` (the expansion dimensions) never appears in this module —
grouping happens in SQL, by summing rows.

The names follow the MetEva middle-function vocabulary so the two can be talked
about with one set of words. A metric's ``family`` is a name from
:mod:`nib_verify.core.quantities`, which is the same name a config file uses and
the same name the store uses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np

from .quantities import FAMILY_OF, FAMILIES, Family

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


def dts(H: float, F: float, M: float, C: float) -> float:
    """Detection threat score — the event's threat score and the no-event's,
    averaged.

    ``ts`` only asks how well the event was found. On a rare event a model can
    look good there while calling almost everything a hit, because hits are
    cheap when the answer is almost always no. This asks the same question of
    the negative class, so firing everywhere is paid for by the second half
    rather than rewarded by the first.

    Both halves have to exist. When either denominator is zero the score is
    undefined, not half of the other one — that is what MetEva's ``IV`` means,
    and taking the mean of one number and a gap would invent a value.
    """
    yes = H + F + M
    no = M + F + C
    if yes <= 0 or no <= 0:
        return NAN
    return (H / yes + C / no) / 2


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
    """Everything the query layer needs to know about one metric.

    ``family`` must name a block that exists and that the metric's columns
    actually live in — the two are checked against each other in
    :func:`spec_for`, because a metric filed under a family it does not read
    would be a formula over columns that are not there, and every answer it gave
    would be a gap rather than an error.
    """

    name: str
    family: str
    columns: tuple[str, ...]
    fn: Callable[..., float]
    label: str

    @property
    def block(self) -> Family:
        return FAMILY_OF[self.family]

    @property
    def needs_level(self) -> bool:
        """Whether the block is keyed by a threshold.

        Read off the block rather than declared per metric: a metric cannot want
        a threshold its block does not have.
        """
        return self.block.needs_level

    @property
    def needs_window(self) -> bool:
        return self.block.needs_window

    def compute(self, row: Mapping[str, float]) -> float:
        args = [row.get(c, 0.0) for c in self.columns]
        try:
            return float(self.fn(*args))
        except (TypeError, ValueError, ZeroDivisionError):
            return NAN


_CONT = FAMILY_OF["continuous"].columns
_YES = FAMILY_OF["yes_or_no"].columns
_MOM = FAMILY_OF["moments"].columns
_SPA = FAMILY_OF["spatial"].columns


REGISTRY: dict[str, MetricSpec] = {
    # yes_or_no — one row per threshold, shared by every metric in the family
    "ts": MetricSpec("ts", "yes_or_no", _YES, csi, "Threat score (CSI)"),
    "bias": MetricSpec("bias", "yes_or_no", _YES, bias, "Frequency bias"),
    "ets": MetricSpec("ets", "yes_or_no", _YES, ets, "Equitable threat score"),
    "pod": MetricSpec("pod", "yes_or_no", _YES, pod, "Probability of detection"),
    "far": MetricSpec("far", "yes_or_no", _YES, far, "False alarm ratio"),
    "dts": MetricSpec("dts", "yes_or_no", _YES, dts, "Detection threat score"),
    # continuous — no threshold
    "me": MetricSpec("me", "continuous", _CONT, me, "Mean error"),
    "mae": MetricSpec("mae", "continuous", _CONT, mae, "Mean absolute error"),
    "rmse": MetricSpec("rmse", "continuous", _CONT, rmse, "Root mean square error"),
    # moments
    "corr": MetricSpec("corr", "moments", _MOM, corr, "Correlation"),
    # spatial — a threshold and a neighbourhood
    "fss": MetricSpec("fss", "spatial", _SPA, fss, "Fractions skill score"),
}

#: metrics whose storage key carries a level, and those that carry a window
LEVEL_METRICS = tuple(sorted(n for n, s in REGISTRY.items() if s.needs_level))
WINDOW_METRICS = tuple(sorted(n for n, s in REGISTRY.items() if s.needs_window))


#: other names for the same formula.  ``ts`` and ``csi`` are one metric, and
#: saying so here is cheaper than letting two entries drift apart.
ALIASES: dict[str, str] = {
    "csi": "ts",
    "threat_score": "ts",
    "critical_success_index": "ts",
    "frequency_bias": "bias",
    "correlation": "corr",
    "detection_threat_score": "dts",
}


def spec_for(method: str) -> MetricSpec:
    key = (method or "").strip().lower()
    key = ALIASES.get(key, key)
    if key not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        raise KeyError(f"unknown metric {method!r}; known metrics: {known}")
    spec = REGISTRY[key]
    block = FAMILY_OF.get(spec.family)
    if block is None:
        raise KeyError(f"metric {key!r} names a family that does not exist: {spec.family!r}")
    missing = [c for c in spec.columns if c not in block.columns]
    if missing:
        raise KeyError(
            f"metric {key!r} is filed under {spec.family!r} but reads "
            f"{', '.join(missing)}, which that family does not store"
        )
    return spec


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


def list_families() -> list[dict]:
    """The families, with the words and the defaults the page needs.

    Sent to the interface rather than repeated there, so the words on the screen
    and the words in a config file cannot drift apart — the same rule the
    catalogue follows for what a ground truth package declares.
    """
    return [
        {
            "family": f.name,
            "label": f.label,
            "added_by_default": f.added_by_default,
            "needs_level": f.needs_level,
            "needs_window": f.needs_window,
            "metrics": sorted(n for n, s in REGISTRY.items() if s.family == f.name),
        }
        for f in FAMILIES
    ]
