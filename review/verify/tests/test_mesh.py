"""The 时间 x 时效 mesh, read back as a document.

A grid is a document that fails quietly in one extra way a line chart does not:
a cell with nothing behind it can be painted the same colour as a real value, or
left out of the grid entirely so the axis closes up around it. Either one turns
"this run produced nothing" into "this run was fine", which is the one thing a
verification chart must never say.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from nib_verify.report.mesh import (
    BETTER_HIGH,
    BETTER_LOW,
    GAP_FILL,
    Mesh,
    Panel,
    _colour,
    _short_time,
    mesh_svg,
)
from nib_verify.store.db import Store

SVG = "{http://www.w3.org/2000/svg}"


def mesh(**over) -> Mesh:
    base = dict(
        title="a mesh",
        x_label="init time",
        y_label="lead minutes",
        x_values=("2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"),
        y_values=(10.0, 20.0),
        panels=(
            Panel("alpha", {(0, 0): 0.5, (0, 1): None, (1, 0): 0.9}),
            Panel("beta", {(0, 0): 0.4, (1, 0): 0.8, (1, 1): 0.7}),
        ),
        method="ts",
        complete=False,
    )
    base.update(over)
    return Mesh(**base)


def fills(svg: str) -> list[str]:
    return [r.get("fill") for r in ET.fromstring(svg).iter(f"{SVG}rect")]


def labels(svg: str) -> list[str]:
    return ["".join(t.itertext()) for t in ET.fromstring(svg).iter(f"{SVG}text")]


# ======================================================================
# it parses
# ======================================================================
def test_the_mesh_is_well_formed_xml():
    """Not a string check.

    An SVG is served as ``image/svg+xml`` and parsed as XML, which defines five
    entities: amp, lt, gt, quot, apos. An arrow written ``&uarr;`` is a parse
    error, and a parse error aborts the document *from that point* -- so one
    arrow cost the colour bar that came after it, and the chart looked like a
    chart that had simply been designed without one.
    """
    ET.fromstring(mesh_svg(mesh()))


def test_a_label_with_xml_in_it_does_not_break_the_document():
    """Member names come from a package manifest, and a manifest is a file
    somebody else wrote."""
    svg = mesh_svg(mesh(panels=(
        Panel("a & b <c>", {(0, 0): 0.5}),
        Panel('quote " and \' apostrophe', {(0, 0): 0.5}),
    )))
    root = ET.fromstring(svg)
    drawn = ["".join(t.itertext()) for t in root.iter(f"{SVG}text")]
    assert "a & b <c>" in drawn
    assert 'quote " and \' apostrophe' in drawn


@pytest.mark.parametrize("method", sorted(BETTER_HIGH | BETTER_LOW | {"bias"}))
def test_every_metric_the_module_knows_still_produces_a_document(method):
    ET.fromstring(mesh_svg(mesh(method=method)))


# ======================================================================
# a gap is a gap
# ======================================================================
def test_a_cell_with_nothing_behind_it_is_not_painted_like_a_value():
    """The failure mode this whole renderer exists to avoid.

    ``None`` means the store had a row and it had no samples. It must not be the
    bottom of the colour ramp, which is what a real low score looks like.
    """
    svg = mesh_svg(mesh())
    grey = [f for f in fills(svg) if f == GAP_FILL]
    assert grey, "the gap cell was painted as a value"
    # and the bottom of the ramp is not the gap colour, or the two would be
    # indistinguishable on the chart
    assert _colour(0.0, 0.0, 1.0) != GAP_FILL


def test_a_column_the_plan_asked_for_and_never_answered_is_still_a_column():
    """Widened axis: the caller hands in every lead the plan contains.

    Without this the cell would simply not be drawn, and a reader comparing two
    panels would see both of them whole.
    """
    whole = mesh(x_values=("t0", "t1"), y_values=(10.0, 20.0), complete=True,
                 panels=(Panel("alpha", {(0, 0): 0.5, (0, 1): 0.5,
                                         (1, 0): 0.5, (1, 1): 0.5}),))
    holed = mesh()
    assert len(whole.panels[0].cells) == 4
    assert len(holed.panels[0].cells) < 4
    assert GAP_FILL in fills(mesh_svg(holed))


def test_the_caption_says_what_the_grey_means():
    assert "a gap, not a zero" in " ".join(labels(mesh_svg(mesh())))


def test_a_complete_mesh_does_not_talk_about_gaps():
    svg = mesh_svg(mesh(complete=True, panels=(
        Panel("alpha", {(0, 0): 0.5, (0, 1): 0.5, (1, 0): 0.5, (1, 1): 0.5}),)))
    assert "a gap, not a zero" not in " ".join(labels(svg))


# ======================================================================
# what carries which dimension
# ======================================================================
def test_the_models_are_panels_and_not_colours():
    """Two models means two grids. Spending the colour channel on a dimension
    with three values would leave nothing for the score."""
    svg = mesh_svg(mesh())
    drawn = labels(svg)
    assert "alpha" in drawn and "beta" in drawn
    # the colour ramp is still doing the score: more than two fills in it
    ramp = {f for f in fills(svg) if f and f != GAP_FILL and f != "none"
            and f != "white"}
    assert len(ramp) > 2, ramp


def test_the_colour_bar_is_drawn_and_labelled():
    drawn = labels(mesh_svg(mesh()))
    assert any("darker is better" == d for d in drawn), drawn
    assert any(d == "ts" for d in drawn), drawn
    # four tick labels plus the two captions
    numbers = [d for d in drawn if d.replace(".", "").replace("-", "").isdigit()]
    assert len(numbers) >= 5, numbers


def test_a_metric_with_no_good_end_says_nothing_about_one():
    """``bias`` is best at 1 and ``me`` at 0, so neither end is the good end and
    claiming one would be worse than saying nothing."""
    assert mesh(method="bias").direction == ""
    assert mesh(method="ts").direction == "darker is better"
    assert mesh(method="mae").direction == "paler is better"


# ======================================================================
# nothing to draw
# ======================================================================
def test_an_empty_mesh_says_so_rather_than_drawing_a_blank_rectangle():
    svg = mesh_svg(mesh(panels=(), x_values=(), y_values=()))
    ET.fromstring(svg)
    drawn = " ".join(labels(svg))
    assert "no stored" in drawn
    assert "a mesh" in drawn


def test_init_times_are_shortened_but_not_invented():
    assert _short_time("2026-01-01T04:00:00Z") == "01-01 04Z"
    assert _short_time("anything else") == "anything else"


def test_a_single_valued_ramp_does_not_divide_by_zero():
    """One distinct value is a real answer -- every lead scored the same."""
    svg = mesh_svg(mesh(panels=(Panel("alpha", {(0, 0): 0.5, (0, 1): 0.5,
                                               (1, 0): 0.5, (1, 1): 0.5}),)))
    ET.fromstring(svg)
    assert "nan" not in svg.lower()


# ======================================================================
# the axis comes from the plan
# ======================================================================
def test_the_axes_come_from_what_was_asked_for_not_from_what_came_back(tmp_path):
    """An init time that produced nothing has no result row.

    Taking the axis from the results, that column does not exist -- so the chart
    closes the gap up and reads as complete. The plan is the only thing that
    still knows the run was supposed to happen, and a missing run is exactly
    what somebody opening this chart is looking for.
    """
    with Store(tmp_path / "v.sqlite") as store:
        for init in ("2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z",
                     "2026-01-01T02:00:00Z"):
            for lead in (10, 20):
                store.set_task_state("g", "m", init, lead, "unavailable")
        # and only two of the three produced anything
        for init in ("2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"):
            store.upsert("yes_or_no",
                         {"group_id": "g", "member": "m", "init_time": init,
                          "valid_time": init, "lead_minutes": 10, "level": 35.0},
                         {"H": 1, "F": 0, "M": 0, "C": 0})
        store.commit()

        times, leads = store.task_axes("g")
        assert times == ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z",
                         "2026-01-01T02:00:00Z"]
        assert leads == [10, 20]
        # the third one is in the axes and has nothing under it
        assert store.count_rows("yes_or_no", filters={"group_id": "g"}) == 2
