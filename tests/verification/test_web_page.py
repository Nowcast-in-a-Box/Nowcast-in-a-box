"""The page and the API must agree.

The interface is written in plain JavaScript with no build step, so nothing
checks at compile time that the paths it calls exist. These tests do that
instead: every ``/api/...`` the page mentions must be a route the service
registers, and every file the page loads must be served. A typo in either
direction is a blank screen in a browser, which is exactly the kind of failure
that is expensive to find by hand and cheap to find here.
"""

from __future__ import annotations

import re
from pathlib import Path

import nib_verify

import pytest

from nib_verify.catalog.hostconfig import load as load_host
from nib_verify.server import create_app

from .conftest import make_access, make_config

# From the module, not from this file: a test that counts
# directories breaks the moment it is moved to the layout
# the repository actually uses.
STATIC = Path(nib_verify.__file__).parent / "server" / "static"

#: the page writes a call either literally or as a template string with an id
#: interpolated into it, so the match has to allow the interpolated expression
API_CALL = re.compile(r"/api/[A-Za-z0-9_\-/$(){}.:]+")

PLAIN_SEGMENT = re.compile(r"[a-z][a-z0-9_\-]*")


def normalise(raw: str) -> str:
    """``/api/configs/${encodeURIComponent(id)}/roster`` -> ``/api/configs/{x}/roster``."""
    parts = [piece for piece in raw.rstrip("/").split("/") if piece]
    return "/" + "/".join(
        piece if PLAIN_SEGMENT.fullmatch(piece) else "{x}" for piece in parts
    )


@pytest.fixture
def app(platform, tmp_path):
    return create_app(
        root=tmp_path / "root",
        configs=tmp_path / "configs",
        packages=platform.packages,
        runs=[platform.runs_root],
        host=load_host(platform.config_path),
        access=make_access(),
    )


def registered_paths(app) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def page_calls() -> set[str]:
    text = (STATIC / "app.js").read_text(encoding="utf-8")
    return {normalise(match.group(0)) for match in API_CALL.finditer(text)}


# ======================================================================
def test_the_page_exists_and_is_not_empty():
    for name in ("index.html", "app.js", "style.css"):
        path = STATIC / name
        assert path.is_file(), f"{name} is missing"
        assert path.stat().st_size > 500, f"{name} looks truncated"


def test_every_api_path_the_page_calls_is_registered(app):
    """The guard that would have caught a typo in a fetch URL."""
    available = {normalise(p) for p in registered_paths(app) if "/api/" in p}
    missing = sorted(call for call in page_calls() if call not in available)
    assert not missing, (
        f"the page calls {missing}, which the service does not serve. "
        f"Registered: {sorted(available)}"
    )


def test_the_page_uses_the_endpoints_it_was_given(app):
    """A regex that matched nothing would make the test above useless.

    Not every route has to be used — some exist for scripts — but the page is
    expected to reach for the ones it is built on.

    ``/api/plan`` is deliberately absent from this list. The wizard had a Preview
    plan button and this is what it called; the button is gone. The endpoint
    stays, because it is the only way to ask what a config would do without
    starting a run, and the page itself no longer asks it.
    """
    calls = page_calls()
    assert len(calls) >= 10, sorted(calls)
    for expected in ("/api/configs", "/api/catalog", "/api/metrics",
                     "/api/runs", "/api/scores", "/api/chart"):
        assert expected in calls, expected
    assert "/api/plan" not in calls


def test_every_static_file_the_page_loads_is_served(app):
    from fastapi.testclient import TestClient

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    referenced = set(re.findall(r'(?:src|href)="(/static/[^"]+)"', html))
    referenced |= set(re.findall(r'(/api/[A-Za-z0-9_\-/]*)', html))

    with TestClient(app) as client:
        for path in sorted(referenced):
            response = client.get(path)
            assert response.status_code == 200, f"{path} -> {response.status_code}"


def test_the_page_declares_no_remote_dependencies(app):
    """The host may have no internet. Everything must come from this process."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html
    assert "cdn." not in html


def test_the_locale_is_declared():
    """A missing charset turns a non-ASCII config name into mojibake."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<meta charset="utf-8">' in html
    assert '<html lang="en">' in html


# ======================================================================
def test_the_detail_panel_uses_the_same_four_names():
    """The wizard and the panel are two views of one config; two vocabularies
    for the same four things would be worse than either."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    for heading in ('"Basic set "', '"Forecasts"', '"Metrics "'):
        assert f'h("h2", {{}}, {heading}' in app, heading
    assert '"The ruler "' not in app and '"Roster"' not in app


def test_every_static_file_on_disk_is_reachable():
    """A file nothing loads is either dead weight or a broken reference."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for path in sorted(STATIC.iterdir()):
        if path.is_file() and path.name != "index.html":
            assert path.name in html, f"{path.name} is never loaded by index.html"


