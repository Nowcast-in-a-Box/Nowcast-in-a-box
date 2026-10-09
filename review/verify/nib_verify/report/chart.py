"""Charts, as SVG, from a query result.

One implementation, used by both the command line and the review page. The
alternative — drawing in the browser — would mean two renderers that drift, a
chart that cannot be exported without a browser, and rules about gaps living in
JavaScript where nothing can test them.

SVG rather than PNG because there is no image library in the dependencies and
because a line chart is text: it stays sharp, it prints, and a test can read it.

Four decisions worth knowing about:

**A gap is not a zero.** The store returns a null for a quantity that had no
samples, and that is not the same as a value of nothing — an undefined CSI and a
CSI of 0 mean opposite things about a forecast. A null **breaks the line**. The
gap is drawn as a break, not as a point at the bottom of the axis.

**The y axis is not forced to zero.** A CSI curve that lives between 0.85 and
0.95 is unreadable against a zero-based axis, and the decay over lead time is the
whole reason to plot it. The axis is therefore fitted to the data and every tick
is labelled, which is what makes a fitted axis honest; `y_min`/`y_max` override
it when a fixed scale is needed for a comparison.

**It is not a plotting library.** No subplots, no stacking, no second y axis. One
x, one y, one line per series.

**No coordinate is ever NaN.** ``x="nan"`` does not render as a warning, it
renders as nothing at all — the element disappears and the chart looks merely
empty. Every coordinate goes through a guard, and there is a test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

#: Every annotation on a chart is two points larger than it was. One number,
#: applied in one place: a legend at 11 px beside a tick at 13 px is a chart that
#: was edited in nine places and kept eight of them.
FONT_DELTA = 2


def fs(size: float) -> float:
    return size + FONT_DELTA


#: What an expansion dimension is called once it is on an axis. The column name
#: is the storage's vocabulary; this is the reader's, and "lead_minutes" is
#: neither a word nor a unit.
AXIS_LABEL = {
    "lead_minutes": "lead time (min)",
    "init_time": "init time (UTC)",
    "valid_time": "valid time (UTC)",
    "hour": "hour of init (UTC)",
    "level": "level",
    "half_window": "half window (cells)",
    "member": "member",
}


def axis_label(dimension: str) -> str:
    return AXIS_LABEL.get(dimension, dimension.replace("_", " "))


#: Enough for the series anyone plots on one chart, and distinguishable in print.
PALETTE = (
    "#1f6feb", "#d1495b", "#2e8b57", "#8b5cf6",
    "#d97706", "#0891b2", "#be185d", "#4b5563",
)

#: Past this many distinct x values, tick at round numbers instead of at every
#: point. Below it, one tick per point: tick marks that do not sit on the data
#: are noise when the data is `lead_minutes = 10, 20, 30, 60`.
MAX_ORDINAL_TICKS = 12


@dataclass(frozen=True)
class Series:
    """One line.

    ``points`` carries a ``None`` y for every x where the quantity was undefined.
    Keeping those positions rather than dropping them is what lets the renderer
    break the line instead of drawing straight through a gap.
    """

    label: str
    points: tuple[tuple[float, float | None], ...]

    @property
    def defined(self) -> tuple[tuple[float, float], ...]:
        return tuple((x, y) for x, y in self.points if y is not None)

    @property
    def has_gap(self) -> bool:
        return any(y is None for _, y in self.points)


@dataclass(frozen=True)
class Chart:
    title: str
    x_label: str
    y_label: str
    series: tuple[Series, ...] = ()
    note: str = ""

    def values(self) -> list[float]:
        return [y for s in self.series for _, y in s.defined]

    def x_values(self) -> list[float]:
        return [x for s in self.series for x, _ in s.points]

    def is_empty(self) -> bool:
        return not self.values()


def from_query(
    result: dict,
    *,
    x: str,
    series: Sequence[str] = (),
    order: Sequence[str] = (),
    title: str | None = None,
    y_label: str | None = None,
) -> Chart:
    """Pivot a query result into a chart.

    ``x`` names the column along the x axis, ``series`` the columns that separate
    one line from another. Everything else in the query has already been filtered
    by ``s``, which is the same filter the table uses -- a chart is a view of a
    query, not a second way to ask.

    ``order`` names the series in the order they are drawn, and it is how a line
    keeps its colour. Sorted alphabetically, hiding one model renumbers the rest
    and every remaining line changes colour under the legend that was just
    clicked.
    """
    records = result.get("records") or []
    keys = tuple(series)

    x_values = sorted({float(r[x]) for r in records if r.get(x) is not None})
    tables: dict[tuple, dict[float, float | None]] = {}
    for record in records:
        if record.get(x) is None:
            continue
        key = tuple(str(record.get(c)) for c in keys) if keys else ("",)
        tables.setdefault(key, {})[float(record[x])] = record.get("value")

    wanted = [one if isinstance(one, tuple) else (str(one),) for one in order]
    walking = [k for k in wanted if k in tables]
    walking += [k for k in sorted(tables) if k not in walking]

    built = []
    for key in walking:
        table = tables[key]
        built.append(Series(
            label=" / ".join(part for part in key if part),
            points=tuple((xv, table.get(xv)) for xv in x_values),
        ))

    meta = result.get("meta") or {}
    metric = meta.get("label") or meta.get("method") or "value"
    return Chart(
        title=title or f"{metric} by {axis_label(x)}",
        x_label=axis_label(x),
        y_label=y_label or metric,
        series=tuple(built),
        note=_filters_note(meta.get("s") or {}),
    )


def _filters_note(filters: dict) -> str:
    if not filters:
        return ""
    return ", ".join(f"{k}={_short(v)}" for k, v in sorted(filters.items()))


def _short(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_short(v) for v in value) + "]"
    return str(value)


# ======================================================================
# the renderer
# ======================================================================
def line_chart(
    chart: Chart,
    *,
    width: int = 760,
    height: int = 440,
    y_min: float | None = None,
    y_max: float | None = None,
    kind: str = "line",
) -> str:
    """One x axis, one y axis, one line -- or one group of bars -- per series.

    Returns an SVG document.

    ``kind`` is ``line`` or ``bar``. The two differ in more than a mark: a bar
    carries its length as meaning, so the axis it is drawn against has to start
    somewhere the length means something. A bar chart from 0.80 to 0.93 makes a
    three point difference look like a five-fold one.

    The legend is not drawn here. It lives on the page, beside the line/bar
    choice, where it can be clicked: a key that cannot be used to hide a line is
    a key with one job less than the reader expects of it.
    """
    if kind not in ("line", "bar"):
        raise ValueError(f"unknown chart kind {kind!r}; line or bar")
    top = 48 if chart.title else 22
    # A cell of a grid carries neither label -- the grid owns them -- so it must
    # not pay for the room they would have needed.
    bottom = 62 if chart.x_label else 40
    left = 76 if chart.y_label else 58
    right = 22
    # The plot is what is left between the two margins. Getting this wrong is not
    # a cosmetic matter: the plot grows by the left margin, everything to its
    # right shifts with it, and the legend walks off the canvas.
    #
    # ``height - top - bottom``, not ``height - bottom``: the box starts at
    # ``top``, so subtracting only the bottom margin left ``bottom - top`` = 14 px
    # under the plot for a row of tick labels and the axis name together. The
    # labels ran eight pixels past the canvas and were clipped by it -- for a long
    # time, and only visibly once the font grew and the axis started labelling its
    # ends.
    plot = _Box(left, top, width - left - right, height - top - bottom)

    ys = chart.values()
    if kind == "bar" and ys:
        # The bar's length is the quantity. Fitting the axis to the data would
        # make the length mean nothing, so 0 comes along whether or not the data
        # goes near it.
        ys = [*ys, 0.0]
    if y_min is None or y_max is None:
        fitted_lo, fitted_hi, step = _axis(ys)
        y_min = fitted_lo if y_min is None else y_min
        y_max = fitted_hi if y_max is None else y_max
        if y_min != fitted_lo or y_max != fitted_hi:
            # A hand-set bound breaks the snap, so the ticks are re-spaced to
            # match what was asked for instead of the range that was replaced.
            step = _nice_step((y_max - y_min) / 5)
    else:
        step = _nice_step((y_max - y_min) / 5)
    if y_max <= y_min:
        y_max = y_min + 1.0
        step = _nice_step((y_max - y_min) / 5)

    xs = chart.x_values()
    if xs:
        # A little air at both ends, so the first and last markers are not sitting
        # on the frame.
        pad = (max(xs) - min(xs)) * 0.03 or 0.5
        x_lo, x_hi = min(xs) - pad, max(xs) + pad
    else:
        x_lo, x_hi = 0.0, 1.0

    def sx(value: float) -> float:
        return plot.x0 + (value - x_lo) / (x_hi - x_lo) * plot.w

    def sy(value: float) -> float:
        # Clamped, so a value outside an explicitly fixed range lands on the
        # boundary rather than outside the drawing.
        value = min(max(value, y_min), y_max)
        return plot.y1 - (value - y_min) / (y_max - y_min) * plot.h

    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">',
        f"<title>{_esc(chart.title)}</title>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="26" font-family="{FONT}" font-size="{fs(15)}" '
        f'font-weight="600" fill="#111827">{_esc(chart.title)}</text>',
    ]
    # ``chart.note`` is deliberately not drawn. It is a sentence about the query,
    # not about the data, and a line of grey text across the plot is the one thing
    # that makes a chart not worth saving on its own. The page prints it under the
    # picture instead.

    # --- gridlines and the y axis -------------------------------------
    for tick in _ticks(y_min, y_max, step):
        y = sy(tick)
        out.append(
            f'<line x1="{plot.x0:.1f}" y1="{y:.1f}" x2="{plot.x1:.1f}" y2="{y:.1f}" '
            f'stroke="#e5e7eb" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{plot.x0 - 10:.1f}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="{FONT}" font-size="{fs(11)}" fill="#6b7280">'
            f"{_esc(_tick_text(tick, step, y_max - y_min))}</text>"
        )

    # --- the x axis ---------------------------------------------------
    x_step = _x_step(xs)
    for tick in _x_ticks(x_lo, x_hi, xs):
        x = sx(tick)
        out.append(
            f'<line x1="{x:.1f}" y1="{plot.y1:.1f}" x2="{x:.1f}" '
            f'y2="{plot.y1 + 5:.1f}" stroke="#9ca3af" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{plot.y1 + 19:.1f}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="{fs(11)}" fill="#6b7280">'
            f"{_esc(_tick_text(tick, x_step, x_hi - x_lo))}</text>"
        )

    out.append(
        f'<line x1="{plot.x0:.1f}" y1="{plot.y1:.1f}" x2="{plot.x1:.1f}" '
        f'y2="{plot.y1:.1f}" stroke="#374151" stroke-width="1.2"/>'
    )
    out.append(
        f'<line x1="{plot.x0:.1f}" y1="{plot.y0:.1f}" x2="{plot.x0:.1f}" '
        f'y2="{plot.y1:.1f}" stroke="#374151" stroke-width="1.2"/>'
    )
    out.append(
        f'<text x="{plot.x0 + plot.w / 2:.1f}" y="{height - 16}" text-anchor="middle" '
        f'font-family="{FONT}" font-size="{fs(12)}" fill="#374151">{_esc(chart.x_label)}</text>'
    )
    out.append(
        f'<text x="18" y="{plot.y0 + plot.h / 2:.1f}" text-anchor="middle" '
        f'font-family="{FONT}" font-size="{fs(12)}" fill="#374151" '
        f'transform="rotate(-90 18 {plot.y0 + plot.h / 2:.1f})">{_esc(chart.y_label)}</text>'
    )

    if chart.is_empty():
        out.append(
            f'<text x="{plot.x0 + plot.w / 2:.1f}" y="{plot.y0 + plot.h / 2:.1f}" '
            f'text-anchor="middle" font-family="{FONT}" font-size="{fs(13)}" fill="#9ca3af">'
            "no samples for this selection</text>"
        )

    # --- the marks ----------------------------------------------------
    if kind == "bar":
        out.extend(_bars(chart, plot, sx, sy, x_lo, x_hi))
    else:
        for index, series in enumerate(chart.series):
            colour = PALETTE[index % len(PALETTE)]
            out.append(
                f'<g stroke="{colour}" fill="{colour}" stroke-width="1.8" '
                f'stroke-linejoin="round" stroke-linecap="round">'
            )
            for run in _runs(series):
                points = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in run)
                if len(run) > 1:
                    out.append(f'<polyline points="{points}" fill="none"/>')
                for x, y in run:
                    out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3"/>')
            out.append("</g>")

    # No legend on the canvas: it belongs beside the line/bar choice, where it can
    # be clicked to hide a model. Two legends would be two places to disagree
    # about a colour.

    out.append("</svg>")
    return "\n".join(out) + "\n"


FONT = "Helvetica, Arial, sans-serif"

def _bars(chart: Chart, plot: _Box, sx, sy, x_lo: float, x_hi: float) -> list[str]:
    """One bar per (series, x), grouped inside the x slot.

    A bar with nothing behind it is left out rather than drawn at zero height:
    at zero the reader cannot tell it from a real score of 0, and those mean
    opposite things.
    """
    out: list[str] = []
    n = max(1, len(chart.series))
    # The slot is the gap between one x and the next, not the whole range: leads
    # of 30, 60, 90 and 120 span 90 minutes and sit 30 apart, and dividing by 90
    # gives bars three pixels wide with the room of a fifteenth of a slot.
    xs = sorted({x for x in chart.x_values()})
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b > a]
    step = min(gaps) if gaps else (x_hi - x_lo)
    span = max(1e-9, x_hi - x_lo)
    slot = max(4.0, plot.w * step / span)
    # Capped: two series over four leads should not become four slabs.
    bar_w = max(2.5, min(34.0, slot * 0.74 / n))
    baseline = sy(0.0)

    for index, series in enumerate(chart.series):
        colour = PALETTE[index % len(PALETTE)]
        for x, y in series.defined:
            centre = sx(x) - (n - 1) / 2 * bar_w + index * bar_w
            top = sy(y)
            # A value at 0 has no bar to draw, and drawing an invisible one would
            # put a shape in the DOM that says nothing.
            if abs(top - baseline) < 0.5:
                continue
            out.append(
                f'<rect x="{centre:.1f}" y="{min(top, baseline):.1f}" '
                f'width="{bar_w:.1f}" height="{abs(top - baseline):.1f}" '
                f'fill="{colour}" fill-opacity="0.9"/>'
            )
    out.append(
        f'<line x1="{plot.x0:.1f}" y1="{baseline:.1f}" x2="{plot.x1:.1f}" '
        f'y2="{baseline:.1f}" stroke="#374151" stroke-width="1"/>'
    )
    return out


@dataclass(frozen=True)
class _Box:
    x0: float
    y0: float
    w: float
    h: float

    @property
    def x1(self) -> float:
        return self.x0 + self.w

    @property
    def y1(self) -> float:
        return self.y0 + self.h


def _runs(series: Series) -> list[list[tuple[float, float]]]:
    """The series split into stretches with no gap in them."""
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for x, y in series.points:
        if y is None:
            if current:
                runs.append(current)
            current = []
        elif _finite(x, y):
            current.append((x, y))
    if current:
        runs.append(current)
    return runs


def _finite(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def _axis(values: Iterable[float]) -> tuple[float, float, float]:
    """A y range with its ends on round numbers, and the step between them.

    Snapping outward rather than padding by a percentage: the tick marks then land
    on the corners of the plot instead of stopping short of it, which is the
    difference between an axis and a guess.

    The bounds are rounded because ``floor(0.6 / 0.1) * 0.1`` is
    ``0.6000000000000001``, and a gridline that misses its own axis corner by a
    floating-point step is a gridline that is simply not drawn.
    """
    usable = [v for v in values if _finite(v)]
    if not usable:
        return 0.0, 1.0, 0.25
    lo, hi = min(usable), max(usable)
    if hi == lo:
        pad = abs(hi) * 0.1 or 0.5
        lo, hi = lo - pad, hi + pad
    step = _nice_step((hi - lo) / 5)
    return (
        round(math.floor(lo / step) * step, 10),
        round(math.ceil(hi / step) * step, 10),
        step,
    )


def _ticks(lo: float, hi: float, step: float) -> list[float]:
    """Ticks from index rather than by accumulation.

    Adding the step repeatedly drifts -- five steps of 0.05 from 0.7 land at
    0.9000000000000002 -- and the drift decides whether the last gridline is drawn.

    Every tick is inside ``[lo, hi]``. Rounding outwards instead put a tick at
    200 on an axis that ends at 180 and another at 0 on one that starts at 10:
    both were drawn, both were clipped, and the second one printed itself over
    the y axis it does not belong to.
    """
    if step <= 0 or not math.isfinite(step):
        step = _nice_step((hi - lo) / 5)
    first = math.ceil(lo / step - 1e-9)
    last = math.floor(hi / step + 1e-9)
    if last < first:
        # No multiple of the step is inside the range. Its own ends are the only
        # honest ticks there are.
        return [round(lo, 10), round(hi, 10)]
    return [round((first + k) * step, 10) for k in range(min(last - first + 1, 40))]


def _x_step(values: Sequence[float]) -> float:
    """The finest gap on the x axis, for deciding how many decimals to print."""
    distinct = sorted(set(values))
    if len(distinct) < 2:
        return 0.0
    return min(b - a for a, b in zip(distinct, distinct[1:]))


def _x_ticks(lo: float, hi: float, values: Sequence[float]) -> list[float]:
    """Ticks along x, with both ends of the *data* among them.

    Snapping to round numbers alone left an axis running 10 to 180 labelled 50,
    100 and 150 -- nothing at either end, which reads as an axis whose labels
    have been cut off rather than one that stops where the data does. ``lo`` and
    ``hi`` are the padded axis ends and only choose the step; the ticks sit on the
    data, inside the padding, where a centred label has room.
    """
    distinct = sorted(set(values))
    if not distinct:
        return []
    if len(distinct) <= MAX_ORDINAL_TICKS:
        return distinct
    step = _nice_step((hi - lo) / 6)
    first, last = distinct[0], distinct[-1]
    if last <= first:
        return [first]
    return [first, *[t for t in _ticks(first, last, step) if first < t < last], last]


def _nice_step(raw: float) -> float:
    if raw <= 0 or not math.isfinite(raw):
        return 1.0
    magnitude = 10.0 ** math.floor(math.log10(raw))
    for multiple in (1.0, 2.0, 2.5, 5.0, 10.0):
        if magnitude * multiple >= raw:
            return magnitude * multiple
    return magnitude * 10.0


def _tick_text(tick: float, step: float, fallback_span: float) -> str:
    """Enough decimals to tell neighbouring ticks apart, and no more.

    From the *step*, not from each value: trimming trailing zeros per tick gives
    an axis reading ``1``, ``1.05``, ``1.1`` — three formats on one scale.
    """
    if step > 0 and math.isfinite(step):
        decimals = max(0, -math.floor(math.log10(step) + 1e-9))
        return f"{tick:.{decimals}f}" if decimals else f"{tick:.0f}"
    if abs(fallback_span) >= 100:
        return f"{tick:.0f}"
    return f"{tick:.3g}"


def _esc(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
