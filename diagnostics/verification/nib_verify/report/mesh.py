"""The 时间 x 时效 mesh.

A line answers "how does skill fall off with lead". A mesh answers "which runs
were bad, and were they bad at every lead or at one" -- the question a line over
six init times cannot answer, because the line averages the six and the average
of one good run and one terrible one looks like two mediocre ones.

Two axes are a time and a lead, so the third dimension -- which model -- becomes
a panel per model. Not a colour, and not a drill-down: the comparison *is*
looking at the two panels side by side, and putting the models in different
colours would spend the colour channel on a dimension that has three values and
needs the whole ramp for the fourth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .chart import PALETTE, _esc, _filters_note, _short, _short_time, fs

#: A sequential ramp for a score. Paled to dark, so a printed copy reads the same
#: way round as a screen. It is not perceptually uniform; it is short, it prints,
#: and it keeps green-yellow-red at the top end where the eye already looks.
#: The pale end is a *tinted* pale and not a near-white. More than half the cells
#: of a real mesh are near the bottom -- long leads, high thresholds, no skill --
#: so a bottom that is almost white makes "the score is zero there" read the same
#: as "nothing was stored there", and the note about grey cells then has to
#: explain away a colour it does not own.
RAMP = ("#cfe0f2", "#a8c8e4", "#8fbcdb", "#5aa1c4", "#3d8f7a",
        "#79b64d", "#d8c33a", "#e08a1e", "#c9482b", "#8f1d16")

#: Which end of the ramp is the good end.  ``bias`` and ``me`` are in neither
#: list: both have a best value that is not an end (1, and 0), so "darker is
#: better" and "paler is better" are both wrong and saying nothing is better than
#: saying one of them.
BETTER_HIGH = frozenset({"ts", "ets", "pod", "dts", "fss", "corr"})
BETTER_LOW = frozenset({"mae", "rmse", "far"})

#: A cell with nothing behind it.  Not white: white is what a value at the bottom
#: of the ramp looks like, and a gap that reads as "scored zero" is the one
#: mistake a verification chart must not make.
GAP_FILL = "#d7dade"

CELL = 18
CELL_MIN = 6

#: A rotated stamp needs about this much room across the canvas before its
#: neighbour's is on top of it, and that is what decides how many are drawn. It is
#: the *horizontal* footprint of the rotated text, not its length: ``01-01 00Z``
#: is sixty pixels of text that takes about fifty-three of them once it is turned
#: forty degrees.
LABEL_PX = 66


@dataclass(frozen=True)
class Panel:
    """One model's grid."""

    label: str
    #: ``(column index, row index) -> value``, ``None`` where there was a record
    #: and it had no samples.  A position with no record at all is absent.
    cells: dict[tuple[int, int], float | None] = field(default_factory=dict)

    @property
    def values(self) -> list[float]:
        return [v for v in self.cells.values() if v is not None]


@dataclass(frozen=True)
class Mesh:
    title: str
    x_label: str
    y_label: str
    #: column labels, left to right
    x_values: tuple[str, ...]
    #: row labels, bottom to top
    y_values: tuple[float, ...]
    panels: tuple[Panel, ...]
    note: str = ""
    method: str = ""
    #: rows present in the axes but not in every panel
    complete: bool = True

    def values(self) -> list[float]:
        return [v for p in self.panels for v in p.values]

    def is_empty(self) -> bool:
        return not self.values()

    @property
    def direction(self) -> str:
        if self.method in BETTER_HIGH:
            return "darker is better"
        if self.method in BETTER_LOW:
            return "paler is better"
        return ""


