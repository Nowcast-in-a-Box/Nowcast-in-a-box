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

import pytest

from nib_verify.catalog.hostconfig import load as load_host
from nib_verify.server import create_app

from .conftest import make_access, make_config

STATIC = Path(__file__).resolve().parents[1] / "nib_verify" / "server" / "static"

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
def test_the_form_fields_the_page_reads_are_the_ones_it_has():
    """``buildSpec`` reads a fixed set of element ids out of index.html."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    # some elements are built by the page rather than written in the markup
    ids |= set(re.findall(r'id: "([A-Za-z0-9_\-]+)"', js))

    read = set(re.findall(r'\$\("#([A-Za-z0-9_\-]+)"', js))
    read |= set(re.findall(r"\$\('#([A-Za-z0-9_\-]+)'", js))
    missing = sorted(i for i in read if i not in ids)
    assert not missing, f"the page reads #{missing}, which nothing defines"


def test_the_wizard_has_four_numbered_sections():
    """Four, and the numbers are the sections. The foot carries no step number,
    because saving is an action rather than a fifth thing to fill in."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    steps = re.findall(r'<span class="step">(\d)</span>', html)
    assert steps == ["1", "2", "3", "4"]
    assert 'id="btn-save"' in html


def test_the_wizard_does_not_preview_a_plan():
    """There was a `Preview plan` button and a panel under it. A plan is a
    decision the platform makes against whatever runs happen to exist, and in the
    middle of filling a form it answers a question nobody asked yet. It is gone
    from here; the explanation of an empty plan stays where an empty plan
    happens, which is a Run that came back with nothing to do.
    """
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="btn-preview"' not in html
    assert 'id="preview"' not in html
    assert 'id="c-probe"' not in html
    assert "previewPlan" not in js
    # and the response of the save still has somewhere to land
    assert 'id="create-result"' in html


def test_the_wizard_sections_are_named_what_they_are():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    headings = re.findall(r'<h2><span class="step">\d</span>([^<]+)</h2>', html)
    assert headings == ["Identity", "Basic set", "Forecasts", "Metrics"], headings


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
    assert 'id="chart-svg"' in html, "the download link has a fixed id"
    assert "download=" in html, "it is a download, not a link to a new tab"


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


def test_the_metrics_section_asks_for_no_selection():
    """The three families are put there by the page, so there is no family
    picker and no list of metrics to tick: everything that could be ticked is
    already on. The parameters live on each group, not on the section."""
    body = (STATIC / "index.html").read_text(encoding="utf-8")
    section = body[body.index('<span class="step">4</span>'):
                   body.index("create-foot")]
    assert "metric-groups" in section
    assert "metric-add" in section
    assert "<select" not in section
    assert "m-levels" not in section
    assert "m-windows" not in section


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