def test_the_chart_is_an_image_the_page_can_load():
    """``<img src>`` and not a container to fill in from JavaScript.

    The renderer lives on the server, in one place, so the page, the command line
    and any report draw the same picture — and a chart is a URL that can be saved
    or linked to.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="chart"' in html
    assert '<img id="chart"' in html, "the chart has to be an <img>"
    assert 'id="chart-card"' in html
    # No save-the-chart link: what somebody who wants their own plot can use is the
    # table's rows, and that button sits with the table. A chart is still a URL --
    # the <img> src -- so it can be saved from the browser as it always could.
    assert 'id="chart-svg"' not in html
    assert 'id="chart-note"' in html, "what the picture is of goes under it"


def test_the_table_can_be_saved_as_csv():
    """The rows, not the picture.

    Whoever wants their own plot takes the table and draws it, in whatever tool
    they already use. And the button sits with the table it saves rather than in
    the chart card, because it is the table that is being saved.
    """
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function saveCsv" in app
    body = app[app.index("function saveCsv"):]
    body = body[: body.index("\n}\n")]
    assert "result.records" in body, "the rows already on screen, not a second query"
    # the header says what the numbers are, or the file cannot be labelled later
    for said in ("# config", "# filters", "ruler"):
        assert said in body, said
    assert "URL.createObjectURL" in body
    assert "URL.revokeObjectURL" in body, "an object URL holds its blob for good"
    # and it is offered beside the table's own summary line
    table = app[app.index("function renderScores"):]
    assert 'id: "btn-csv"' in table[: table.index("h(\"table\"")]


def test_the_chart_and_the_table_expand_by_the_same_dimensions():
    """One query, two shapes.

    The table groups by what the chart draws along. If the two could disagree the
    page would be describing two different queries, and the picture would not be
    of the numbers underneath it.
    """
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    body = app[app.index("async function runQuery"):]
    body = body[: body.index("\n}\n")]
    assert 'axes.length > 1' in body, "two axes is not a line chart"
    assert "...many, ...axes" in body, "the extra dimensions are the marks"
    assert 'dims.forEach(d => params.append("g", d))' in body, \
        "the table expands by exactly those"

    chart = app[app.index("function showChart"):]
    chart = chart[: chart.index("\n}\n")]
    assert 'chart.delete("g")' in chart, "g is the table's grouping, not the chart's"
    assert 'chart.set("series", "member")' in chart, "the models are the marks"
    assert 'chart.set("kind", kind)' in chart, "the form reaches the chart"
    assert 'chart.append("panel", extra)' in chart, \
        "a parameter with more than one value is a panel, not another line"
    assert '["member", ...many]' not in chart, "and never a second series"


def test_the_axis_is_a_control_and_not_the_order_the_chips_were_clicked():
    """The axis used to be whatever the expansion chips happened to spell.

    That is an implicit rule: it works only for somebody who already knows it,
    and nothing on the page says it. The axes are named in a select now, and the
    pair that makes a grid is one of the entries rather than a separate form.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="q-axis"' in html
    assert 'value="lead_minutes"' in html
    assert 'value="init_time"' in html
    assert 'value="hour"' in html, "the hour a run started is one of the axes"
    assert 'value="init_time+lead_minutes"' in html, "and the pair is the grid"
    assert 'id="q-g"' not in html, "the chip order is gone"
    assert "the last becomes the x-axis" not in html


def test_the_results_tab_is_split_into_a_question_and_an_answer():
    """The picture has to be on screen while the numbers under it are read.

    One scrolling page puts the chart above a forty-row table and therefore off
    the screen entirely.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    results = html[html.index('id="tab-results"'):]
    results = results[: results.index("</section>")]
    assert 'class="split"' in results
    assert 'class="ask"' in results and 'class="answer"' in results
    assert ".split > .answer" in css and "overflow: auto" in css


def test_the_config_form_does_not_ask_for_notes():
    """What the form asks for is what the config carries. A field nobody reads
    is a question with no answer path."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="c-notes"' not in html
    assert "c-notes" not in app


def test_the_wizard_does_not_ask_when_a_forecast_ran():
    """A forecast picked from the list is on the roster from now on. Giving one a
    last day is a later action, in the config's own detail panel, and it is the
    only reason the dates still exist at all."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    wizard = app[app.index("function emptyMember"):
                 app.index("function renderMembers")]
    assert "active_from" not in wizard
    assert "active_to" not in wizard
    assert "active_from" in app, "the detail panel still gives a member a last day"
def test_the_page_owns_no_list_of_families():
    """The families, their words and the defaults all come from the service.

    The page having a list of its own is the whole bug class: a family added on
    the server would not show up here until the page was edited, and a family
    renamed on the server would show up under the old name.
    """
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "DEFAULT_FAMILIES" not in app
    assert "FAMILY_LABEL" not in app
    assert "familyLabel" in app
    assert "defaultFamilies" in app


def test_the_family_words_are_sent_with_the_registry():
    """On the service, not in the page: one definition of each word."""
    from nib_verify.core.metrics import list_families

    words = {f["family"]: f["label"] for f in list_families()}
    assert words == {"continuous": "Continuous", "yes_or_no": "Yes or no",
                     "spatial": "Spatial", "moments": "Moments"}
    defaults = {f["family"] for f in list_families() if f["added_by_default"]}
    assert defaults == {"continuous", "yes_or_no", "spatial"}
    # and the names are the ones the store knows, because they are one name
    from nib_verify.core.quantities import FAMILY_OF

    assert set(FAMILY_OF) == set(words)


def test_each_group_owns_its_own_parameter_boxes():
    """Two families share the `level` parameter, so a single shared box would
    have to be copied into both configs and the last one typed would win."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "m-levels-${group.family}" in app
    assert "m-windows-${group.family}" in app


