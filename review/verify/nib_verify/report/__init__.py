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
from .chart import PALETTE, Chart, Series, from_query, line_chart
from .mesh import Mesh, Panel, mesh_for, mesh_svg

__all__ = [
    "Chart",
    "Mesh",
    "PALETTE",
    "Panel",
    "Series",
    "chart_for",
    "from_query",
    "line_chart",
    "mesh_for",
    "mesh_svg",
]


def chart_for(
    store,
    cfg,
    *,
    method: str,
    s: Mapping[str, Any] | None = None,
    x: str = "lead_minutes",
    series: Sequence[str] = ("member",),
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
    return from_query(result, x=x, series=tuple(series))
