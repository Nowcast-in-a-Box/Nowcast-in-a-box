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
    """
    calls = page_calls()
    assert len(calls) >= 10, sorted(calls)
    for expected in ("/api/configs", "/api/catalog", "/api/metrics",
                     "/api/plan", "/api/runs", "/api/scores", "/api/chart"):
        assert expected in calls, expected


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
    """Four, and the numbers are the sections. The preview bar carries no step
    number, because it is an action rather than a fifth thing to fill in."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    steps = re.findall(r'<span class="step">(\d)</span>', html)
    assert steps == ["1", "2", "3", "4"]
    assert 'id="btn-preview"' in html and 'id="btn-save"' in html


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


def test_the_chart_follows_the_expansion_order_that_was_chosen():
    """The page does not invent an axis. It reads back the last expansion, which
    is the convention the query already documents."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    body = app[app.index("function showChart"):]
    body = body[: body.index("\n}\n")]
    assert 'dims[dims.length - 1]' in body, "the last expansion is the x axis"
    assert "dims.slice(0, -1)" in body, "the ones before it separate the lines"
    assert "chart.delete(\"g\")" in body, "g is the table's grouping, not the chart's"


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
                 app.index("function renderMetricAdder")]
    assert "active_from" not in wizard
    assert "active_to" not in wizard
    assert "active_from" in app, "the detail panel still retires a member"


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
