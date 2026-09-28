"""Query: sums in, numbers out.

The read path never touches a field, a file, or a provider.  Everything it needs
is already in the store as counts and sums, so this is a ``GROUP BY`` plus the
metric formula.  A different threshold over an already-stored level set costs
exactly nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from ..config.schema import VerificationConfig
from ..core.metrics import REGISTRY, MetricSpec, spec_for
from ..store.db import Between, Store, iso_utc

#: how many valid samples each family contributes
SAMPLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "cat": ("H", "F", "M", "C"),
    "cont": ("T",),
    "mom": ("T",),
    "spatial": ("n_valid",),
}

DEFAULT_LIMIT = 20_000


class QueryError(ValueError):
    """A query that cannot be answered as asked."""


def query_scores(
    store: Store,
    cfg: VerificationConfig | None,
    *,
    method: str,
    s: Mapping[str, Any] | None = None,
    g: Sequence[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Aggregate stored quantities and apply one metric.

    ``s`` filters — ``time``, ``dtime`` (lead), ``member``, ``level``,
    ``half_window``.  ``g`` expands — any of ``member``, ``valid_time``,
    ``init_time``, ``lead_minutes``, ``level``, ``half_window``.

    Following MetEva, the *last* entry of ``g`` becomes the x-axis and the one
    before it becomes the legend when this is plotted; the earlier ones become
    subplots.  This function does not care — it returns the dims in order.
    """
    try:
        spec = spec_for(method)
    except KeyError as exc:
        raise QueryError(str(exc)) from None
    filters = _translate(s or {})
    group_by = tuple(g or ())

    if cfg is not None:
        if not cfg.metrics_for(method):
            raise QueryError(
                f"metric {method!r} is not part of config {cfg.group_id!r}"
            )
        filters.setdefault("group_id", cfg.group_id)

    if "group_id" not in filters:
        raise QueryError("a query must name a config (group_id)")

    for column in group_by:
        if column not in _groupable(spec.family):
            raise QueryError(
                f"cannot expand by {column!r} for metric {method!r}; "
                f"available: {sorted(_groupable(spec.family))}"
            )

    rows = store.sum_rows(spec.family, filters=filters, group_by=group_by)

    if not group_by and len(rows) > limit:
        raise QueryError(f"query would return {len(rows)} rows, over the {limit} limit")

    records: list[dict] = []
    n_valid_total = 0.0
    for row in rows:
        value = spec.compute(row)
        n_valid = float(sum(row.get(c, 0.0) for c in SAMPLE_COLUMNS[spec.family]))
        n_valid_total += n_valid
        rec = {c: _plain(row.get(c)) for c in group_by}
        rec["value"] = None if value != value else value  # NaN -> null in JSON
        rec["n_valid"] = n_valid
        records.append(rec)

    if len(records) > limit:
        raise QueryError(
            f"query would return {len(records)} records, over the {limit} limit. "
            "Narrow the time range, the lead range, or the expansion dimensions."
        )

    return {
        "meta": _meta(cfg, spec, method, s, g),
        "coverage": _coverage(store, filters.get("group_id")),
        "sample": {"n_records": len(records), "n_valid": n_valid_total},
        "dims": list(group_by),
        "records": records,
    }


def available_expansions(method: str) -> list[str]:
    return sorted(_groupable(spec_for(method).family))


def list_metrics() -> list[dict]:
    return [
        {
            "method": name,
            "family": REGISTRY[name].family,
            "label": REGISTRY[name].label,
            "params": _params(REGISTRY[name]),
        }
        for name in sorted(REGISTRY)
    ]


# ----------------------------------------------------------------------
def _params(spec: MetricSpec) -> list[str]:
    out = []
    if spec.needs_level:
        out.append("level")
    if spec.needs_window:
        out.append("half_window")
    return out


def _groupable(family: str) -> frozenset[str]:
    from ..store.db import KEY_COLUMNS

    return frozenset(KEY_COLUMNS[family]) - {"group_id"}


def _translate(s: Mapping[str, Any]) -> dict[str, Any]:
    """MetEva-flavoured filter names to storage columns."""
    out: dict[str, Any] = {}
    for key, value in s.items():
        if value is None:
            continue
        if key == "time":
            out["valid_time"] = _range(value)
        elif key == "init_time":
            out["init_time"] = _range(value)
        elif key in ("dtime", "lead_minutes", "lead"):
            out["lead_minutes"] = _range(value)
        elif key == "member":
            out["member"] = _as_list(value)
        elif key == "level":
            out["level"] = _as_list(value)
        elif key == "half_window":
            out["half_window"] = _as_list(value)
        elif key == "group_id":
            out["group_id"] = value
        else:
            raise QueryError(f"unknown filter {key!r}")
    return out


def _range(value: Any) -> Any:
    if isinstance(value, Between):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return Between(_stamp(value[0]), _stamp(value[1]))
    single = _stamp(value)
    return single


def _stamp(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso_utc(value)
    return value


def _as_list(value: Any) -> list:
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _plain(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _meta(
    cfg: VerificationConfig | None,
    spec: MetricSpec,
    method: str,
    s: Mapping[str, Any] | None,
    g: Sequence[str] | None,
) -> dict:
    meta: dict[str, Any] = {
        "method": method,
        "label": spec.label,
        "family": spec.family,
        "params": _params(spec),
        "s": dict(s or {}),
        "g": list(g or ()),
    }
    if cfg is not None:
        meta["group"] = {
            "id": cfg.group_id,
            "name": cfg.name,
            "ruler": {
                "ground_truth": cfg.ruler.ground_truth,
                "variable": cfg.ruler.variable,
                "unit": cfg.ruler.unit,
                "time_step_minutes": cfg.ruler.time_step_minutes,
                "extent": list(cfg.ruler.extent),
                "resolution": cfg.ruler.resolution,
                "fingerprint": cfg.ruler.fingerprint,
            },
        }
    return meta


def _coverage(store: Store, group_id: str | None) -> dict:
    if not group_id:
        return {"tasks": {}, "missing_obs_times": []}
    return {
        "tasks": store.task_counts(group_id),
        "missing_obs_times": store.obs_gaps(group_id),
    }