def test_the_page_does_not_keep_its_own_copy_of_the_plan_reasons():
    """It did, and it went stale.

    The server's table gained `other-source` and the page's did not, so a run
    turned away for using another observation source was rendered with an empty
    'what it means' cell -- a rejection with no reason shown, which reads like
    nothing being wrong. The sentence now comes down with the rejection.
    """
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "REASON_TEXT" not in app
    for sentence in ("is not on this config's roster",
                     "different observation source",
                     "was not on the roster on that date",
                     "no start_time",
                     "forecast horizon",
                     "packages do not match"):
        assert sentence not in app, sentence


def test_the_legend_is_the_roster_and_clicking_it_hides_a_model():
    """The key is built from the config's roster, not from the records that came
    back -- a model that has been clicked off is not in the records, and would
    have no way back onto the chart."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    legend = app[app.index("function renderLegend"):]
    legend = legend[: legend.index(chr(10) + "}" + chr(10))]
    assert "member_names" in legend, "the roster"
    assert "series_colours" in legend, "the palette the lines are drawn with"
    assert "state.hidden" in legend
    assert "Pick at least one model" in legend, "the last one cannot be hidden"

    params = app[app.index("function queryParams"):]
    params = params[: params.index(chr(10) + "}" + chr(10))]
    assert 'params.append("member", name)' in params,         "hiding a model has to reach the query, not only the picture"

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="chart-legend"' in html
    row = html[html.index('id="chart-legend"') - 400: html.index('id="q-kind"') + 200]
    assert "justify-content:space-between" in row, "the two ends of one row"
    assert html.index('id="chart-legend"') < html.index('id="q-kind"'),         "the key on the left, the buttons on the right"


def test_the_gap_marker_is_on_the_page_because_the_canvas_has_no_legend():
    """A line with a hole in it says it has a hole by not being drawn through it.

    What it cannot say is whether the hole is missing data or a drawing artefact,
    and that sentence now lives under the key rather than across the plot.
    """
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    legend = app[app.index("function renderLegend"):]
    legend = legend[: legend.index(chr(10) + "}" + chr(10))]
    assert 'r.value === null' in legend, "a member with a missing lead"
    assert '(gap)' in legend


def test_the_time_fields_say_what_they_mean_and_have_a_key_to_confirm_it():
    """The browser's own date picker has no done button.

    The value was never the problem -- ``queryParams`` reads the fields when the
    query runs, so nothing depended on the blur that closes the calendar. What was
    missing was any sign that the choice had taken, and the only way to give one
    was to click some other part of the page. So: a week is a click, the range is
    said back as it is typed, and Enter is a key that means done.
    """
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function sayInit" in app and "function applyInit" in app
    said = app[app.index("function sayInit"):]
    said = said[: said.index(chr(10) + "}" + chr(10))]
    assert "btn-init-range" in said, "the range is said on the button that opens it"

    # The one button, on the panel, where the choosing happens
    panel = app[app.index("function applyInit"):]
    panel = panel[: panel.index(chr(10) + "}" + chr(10))]
    assert "closeInitPanel()" in panel and "sayInit()" in panel
    assert "panelParts" in panel, "the panel is what is read, not the hidden field"

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="init-panel"' in html and 'id="btn-init-ok"' in html
    assert 'id="q-init-quick"' in html, "the week is a click, not a calendar"
    assert 'type="hidden" id="q-init-from"' in html, \
        "the browser's own picker is not used: it draws Today and Clear and there is no way to change that"
    # the panel is inside the same block as the fields it edits, not off to one side
    assert html.index('id="q-init-quick"') < html.index('id="btn-init-range"') \
        < html.index('id="init-panel"')
    # and nothing of the picker's own footer is ours
    for theirs in ("\u4eca\u5929", "\u6e05\u9664"):
        assert theirs not in html, theirs


def test_the_quick_picks_are_the_windows_people_ask_for():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    picks = app[app.index("const INIT_PICKS"):]
    picks = picks[: picks.index("];")]
    for days in ("[1,", "[3,", "[7,", "[30,"):
        assert days in picks, days
    assert "[null" not in picks, "All is not a quick pick: a year takes seconds"
    assert 'text: "Custom"' in app, "the range that is not a preset is still a click"
    assert "function setInitDays" in app
    assert "7 * 86400000" in app, "the page opens on a recent week by default"

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="q-init-day"' not in html
    assert "All data" not in html
