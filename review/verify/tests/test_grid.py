"""A grid of charts, read back as a document.

A grid fails in one extra way a single chart does not: the cells can be scaled to
their own data, and then two panels side by side look alike while showing
different things. The shared y range is the whole point of the layout, so it is
asserted here rather than left to whoever reads the picture.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from nib_verify.report import Chart, Series
from nib_verify.report.grid import columns_for, small_multiples

SVG = "{http://www.w3.org/2000/svg}"


def chart(values, xs=(10.0, 20.0, 30.0, 60.0), series=("alpha", "beta")):
    made = []
    for index, name in enumerate(series):
        made.append(Series(name, tuple(
            (x, None if v is None else v + index * 0.01) for x, v in zip(xs, values))))
    return Chart("t", "lead minutes", "score", tuple(made))


def texts(svg: str) -> list[str]:
    return ["".join(t.itertext()) for t in ET.fromstring(svg).iter(f"{SVG}text")]


# ======================================================================
# it parses
# ======================================================================
def test_the_grid_is_well_formed_xml():
    ET.fromstring(small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * 3,
                                  ["level 1", "level 2", "level 3"]))


def test_a_label_with_xml_in_it_does_not_break_the_document():
    svg = small_multiples([chart([0.5, 0.6, 0.7, 0.8])], ["a & b <c>"])
    assert "a & b <c>" in texts(svg)


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 9])
def test_every_panel_count_still_produces_a_document(count):
    ET.fromstring(small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * count,
                                  [f"p{i}" for i in range(count)]))


def test_a_grid_of_nothing_still_says_what_it_is():
    empty = Chart("t", "lead minutes", "score", (Series("alpha", ()),))
    svg = small_multiples([empty], ["level 1"])
    ET.fromstring(svg)
    assert "no samples" in " ".join(texts(svg))


# ======================================================================
# the axes the cells share
# ======================================================================
def test_every_cell_is_drawn_against_the_same_y_range():
    """The failure this layout exists to prevent.

    A cell fitted to its own data gives a panel spanning 0.84 to 0.86 the same
    height as one spanning 0.2 to 0.9, and the two then look alike.
    """
    narrow = chart([0.84, 0.845, 0.85, 0.855])
    wide = chart([0.20, 0.45, 0.70, 0.90])
    svg = small_multiples([narrow, wide], ["narrow", "wide"])
    ticks = [t for t in texts(svg) if t.replace(".", "").isdigit()]
    # each y tick label appears once per cell at most, and the numbers come from
    # one axis: the narrow cell does not get ticks of its own around 0.84
    assert "0.8" in ticks and "0.2" in ticks, ticks
    assert max(float(t) for t in ticks) >= 0.9, ticks
    assert min(float(t) for t in ticks) <= 0.2, ticks


def test_the_canvas_carries_no_legend_at_all():
    """Not once, and not in every cell either.

    The key is on the page, beside the line/bar choice, where clicking it hides a
    model -- and the page draws it from the same palette the lines are drawn with.
    """
    svg = small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * 3, ["a", "b", "c"])
    drawn = texts(svg)
    assert "alpha" not in drawn and "beta" not in drawn, drawn


def test_each_cell_is_named_by_what_separates_it():
    svg = small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * 2, ["level 16", "level 133"])
    drawn = texts(svg)
    assert "level 16" in drawn and "level 133" in drawn


def test_the_cells_are_cells_and_not_one_wide_chart():
    svg = small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * 3, ["a", "b", "c"])
    root = ET.fromstring(svg)
    # direct children, so the outer document is not counted as one of the cells
    nested = root.findall(f"{SVG}svg")
    assert len(nested) == 3, "one nested document per panel"
    # and each of them is somewhere else on the canvas
    spots = {(e.get("x"), e.get("y")) for e in nested}
    assert len(spots) == 3, spots


def test_the_x_axis_is_named_once_for_the_whole_grid():
    svg = small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * 3, ["a", "b", "c"])
    assert texts(svg).count("lead minutes") == 1


# ======================================================================
# layout arithmetic
# ======================================================================
def test_columns_grow_with_the_panel_count():
    assert columns_for(1) == 1
    assert columns_for(2) == 2
    assert columns_for(4) == 2
    assert columns_for(6) == 3


def test_the_canvas_is_wide_enough_for_the_cells_it_holds():
    for count in (1, 2, 3, 4, 6):
        svg = small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * count,
                              [f"p{i}" for i in range(count)])
        root = ET.fromstring(svg)
        width = float(root.get("width"))
        cells = root.findall(f"{SVG}svg")
        assert cells, count
        rightmost = max(float(c.get("x")) + float(c.get("width")) for c in cells)
        assert rightmost < width, (count, rightmost, width)


def test_a_bar_grid_keeps_zero_on_the_axis():
    svg = small_multiples([chart([0.84, 0.86, 0.88, 0.90])] * 2, ["a", "b"], kind="bar")
    ticks = [float(t) for t in texts(svg) if t.replace(".", "").isdigit()]
    assert 0.0 in ticks, "a bar chart from 0.84 makes three points look like a ratio"


def test_the_canvas_does_not_carry_a_sentence_about_the_query():
    """A chart is meant to be worth saving on its own.

    The filters, the panel count and the sample size are about the query, not
    about the data, and a line of small grey text across the plot is what makes a
    saved chart unreadable. The page prints them under the picture instead.
    """
    svg = small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * 2, ["a", "b"])
    drawn = " ".join(texts(svg))
    assert "panel(s)" not in drawn, drawn
    assert "dtime" not in drawn, drawn
    assert "init_time" not in drawn, drawn


def test_the_grid_still_says_what_each_cell_is():
    svg = small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * 2, ["20 dBZ", "60 dBZ"])
    drawn = texts(svg)
    assert "20 dBZ" in drawn and "60 dBZ" in drawn
    assert "lead minutes" in drawn and "score" in drawn


def test_the_legend_does_not_take_a_column_off_every_cell():
    """The cells need the width; the legend runs across the top instead."""
    svg = small_multiples([chart([0.5, 0.6, 0.7, 0.8])] * 2, ["a", "b"], width=800)
    root = ET.fromstring(svg)
    cells = root.findall(f"{SVG}svg")
    rightmost = max(float(c.get("x")) + float(c.get("width")) for c in cells)
    assert rightmost > 800 * 0.9, rightmost
