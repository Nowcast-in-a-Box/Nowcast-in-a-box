"""The chart, read back as what it is: a document.

An SVG that is wrong is usually wrong *silently*. A coordinate of ``nan`` does
not raise — it makes the element vanish, and the chart is then merely emptier
than it should be. A legend that runs off the right edge looks fine to whoever
wrote it. A tick that misses its own axis corner is not drawn, and the axis
quietly starts one gridline lower than it claims.

None of that is visible to a person reading the source, so it is asserted here
instead: the document is parsed, and the geometry is checked against the canvas.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import pytest

from nib_verify.report import Series, chart_for, from_query, line_chart
from nib_verify.report.chart import CHAR_W, _axis, _nice_step, _ticks

SVG = "{http://www.w3.org/2000/svg}"


def texts(svg: str):
    return ET.fromstring(svg).findall(f"{SVG}text")


def labels(svg: str) -> list[str]:
    return ["".join(t.itertext()) for t in texts(svg)]


def geometry(svg: str):
    """Every drawn coordinate, whichever attribute carries it."""
    root = ET.fromstring(svg)
    for element in root.iter():
        tag = element.tag.replace(SVG, "")
        if tag == "circle":
            for key in ("cx", "cy"):
                yield tag, key, float(element.get(key))
        elif tag in ("line", "rect"):
            for key in ("x1", "x2", "y1", "y2"):
                if element.get(key) is not None:
                    yield tag, key, float(element.get(key))
        elif tag == "polyline":
            for point in element.get("points").split():
                x, y = point.split(",")
                yield tag, "point", float(x)
                yield tag, "point", float(y)


def chart(*series) -> "object":
    from nib_verify.report import Chart

    return Chart("a title", "lead minutes", "value", tuple(series))


def simple(values, xs=(10.0, 20.0, 30.0, 60.0)) -> "object":
    return Series("mlcast-det", tuple(zip(xs, values)))


# ======================================================================
# it is a document
# ======================================================================
def test_the_output_parses_as_xml():
    root = ET.fromstring(line_chart(chart(simple([0.5, 0.6, 0.7, 0.8]))))
    assert root.tag == f"{SVG}svg"
    assert root.get("viewBox") == "0 0 760 440"


def test_the_size_can_be_asked_for():
    root = ET.fromstring(line_chart(chart(simple([1, 2])), width=1200, height=300))
    assert root.get("width") == "1200"
    assert root.get("height") == "300"


def test_a_title_with_markup_in_it_is_escaped():
    from nib_verify.report import Chart

    svg = line_chart(Chart('a & b <c> "d"', "x", "y", (simple([1, 2]),)))
    ET.fromstring(svg)                       # would raise if it were not escaped
    assert "a &amp; b &lt;c&gt;" in svg
    assert "a & b <c>" not in svg


@pytest.mark.parametrize("values", [
    [0.5, 0.6, 0.7, 0.8],
    [None, None, None, None],
    [1e-9, 1e9, -3.0, 0.0],
    [0.0, 0.0, 0.0, 0.0],
    [float("nan"), 1.0, float("inf"), 2.0],
    [0.5],
])
def test_no_coordinate_is_ever_nan_or_infinite(values):
    """``x="nan"`` does not warn, it disappears. Every coordinate is checked."""
    svg = line_chart(chart(Series("m", tuple(zip((10.0, 20.0, 30.0, 60.0), values)))))
    for tag, key, value in geometry(svg):
        assert math.isfinite(value), f"{tag}.{key} = {value}"


def test_a_nan_value_is_treated_as_missing_rather_than_drawn():
    svg = line_chart(chart(simple([0.5, float("nan"), 0.7, 0.8])))
    for tag, key, value in geometry(svg):
        assert math.isfinite(value)


def test_everything_drawn_is_inside_the_canvas():
    """This is the one a person cannot see. The legend is laid out to the right
    of the plot, and an off-by-one-margin mistake there puts it off the edge."""
    width, height = 760, 440
    svg = line_chart(chart(
        Series("mlcast-det", ((10.0, 0.5), (20.0, 0.6), (30.0, 0.7), (60.0, 0.8))),
        Series("a-very-long-member-name-indeed", ((10.0, 0.4), (20.0, 0.5), (30.0, 0.6), (60.0, 0.7))),
    ), width=width, height=height)

    for tag, key, value in geometry(svg):
        assert 0 <= value <= width, f"{tag}.{key} = {value}"
        if key in ("y1", "y2", "cy"):
            assert 0 <= value <= height, f"{tag}.{key} = {value}"

    for element in texts(svg):
        if element.get("transform"):
            continue                          # the rotated axis label
        size = float(element.get("font-size", 11))
        length = len("".join(element.itertext())) * size * 0.6
        x = float(element.get("x"))
        anchor = element.get("text-anchor", "start")
        left = x - length if anchor == "end" else (x - length / 2 if anchor == "middle" else x)
        assert left >= -1, f"text starts at {left}: {''.join(element.itertext())!r}"
        assert left + length <= width + 1, (
            f"text runs to {left + length:.0f} on a {width} canvas: "
            f"{''.join(element.itertext())!r}"
        )


def test_a_label_too_long_for_the_legend_is_clipped_not_dropped():
    svg = line_chart(chart(
        Series("m" * 80, ((10.0, 1.0), (20.0, 2.0))),
    ))
    shown = [t for t in labels(svg) if t.startswith("m")]
    assert shown, "the series disappeared from the legend"
    assert len(shown[0]) < 80
    assert shown[0].endswith("\u2026")


# ======================================================================
# a gap is a gap
# ======================================================================
def test_a_missing_value_breaks_the_line_instead_of_being_drawn_across():
    """An undefined CSI and a CSI of zero mean opposite things. A line drawn
    straight through the gap says the forecast decayed and recovered."""
    xs = (10.0, 20.0, 30.0, 60.0, 70.0)
    svg = line_chart(chart(Series("m", tuple(zip(xs, [0.5, 0.6, None, 0.8, 0.9])))))
    root = ET.fromstring(svg)
    assert len(root.findall(f".//{SVG}polyline")) == 2, "two stretches, not one line"
    assert len(root.findall(f".//{SVG}circle")) == 4, "the four defined points"


def test_a_lone_point_between_two_gaps_is_a_dot():
    xs = (10.0, 20.0, 30.0, 60.0, 70.0)
    svg = line_chart(chart(Series("m", tuple(zip(xs, [None, 0.5, None, 0.8, 0.9])))))
    root = ET.fromstring(svg)
    assert len(root.findall(f".//{SVG}circle")) == 3
    assert len(root.findall(f".//{SVG}polyline")) == 1      # only (60, 70)


def test_a_point_with_no_neighbours_is_a_dot_not_a_line():
    svg = line_chart(chart(simple([0.5, None, None, None])))
    assert ET.fromstring(svg).findall(f".//{SVG}polyline") == []
    assert len(ET.fromstring(svg).findall(f".//{SVG}circle")) == 1


def test_the_legend_says_a_line_has_a_gap():
    svg = line_chart(chart(simple([0.5, None, 0.7, 0.8])))
    assert any(t.endswith("(gap)") for t in labels(svg))


def test_the_legend_does_not_claim_a_gap_that_is_not_there():
    svg = line_chart(chart(simple([0.5, 0.6, 0.7, 0.8])))
    assert not any(t.endswith("(gap)") for t in labels(svg))


def test_the_gap_marker_survives_a_long_name():
    """The suffix is part of the legend, so the room for it is reserved rather
    than spent on the name."""
    svg = line_chart(chart(Series("a-fairly-long-member-name",
                                 ((10.0, 0.5), (20.0, None), (30.0, 0.7)))))
    shown = [t for t in labels(svg) if t.endswith("(gap)")]
    assert shown, labels(svg)
    # and the name is the part that gives way, which is the whole point
    assert shown[0].startswith("a-fairly-long"), shown


# ======================================================================
# nothing to draw
# ======================================================================
@pytest.mark.parametrize("series", [
    (),
    (Series("m", ()),),
    (Series("m", ((10.0, None), (20.0, None))),),
])
def test_an_empty_chart_still_renders_a_frame_and_says_so(series):
    svg = line_chart(chart(*series))
    ET.fromstring(svg)
    assert any("no samples" in t for t in labels(svg))
    assert ET.fromstring(svg).findall(f".//{SVG}polyline") == []
    for tag, key, value in geometry(svg):
        assert math.isfinite(value)


# ======================================================================
# the axes
# ======================================================================
@pytest.mark.parametrize("values", [
    [0.5, 0.8], [0.71, 0.89], [5.4, 10.1], [1.0, 1.15], [0.962, 0.989],
    [-12.0, 4.0], [0.0, 0.0], [1234.0, 5678.0],
])
def test_the_axis_bounds_are_round_numbers_a_tick_lands_on(values):
    lo, hi, step = _axis(values)
    assert lo <= min(values) and max(values) <= hi
    ticks = _ticks(lo, hi, step)
    assert ticks, "an axis with no ticks is not an axis"
    # the ends of the axis are ticks, which is the whole point of snapping
    assert ticks[0] == pytest.approx(lo)
    assert ticks[-1] == pytest.approx(hi)


@pytest.mark.parametrize("values", [[0.5, 0.8], [0.71, 0.89], [5.4, 10.1], [0.962, 0.989]])
def test_ticks_are_evenly_spaced_and_within_the_axis(values):
    lo, hi, step = _axis(values)
    ticks = _ticks(lo, hi, step)
    assert len(ticks) >= 3
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) - min(gaps) < 1e-9
    assert all(lo - 1e-9 <= t <= hi + 1e-9 for t in ticks)


def test_a_flat_series_gets_a_visible_axis_rather_than_a_zero_height_one():
    lo, hi, _ = _axis([0.5, 0.5, 0.5])
    assert hi > lo


def test_the_y_axis_can_be_pinned():
    svg = line_chart(chart(simple([0.5, 0.6, 0.7, 0.65])), y_min=0.0, y_max=1.0)
    shown = labels(svg)
    assert "0.0" in shown and "1.0" in shown


def test_a_value_outside_a_pinned_axis_is_clamped_not_drawn_outside():
    svg = line_chart(chart(simple([0.5, 9.9, 0.7, 0.8])), y_min=0.0, y_max=1.0)
    root = ET.fromstring(svg)
    for circle in root.findall(f".//{SVG}circle"):
        x, y = float(circle.get("cx")), float(circle.get("cy"))
        assert 0 <= x <= 760
        assert 0 <= y <= 440
    # and the ones that were clamped are on the boundary rather than in the margin
    assert any(abs(float(c.get("cy")) - 48) < 1.0 for c in root.findall(f".//{SVG}circle"))


@pytest.mark.parametrize("values", [[0.5], [0.5, 0.5]])
def test_a_single_x_value_does_not_divide_by_zero(values):
    svg = line_chart(chart(Series("m", tuple((10.0, v) for v in values))))
    for tag, key, value in geometry(svg):
        assert math.isfinite(value)


def test_the_x_axis_ticks_the_data_when_there_is_little_of_it():
    """`lead_minutes` is 10, 20, 30, 60. Round-number ticks would name values
    that were never verified."""
    svg = line_chart(chart(simple([0.5, 0.6, 0.7, 0.8])))
    for lead in ("10", "20", "30", "60"):
        assert lead in labels(svg)


@pytest.mark.parametrize("raw,expected", [
    (0.9, 1.0), (1.1, 2.0), (2.2, 2.5), (3.0, 5.0), (7.0, 10.0), (0.04, 0.05),
])
def test_steps_are_one_of_the_readable_multiples(raw, expected):
    assert _nice_step(raw) == pytest.approx(expected)


# ======================================================================
# series
# ======================================================================
def test_each_series_gets_its_own_colour():
    svg = line_chart(chart(simple([1, 2]), Series("other", ((10.0, 3), (20.0, 4)))))
    colours = [g.get("stroke") for g in ET.fromstring(svg).findall(f".//{SVG}g")]
    assert len(colours) == 2
    assert colours[0] != colours[1]


def test_the_same_series_is_drawn_twice_the_same_way():
    a = line_chart(chart(simple([0.5, 0.6, 0.7, 0.8])))
    b = line_chart(chart(simple([0.5, 0.6, 0.7, 0.8])))
    assert a == b


# ======================================================================
# from a query result
# ======================================================================
def result(records, label="Threat score (CSI)", s=None, method="ts"):
    return {"records": records, "meta": {"label": label, "method": method, "s": s or {}}}


def test_a_result_is_pivoted_into_one_line_per_series():
    got = from_query(result([
        {"member": "a", "lead_minutes": 10, "value": 0.5, "n_valid": 4},
        {"member": "a", "lead_minutes": 20, "value": 0.6, "n_valid": 4},
        {"member": "b", "lead_minutes": 10, "value": 0.7, "n_valid": 4},
        {"member": "b", "lead_minutes": 20, "value": 0.8, "n_valid": 4},
    ]), x="lead_minutes", series=("member",))

    assert [s.label for s in got.series] == ["a", "b"]
    assert got.series[0].points == ((10.0, 0.5), (20.0, 0.6))
    assert got.series[1].points == ((10.0, 0.7), (20.0, 0.8))


def test_a_series_missing_an_x_gets_a_gap_there_not_a_shorter_line():
    got = from_query(result([
        {"member": "a", "lead_minutes": 10, "value": 0.5},
        {"member": "a", "lead_minutes": 30, "value": 0.7},
        {"member": "b", "lead_minutes": 10, "value": 0.7},
        {"member": "b", "lead_minutes": 20, "value": None},      # no samples
        {"member": "b", "lead_minutes": 30, "value": 0.9},
    ]), x="lead_minutes", series=("member",))

    a = next(s for s in got.series if s.label == "a")
    b = next(s for s in got.series if s.label == "b")
    assert a.points == ((10.0, 0.5), (20.0, None), (30.0, 0.7))
    assert b.points == ((10.0, 0.7), (20.0, None), (30.0, 0.9))
    assert a.has_gap and b.has_gap


def test_two_series_columns_make_a_combined_label():
    got = from_query(result([
        {"member": "a", "half_window": 1, "lead_minutes": 10, "value": 0.5},
        {"member": "a", "half_window": 2, "lead_minutes": 10, "value": 0.6},
    ]), x="lead_minutes", series=("member", "half_window"))
    assert [s.label for s in got.series] == ["a / 1", "a / 2"]


def test_one_line_when_nothing_separates_the_series():
    got = from_query(result([
        {"lead_minutes": 10, "value": 0.5},
        {"lead_minutes": 20, "value": 0.6},
    ]), x="lead_minutes")
    assert len(got.series) == 1
    assert got.series[0].points == ((10.0, 0.5), (20.0, 0.6))


def test_a_row_with_no_x_is_dropped_rather_than_placed_at_zero():
    got = from_query(result([
        {"member": "a", "lead_minutes": None, "value": 0.5},
        {"member": "a", "lead_minutes": 20, "value": 0.6},
    ]), x="lead_minutes", series=("member",))
    assert got.series[0].points == ((20.0, 0.6),)


def test_the_title_and_axis_labels_come_from_the_metric():
    got = from_query(result([{"lead_minutes": 10, "value": 1.0}]),
                     x="lead_minutes", series=())
    assert got.title == "Threat score (CSI) by lead time (min)"
    assert got.y_label == "Threat score (CSI)"
    assert got.x_label == "lead time (min)"


def test_the_filters_are_named_on_the_chart():
    """A chart that does not say what it is a chart *of* is a decoration."""
    got = from_query(result([{"lead_minutes": 10, "value": 1.0}],
                            s={"level": [181.0], "dtime": [30]}),
                     x="lead_minutes")
    ET.fromstring(line_chart(got))
    assert "dtime=[30]" in got.note and "level=[181.0]" in got.note


def test_an_empty_result_produces_an_empty_chart_not_an_error():
    got = from_query(result([]), x="lead_minutes", series=("member",))
    assert got.is_empty()
    assert not got.series
    assert "no samples" in " ".join(labels(line_chart(got)))
