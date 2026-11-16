"""Pictures for the completeness table: a daily curve, observation bars, a mesh.

Rendered server-side like every other chart in this module, but delivered inline
rather than behind an ``<img>``: a point on the curve has to be clickable to open
the day underneath it, and an image cannot carry a click target. Each mark that
the page acts on therefore carries its own ``data-`` attribute.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .chart import _esc

INK = "#1c2331"
MUTED = "#67728a"
LINE = "#dce2ec"
GRID = "#eef1f6"
OK = "#1a7f52"
OK_SOFT = "#e8f6ee"
BAD = "#b42318"
BAD_SOFT = "#fdecea"
ACCENT = "#2a6df4"


def _head(width: int, height: int, title: str | None) -> list[str]:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" font-family="-apple-system, '
        f'Segoe UI, Roboto, sans-serif" font-size="11">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    if title:
        out.append(f'<text x="10" y="16" font-size="12" font-weight="600" '
                   f'fill="{INK}">{_esc(title)}</text>')
    return out


def _axis_box(width: int, height: int, *, top: int, right: int, bottom: int, left: int):
    return left, top, width - left - right, height - top - bottom


def daily_curve_svg(series: dict[str, Any], *, width: int = 560, height: int = 200,
                    title: str | None = None) -> str:
    """One point per day, y from 0 to 100 %, each point clickable."""
    days = series.get("days") or []
    l, t, w, h = _axis_box(width, height, top=36, right=16, bottom=32, left=42)
    out = _head(width, height, title)
    if not days:
        out.append(f'<text x="{width // 2}" y="{height // 2}" text-anchor="middle" '
                   f'fill="{MUTED}">No days recorded</text></svg>')
        return "".join(out)

    # grid + y labels
    for pct in (0, 25, 50, 75, 100):
        y = t + h - h * pct / 100.0
        out.append(f'<line x1="{l}" y1="{y:.1f}" x2="{l + w}" y2="{y:.1f}" '
                   f'stroke="{GRID if pct not in (0, 100) else LINE}"/>')
        out.append(f'<text x="{l - 6}" y="{y + 3:.1f}" text-anchor="end" '
                   f'fill="{MUTED}">{pct}</text>')

    n = len(days)
    def px(i: int) -> float:
        return l + (w * (i + 0.5) / n if n else w / 2)

    def py(pct: float | None) -> float:
        value = 0.0 if pct is None else max(0.0, min(100.0, pct))
        return t + h - 2 - (h - 4) * value / 100.0

    points = " ".join(f"{px(i):.1f},{py(d['pct']):.1f}" for i, d in enumerate(days))
    out.append(f'<polyline points="{points}" fill="none" stroke="{ACCENT}" stroke-width="1.6"/>')
    for i, day in enumerate(days):
        pct = day["pct"]
        colour = OK if (pct or 0) >= 99.5 else BAD if (pct or 0) < 90 else "#b26a00"
        out.append(
            f'<circle class="day-point" cx="{px(i):.1f}" cy="{py(pct):.1f}" r="3.2" '
            f'fill="{colour}" stroke="#ffffff" stroke-width="1" '
            f'data-date="{_esc(day["date"])}" data-pct="{pct if pct is not None else ""}">'
            f'<title>{_esc(day["date"])}: {pct}% ({day["done"]}/{day["expected"]})</title>'
            f'</circle>')

    # x labels: first, last and a few between
    every = max(1, n // 6)
    for i in range(0, n, every):
        out.append(f'<text x="{px(i):.1f}" y="{t + h + 16}" text-anchor="middle" '
                   f'fill="{MUTED}">{_esc(days[i]["date"][5:])}</text>')
    if (n - 1) % every:
        out.append(f'<text x="{px(n - 1):.1f}" y="{t + h + 16}" text-anchor="middle" '
                   f'fill="{MUTED}">{_esc(days[-1]["date"][5:])}</text>')
    out.append("</svg>")
    return "".join(out)


def obs_mesh_svg(detail: dict[str, Any], *, width: int = 620, height: int = 240,
                 title: str | None = None) -> str:
    """Observation frames as a grid: hours across, minutes down.

    The same 240 frames a bar chart would draw, laid out so a dropout reads as a
    block instead of a run of thin bars: "it stopped at 10:00 and came back at
    12:24" is a shape, not a count.
    """
    frames = detail.get("frames") or []
    step = max(1, int(detail.get("step_minutes") or 6))
    per_hour = max(1, 60 // step)
    hours = 24
    l, t, w, h = _axis_box(width, height, top=44, right=16, bottom=44, left=50)
    out = _head(width, height, title)
    if not frames:
        out.append(f'<text x="{width // 2}" y="{height // 2}" text-anchor="middle" '
                   f'fill="{MUTED}">No frames</text></svg>')
        return "".join(out)

    cells = [[0] * hours for _ in range(per_hour)]
    for frame in frames:
        stamp = str(frame["time"])
        hour, minute = int(stamp[11:13]), int(stamp[14:16])
        row = min(per_hour - 1, minute // step)
        if 0 <= hour < hours:
            cells[row][hour] = 1 if frame["present"] else 0

    cw, ch = w / hours, h / per_hour
    for r in range(per_hour):
        y = t + h - (r + 1) * ch
        minute = r * step
        out.append(f'<text x="{l - 7}" y="{y + ch / 2 + 3.5:.1f}" text-anchor="end" '
                   f'fill="{MUTED}">{minute:02d}</text>')
        for c in range(hours):
            x = l + c * cw
            present = bool(cells[r][c])
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw + 0.4:.1f}" '
                       f'height="{ch + 0.4:.1f}" fill="{OK if present else BAD_SOFT}" '
                       f'stroke="#ffffff" stroke-width="0.4">'
                       f'<title>{c:02d}:{minute:02d} — '
                       f'{"recorded" if present else "absent"}</title></rect>')

    for hour in range(0, 25, 3):
        if hour > 23:
            break
        out.append(f'<text x="{l + w * (hour + 0.5) / hours:.1f}" y="{t + h + 16}" '
                   f'text-anchor="middle" fill="{MUTED}">{hour:02d}</text>')
    out.append(f'<text x="{l - 7}" y="{t - 10}" text-anchor="end" fill="{MUTED}">min</text>')
    legend_y = t - 14
    out.append(f'<rect x="{l}" y="{legend_y}" width="9" height="9" fill="{OK}"/>')
    out.append(f'<text x="{l + 13}" y="{legend_y + 8}" fill="{MUTED}">recorded</text>')
    out.append(f'<rect x="{l + 78}" y="{legend_y}" width="9" height="9" '
               f'fill="{BAD_SOFT}" stroke="{BAD}" stroke-width="0.6"/>')
    out.append(f'<text x="{l + 91}" y="{legend_y + 8}" fill="{MUTED}">absent</text>')
    out.append(f'<text x="{width - 16}" y="{height - 12}" text-anchor="end" fill="{MUTED}">'
               f'{detail.get("done", 0)} / {detail.get("expected", 0)} recorded</text>')
    out.append("</svg>")
    return "".join(out)


def mesh_svg(detail: dict[str, Any], *, width: int = 560, height: int = 260,
              title: str | None = None) -> str:
    """Rows are lead times, columns are init hours: filled means recorded."""
    inits: Sequence[str] = detail.get("inits") or []
    leads: Sequence[int] = detail.get("leads") or []
    cells: Iterable[Sequence[int]] = detail.get("cells") or []
    l, t, w, h = _axis_box(width, height, top=44, right=16, bottom=48, left=50)
    out = _head(width, height, title)
    if not inits or not leads:
        out.append(f'<text x="{width // 2}" y="{height // 2}" text-anchor="middle" '
                   f'fill="{MUTED}">Nothing to draw</text></svg>')
        return "".join(out)

    cols, rows = len(inits), len(leads)
    cw, ch = w / cols, h / rows
    for r, lead in enumerate(leads):
        y = t + h - (r + 1) * ch
        for c in range(cols):
            x = l + c * cw
            present = bool(cells[r][c]) if r < len(cells) else False
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw + 0.4:.1f}" '
                       f'height="{ch + 0.4:.1f}" fill="{OK if present else BAD_SOFT}" '
                       f'stroke="#ffffff" stroke-width="0.4">'
                       f'<title>{_esc(inits[c][11:16])} +{lead} min: '
                       f'{"recorded" if present else "absent"}</title></rect>')
        if r % 3 == 0 or r == rows - 1:
            out.append(f'<text x="{l - 7}" y="{y + ch / 2 + 3.5:.1f}" text-anchor="end" '
                       f'fill="{MUTED}">{lead}</text>')

    for hour in range(0, 25, 6):
        x = l + w * hour / 24.0
        out.append(f'<text x="{x:.1f}" y="{t + h + 16}" text-anchor="middle" '
                   f'fill="{MUTED}">{hour:02d}</text>')
    out.append(f'<text x="{l - 7}" y="{t - 10}" text-anchor="end" fill="{MUTED}">lead</text>')
    # legend: two swatches, so the colour has a key
    legend_y = t - 14
    out.append(f'<rect x="{l}" y="{legend_y}" width="9" height="9" fill="{OK}"/>')
    out.append(f'<text x="{l + 13}" y="{legend_y + 8}" fill="{MUTED}">recorded</text>')
    out.append(f'<rect x="{l + 78}" y="{legend_y}" width="9" height="9" fill="{BAD_SOFT}" stroke="{BAD}" stroke-width="0.6"/>')
    out.append(f'<text x="{l + 91}" y="{legend_y + 8}" fill="{MUTED}">absent</text>')
    out.append(f'<text x="{width - 16}" y="{height - 12}" text-anchor="end" fill="{MUTED}">'
               f'{detail.get("done", 0)} / {detail.get("expected", 0)} recorded</text>')
    out.append("</svg>")
    return "".join(out)
