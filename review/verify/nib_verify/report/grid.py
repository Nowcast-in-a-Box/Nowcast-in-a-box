"""Several charts in a grid, for when one panel is not enough.

A parameter with more than one value is a dimension, and a dimension drawn as
extra lines in one panel is not the same picture. Three thresholds on one pair of
axes read as three ways of scoring the same forecast; on three panels sharing a
y axis they read as one score that moves as the threshold moves, which is the
question somebody picking three thresholds is asking.

Which is why the y range is shared and not fitted per cell. Cells scaled to their
own data are the classic way a grid of charts lies: a panel whose whole range is
0.84 to 0.86 gets the same vertical space as one spanning 0.2 to 0.9, and the two
look alike.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Sequence

from .chart import FONT, Chart, _axis, _esc, fs, line_chart

#: Cells across, by how many there are. Two panels side by side beat two stacked;
#: past four, three across keeps the cells from going flat.
def columns_for(count: int) -> int:
    if count <= 1:
        return 1
    return 2 if count <= 4 else 3


def small_multiples(
    charts: Sequence[Chart],
    labels: Sequence[str],
    *,
    kind: str = "line",
    width: int = 820,
    cell_height: int = 250,
    gap_x: int = 24,
    gap_y: int = 30,
    y_min: float | None = None,
    y_max: float | None = None,
) -> str:
    """The charts as cells of one grid, sharing a y axis."""
    what = [c for c in charts if not c.is_empty()]
    if not what:
        labels = list(labels) or ["—"]
        return line_chart(charts[0] if charts else Chart("no chart", "", ""),
                          width=width, kind=kind)

    values = [v for c in what for v in c.values()]
    if kind == "bar":
        values = [*values, 0.0]
    lo, hi, _ = _axis(values)
    lo = lo if y_min is None else y_min
    hi = hi if y_max is None else y_max
    first = charts[0]

    count = len(charts)
    cols = columns_for(count)
    rows = math.ceil(count / cols)
    # Nothing is reserved down the side, so the cells get the whole width. And
    # ``left`` here is room for the *axis name* only: every cell reserves its own margin for its own tick labels, so
    # adding the grid's 58 to the cell's 58 made a hundred pixels of nothing
    # between the y-axis name and the y axis.
    left, right, top, bottom = 22, 16, 26, 46
    cell_w = max(200, int((width - left - right - gap_x * (cols - 1)) / cols))
    cell_h = cell_height

    total_w = left + cols * cell_w + gap_x * (cols - 1) + right
    # No gap after the last row: a trailing one is a band of nothing between the
    # bottom of the chart and its caption.
    total_h = top + rows * cell_h + (rows - 1) * gap_y + bottom

    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
        f'viewBox="0 0 {total_w} {total_h}" font-family="{FONT}" role="img">',
        f'<rect width="{total_w}" height="{total_h}" fill="#ffffff"/>',
    ]

    for index, chart in enumerate(charts):
        row, col = divmod(index, cols)
        x = left + col * (cell_w + gap_x)
        y = top + row * (cell_h + gap_y)
        name = labels[index] if index < len(labels) else ""
        if name:
            out.append(
                f'<text x="{x}" y="{y - 8}" font-family="{FONT}" font-size="{fs(12)}" '
                f'font-weight="600" fill="#111827">{_esc(name)}</text>'
            )
        # A cell is a whole chart minus the three things the grid owns: its title,
        # the legend, and the note -- which repeated in every cell is a line of
        # small grey text laid across the plot it is describing.
        cell = line_chart(replace(chart, title="", x_label="", y_label="", note=""),
                          width=cell_w, height=cell_h, kind=kind,
                          y_min=lo, y_max=hi)
        body = cell.split("\n", 1)[1] if cell.startswith("<?xml") else cell
        body = body.replace("<svg ", f'<svg x="{x}" y="{y}" ', 1)
        out.append(body)

    # No legend here: the page draws one, under the chart and beside the
    # line/bar choice, and it can be clicked to hide a model. Two legends would be
    # two places to disagree about a colour.
    out.append(
        f'<text x="{left + (total_w - left - right) / 2:.1f}" y="{total_h - 10}" '
        f'text-anchor="middle" font-family="{FONT}" font-size="{fs(12)}" fill="#374151">'
        f'{_esc(first.x_label or "x")}</text>'
    )
    out.append(
        f'<text x="13" y="{top + (total_h - top - bottom) / 2:.1f}" text-anchor="middle" '
        f'font-family="{FONT}" font-size="{fs(12)}" fill="#374151" '
        f'transform="rotate(-90 13 {top + (total_h - top - bottom) / 2:.1f})">'
        f'{_esc(first.y_label or "y")}</text>'
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"