def mesh_for(
    store,
    cfg,
    *,
    method: str,
    s: dict[str, Any] | None = None,
    time_dim: str = "init_time",
    lead_dim: str = "lead_minutes",
    panel_dims: Sequence[str] = ("member",),
    limit: int = 20_000,
) -> Mesh:
    """Ask the store for the whole grid at once.

    One query with a three-way expansion, not a loop of queries: the panels have
    to agree about their axes, and two queries can disagree about which init
    times exist. A time that is missing for one model is then visibly missing
    from that model's panel instead of silently shifting every column after it.

    ``panel_dims`` are the dimensions that separate one grid from the next -- the
    model, and any parameter that was filtered to more than one value. Asking for
    three thresholds and getting one panel would be answering a question nobody
    asked.
    """
    from ..query.service import _translate, query_scores

    panels_by = tuple(dict.fromkeys(panel_dims))
    expand = tuple(dict.fromkeys([*panels_by, lead_dim, time_dim]))
    result = query_scores(store, cfg, method=method, s=s, g=expand, limit=limit)
    records = result.get("records") or []

    x_values = sorted({str(r[time_dim]) for r in records if r.get(time_dim) is not None})
    y_values = sorted({float(r[lead_dim]) for r in records if r.get(lead_dim) is not None})

    seen: dict[tuple, None] = {}
    for r in records:
        seen.setdefault(tuple(str(r.get(d)) for d in panels_by), None)
    if not seen and cfg is not None:
        seen = {(m.name,): None for m in cfg.members}

    # Widen the axes to what the plan contains.  A run that produced nothing has
    # no record, so the union above cannot see it -- and a column that is not
    # there is not a gap, it is an absence the chart quietly papers over.
    group_id = (s or {}).get("group_id") or (cfg.group_id if cfg is not None else None)
    if group_id:
        # The plan is read through the same filter the results were, so a chart
        # of next month does not grow columns from last month.
        window = _translate(s or {})
        planned_times, planned_leads = store.task_axes(group_id, window=window)
        x_values = sorted(set(x_values) | set(planned_times))
        y_values = sorted(set(y_values) | {float(v) for v in planned_leads})

    panels = []
    for key in sorted(seen):
        cells: dict[tuple[int, int], float | None] = {}
        for r in records:
            if tuple(str(r.get(d)) for d in panels_by) != key:
                continue
            x = x_values.index(str(r[time_dim]))
            y = y_values.index(float(r[lead_dim]))
            cells[(x, y)] = r.get("value")
        panels.append(Panel(label=" / ".join(part for part in key if part), cells=cells))

    meta = result.get("meta") or {}
    label = meta.get("label") or method
    complete = all(len(p.cells) == len(x_values) * len(y_values) for p in panels)
    return Mesh(
        title=f"{label} by {time_dim.replace('_', ' ')} and "
              f"{lead_dim.replace('_', ' ')}",
        x_label=time_dim.replace("_", " "),
        y_label=lead_dim.replace("_", " "),
        x_values=tuple(x_values),
        y_values=tuple(y_values),
        panels=tuple(panels),
        note=_filters_note(meta.get("s") or {}),
        method=(meta.get("method") or method),
        complete=complete,
    )


