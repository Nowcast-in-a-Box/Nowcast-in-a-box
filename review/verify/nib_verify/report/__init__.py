"""Turning a query into a picture.

A chart is a view of a query, not a second way to ask, so there is exactly one
path from the store to a line on a chart and it goes through
:func:`~nib_verify.query.service.query_scores`. Filters mean the same thing in a
chart as they do in a table, and a chart cannot show a number the table would
refuse to return.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..query.service import DEFAULT_LIMIT
from .chart import (PALETTE, Chart, Series, axis_label, failure_svg,
                    from_query, line_chart)
from .grid import columns_for, small_multiples
from .mesh import Mesh, Panel, mesh_for, mesh_svg

__all__ = [
    "Chart",
    "Mesh",
    "PALETTE",
    "Panel",
    "Series",
    "chart_for",
    "chart_panels",
    "columns_for",
    "failure_svg",
    "from_query",
    "line_chart",
    "mesh_for",
    "mesh_svg",
    "small_multiples",
]


def chart_for(
    store,
    cfg,
    *,
    method: str,
    s: Mapping[str, Any] | None = None,
    x: str = "lead_minutes",
    series: Sequence[str] = ("member",),
    order: Sequence[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> Chart:
    """Query, pivot, and return a chart ready to render.

    ``x`` and ``series`` are expansion dimensions, so both have to be groupable
    for the metric — which is the same check the table does, with the same error
    message.
    """
    from ..query.service import query_scores

    # Deduplicated, but insertion-ordered: the caller's series come first so the
    # legend reads the way they asked for it.
    expand = tuple(dict.fromkeys([*series, x]))
    result = query_scores(store, cfg, method=method, s=s, g=expand, limit=limit)
    return from_query(result, x=x, series=tuple(series), order=_order(cfg, order))


def chart_panels(
    store,
    cfg,
    *,
    method: str,
    s: Mapping[str, Any] | None = None,
    x: str = "lead_minutes",
    series: Sequence[str] = ("member",),
    panel_dims: Sequence[str] = (),
    order: Sequence[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[str], list[Chart]]:
    """One chart per value of the panel dimensions, all sharing a y axis.

    A parameter filtered to more than one value is a dimension, and a dimension
    drawn as extra lines in one panel is a different picture: three thresholds
    overlaid read as three ways of scoring one forecast, and side by side they
    read as one score moving with the threshold.
    """
    from ..query.service import query_scores

    panels = tuple(dict.fromkeys(panel_dims))
    expand = tuple(dict.fromkeys([*panels, *series, x]))
    result = query_scores(store, cfg, method=method, s=s, g=expand, limit=limit)
    records = result.get("records") or []

    if not panels:
        return [""], [from_query(result, x=x, series=tuple(series),
                                 order=_order(cfg, order))]

    seen: dict[tuple, None] = {}
    for record in records:
        seen.setdefault(tuple(str(record.get(d)) for d in panels), None)

    labels, built = [], []
    for key in sorted(seen):
        subset = [r for r in records
                  if tuple(str(r.get(d)) for d in panels) == key]
        labels.append(" / ".join(panel_title(d, v, cfg)
                                  for d, v in zip(panels, key) if v))
        built.append(from_query({"records": subset, "meta": result.get("meta") or {}},
                                x=x, series=tuple(series),
                                order=_order(cfg, order)))
    return labels, built


def _order(cfg, given: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """The series order a chart is drawn in, so a line keeps its colour.

    The roster's own order, and only then whatever the caller said: a config
    lists its models once and that list does not change when one of them is
    hidden off the chart, which is what stops the legend re-colouring itself
    under the click that hid something.
    """
    names = tuple(m.name for m in getattr(cfg, "members", ()) or ())
    if given:
        return tuple((str(one),) for one in given)
    return tuple((name,) for name in names)


#: What separates one panel of a grid from the next, with the unit the number is
#: in. "40" is a threshold in no unit at all; "40 dBZ" is a threshold on
#: reflectivity, and "10 cells" is a half window.
def panel_title(dimension: str, value: str, cfg) -> str:
    if dimension == "half_window":
        return f"{value} cells"
    if dimension == "level":
        unit = getattr(getattr(cfg, "ruler", None), "unit", "") or ""
        return f"{value} {unit}".strip()
    return str(value)