# ----------------------------------------------------------------------
def _colour(value: float | None, lo: float, hi: float) -> str:
    """The colour for a value, interpolated between the stops of the ramp.

    Interpolated rather than bucketed into the ten stops. Bucketed, more than
    half the cells of a real mesh landed on the same palest colour -- the long
    leads and the high thresholds where the score is near zero -- and a mesh whose
    majority is one flat colour has the same problem as one that is all 0.99: it
    is a picture with nothing in it to read.
    """
    if value is None:
        return GAP_FILL
    if hi <= lo:
        return RAMP[len(RAMP) // 2]
    frac = min(1.0, max(0.0, (value - lo) / (hi - lo))) * (len(RAMP) - 1)
    low = int(frac)
    if low >= len(RAMP) - 1:
        return RAMP[-1]
    return _mix(RAMP[low], RAMP[low + 1], frac - low)


def _mix(one: str, other: str, frac: float) -> str:
    """``frac`` of the way from one hex colour to another."""
    parts = []
    for at in (1, 3, 5):
        a = int(one[at:at + 2], 16)
        b = int(other[at:at + 2], 16)
        parts.append(f"{int(round(a + (b - a) * frac)):02x}")
    return "#" + "".join(parts)


def mesh_svg(mesh: Mesh, *, cell: int = CELL, width: int | None = None) -> str:
    """The mesh, as one SVG document. Returns a document that says so when it
    has nothing to draw, rather than a blank rectangle."""
    if mesh.is_empty() or not mesh.panels:
        return _empty_svg(mesh, width=width)

    ncol, nrow = len(mesh.x_values), len(mesh.y_values)
    left = 66
    top = 46
    label_row = 26
    colorbar = 14
    gap = 26
    legend = 118
    aside = legend + colorbar + gap

    # The cells give way, not the canvas. Laid out at a fixed size, a mesh of
    # seventy-two hours is wider than the picture and the right-hand hours are cut
    # off by it -- the same hours, and the same silent loss, as an axis taken from
    # the results.
    if width:
        room = max(CELL_MIN, (width - left - aside) // max(ncol, 1))
        cell = max(CELL_MIN, min(cell, room))
    else:
        cell = max(CELL_MIN, min(cell, 34))

    panel_w = ncol * cell
    panel_h = nrow * cell
    total_w = width or (left + panel_w + aside)
    header = 30 if mesh.direction else 0
    total_h = top + header + len(mesh.panels) * (panel_h + label_row + gap) + 44

    # The ends of the ramp come from the 2nd and 98th percentile, not from the
    # extremes. One long-lead cell that over-forecast by a factor of three takes
    # the whole ramp with it: every other cell, all of them between 1.0 and 1.5,
    # lands in the bottom tenth of a scale that now runs to 2.2 and the mesh reads
    # as empty. Values past the ends saturate, which is what a ramp is for.
    every = sorted(mesh.values())
    if len(every) >= 25:
        # Nearest rank over the *gaps* between values, not over the count: with
        # thirty values, 0.98 * 30 is 29, which is the last one -- the outlier
        # this is here to exclude.
        last = len(every) - 1
        lo = every[int(round(0.02 * last))]
        hi = every[int(round(0.98 * last))]
    else:
        lo, hi = every[0], every[-1]
    if hi <= lo:
        hi = lo + 1e-9
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
        f'viewBox="0 0 {total_w} {total_h}" font-family="Helvetica,Arial,sans-serif">',
        f'<rect width="{total_w}" height="{total_h}" fill="white"/>',
        f'<text x="14" y="24" font-size="{fs(14)}" font-weight="600">{_esc(mesh.title)}</text>',
    ]

    # ---- one panel per model
    y = top + header
    for panel in mesh.panels:
        out.append(f'<text x="14" y="{y - 6}" font-size="{fs(13)}" font-weight="600">'
                   f'{_esc(panel.label)}</text>')
        for col in range(ncol):
            for row in range(nrow):
                px = left + col * cell
                py = y + (nrow - 1 - row) * cell
                value = panel.cells.get((col, row))
                fill = _colour(value, lo, hi)
                out.append(f'<rect x="{px}" y="{py}" width="{cell}" height="{cell}" '
                           f'fill="{fill}" stroke="#ffffff" stroke-width="0.5"/>')
                # No number in the cell. It is the colour that carries the value
                # here, and the colour bar is what reads it; a two-decimal figure
                # in a nine-pixel square is legible only when the square is big
                # enough to defeat the reason for the mesh. The table beside it has
                # the numbers, to as many places as anyone wants.

        # the box, so a panel that ends early still looks like a grid
        out.append(f'<rect x="{left}" y="{y}" width="{panel_w}" height="{panel_h}" '
                   f'fill="none" stroke="#9aa5b1" stroke-width="0.6"/>')

        # y ticks: leads, bottom to top, every 24 minutes -- 24, 48, 72, 96, 120.
        # One per lead is a grey smear on a short canvas; every nth has no
        # meaning to anyone reading the axis; every 24 does, because the data
        # is on a six-minute grid and a label on a multiple of four of them
        # lands on a minute the lead grid itself respects.
        for row, lead in enumerate(mesh.y_values):
            if lead % 24 and row != len(mesh.y_values) - 1:
                continue      # the far lead stays: the axis stops there
            py = y + (nrow - 1 - row) * cell + cell / 2 + 3.5
            out.append(f'<text x="{left - 8}" y="{py}" font-size="{fs(10)}" fill="#414b57" '
                       f'text-anchor="end">{_esc(_short(lead))}</text>')
        # x ticks: init times
        # Skipped as needed rather than every n-th: what matters is that the
        # stamps do not sit on top of each other, and that depends on the cells.
        stride = max(1, -(-LABEL_PX // cell))
        for col in range(0, ncol, stride):
            px = left + col * cell + cell / 2
            out.append(f'<text x="{px}" y="{y + panel_h + 13}" font-size="{fs(9.5)}" '
                       f'fill="#414b57" text-anchor="end" '
                       f'transform="rotate(-40 {px} {y + panel_h + 13})">'
                       f'{_esc(_short_time(mesh.x_values[col]))}</text>')
        y += panel_h + label_row + gap

    # ---- axes names
    #
    # Both go where the panel actually ends, not at a fixed offset from it. The
    # offsets were chosen when a cell was twenty-two pixels wide, and a mesh that
    # shrinks its cells to fit a width -- which is what seventy-two hours needs --
    # left these two sitting past the right edge of the canvas, clipped by it.
    right_of_panel = left + panel_w + gap
    out.append(f'<text x="{left + panel_w / 2}" y="{total_h - 6}" font-size="{fs(11)}" '
               f'fill="#414b57" text-anchor="middle">{_esc(mesh.x_label)}</text>')
    # The arrow is a character, not `&uarr;`. An SVG served as image/svg+xml is
    # parsed as XML, and XML defines five entities: amp, lt, gt, quot, apos. A
    # named HTML entity is a parse error, and a parse error aborts the document
    # from that point -- so one arrow cost the colour bar that came after it.
    out.append(f'<text x="{right_of_panel}" y="{top + header + 24}" font-size="{fs(11)}" '
               f'fill="#414b57">{_esc(mesh.y_label)} ↑</text>')

    # ---- colour bar
    bx = right_of_panel
    by = top + header
    bar_h = min(190, max(90, panel_h // 2))
    steps = 60
    for i in range(steps):
        frac = i / (steps - 1)
        out.append(f'<rect x="{bx}" y="{by + bar_h * (1 - frac) - bar_h / steps:.2f}" '
                   f'width="{colorbar}" height="{bar_h / steps + 1:.2f}" '
                   f'fill="{_colour(lo + frac * (hi - lo), lo, hi)}"/>')
    out.append(f'<rect x="{bx}" y="{by}" width="{colorbar}" height="{bar_h}" '
               f'fill="none" stroke="#9aa5b1" stroke-width="0.6"/>')
    for i in range(5):
        frac = i / 4
        out.append(f'<text x="{bx + colorbar + 5}" y="{by + bar_h * (1 - frac) + 3.5}" '
                   f'font-size="{fs(10)}" fill="#414b57">{lo + frac * (hi - lo):.3f}</text>')
    out.append(f'<text x="{bx}" y="{by + bar_h + 16}" font-size="{fs(10.5)}" '
               f'fill="#414b57">{_esc(mesh.method)}</text>')
    if mesh.direction:
        out.append(f'<text x="{bx}" y="{by + bar_h + 29}" font-size="{fs(10.5)}" '
                   f'fill="#414b57">{_esc(mesh.direction)}</text>')

    # ---- the one thing on the canvas that is about the data and not about the
    # query: grey means nothing was stored there. Leave it out and a gap reads as
    # a score of zero.
    if not mesh.complete:
        out.append(f'<text x="14" y="{total_h - 8}" font-size="{fs(10)}" fill="#6b7480">'
                   f'grey cell: nothing stored \u2014 a gap, not a zero</text>')
    out.append("</svg>")
    return "\n".join(out)


def _empty_svg(mesh: Mesh, *, width: int | None = None) -> str:
    w = width or 560
    reason = f"no stored {mesh.method or 'values'} match this query"
    if mesh.note:
        reason += f" ({mesh.note})"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="120" '
        f'viewBox="0 0 {w} 120" font-family="Helvetica,Arial,sans-serif">'
        f'<rect width="{w}" height="120" fill="white"/>'
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="119" fill="none" '
        f'stroke="#d0d5db"/>'
        f'<text x="16" y="40" font-size="{fs(13)}" fill="#414b57">{_esc(mesh.title)}</text>'
        f'<text x="16" y="66" font-size="{fs(12)}" fill="#8a929c">{_esc(reason)}</text>'
        f'</svg>'
    )
