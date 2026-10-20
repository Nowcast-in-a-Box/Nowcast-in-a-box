"""The page, in a real browser.

The other page tests are static: they read ``app.js`` and check that the paths and
element ids it mentions exist. That catches a typo in a URL, and it does not catch
a call to a function that was renamed — which is exactly the bug that got past
them once, and showed up as a red box in the page instead of a failing test.

So this module drives the page in a browser when one is available, and skips when
it is not. It is a real check, and it degrades to nothing rather than to a
false pass. Install with ``pip install -e '.[dev]'`` and ``playwright install`` to
run it; on a machine with Edge or Chrome installed, no download is needed.

What it asserts is deliberately shallow: no console errors, no page errors, and
content that actually rendered. Anything more specific belongs in the static
tests, which do not need a browser at all.
"""

from __future__ import annotations

import threading
import time

import pytest

from nib_verify.catalog.hostconfig import load as load_host
from nib_verify.server import create_app

from .conftest import make_access, make_config

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="playwright is not installed"
)


# ======================================================================
@pytest.fixture
def live_server(platform, tmp_path):
    """The app on a real port, in a background thread."""
    import uvicorn

    app = create_app(
        root=tmp_path / "verify-root",
        configs=tmp_path / "configs",
        packages=platform.packages,
        runs=[platform.runs_root],
        host=load_host(platform.config_path),
        access=make_access(),
    )
    # a config to look at, so the table is not empty
    from nib_verify.server.library import ConfigLibrary

    ConfigLibrary(tmp_path / "configs").create(make_config())

    config = uvicorn.Config(app, host="127.0.0.1", port=0,
                            log_level="error", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(300):
        if server.started:
            break
        time.sleep(0.05)
    else:  # pragma: no cover
        pytest.skip("uvicorn did not start")

    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def browser():
    """An installed browser, or a skip. Edge and Chrome are tried first."""
    with playwright_api.sync_playwright() as p:
        for attempt in ("msedge", "chrome", None):
            try:
                instance = p.chromium.launch(channel=attempt) if attempt else p.chromium.launch()
            except Exception:  # noqa: BLE001 - any failure means "try the next one"
                continue
            try:
                yield instance
            finally:
                instance.close()
            return
        pytest.skip("no browser is available to playwright")


class Page:
    """A loaded page, plus everything it complained about."""

    def __init__(self, page):
        self.page = page
        self.errors: list[str] = []
        page.on("pageerror", lambda exc: self.errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: self.errors.append(f"console.{msg.type}: {msg.text}")
                if msg.type == "error" else None)

    def load(self, url: str) -> "Page":
        self.page.goto(url, wait_until="networkidle")
        self.page.wait_for_timeout(400)
        return self

    def tab(self, name: str) -> "Page":
        """The tab bar is gone: the query panel is the page now.

        Kept as a no-op so the tests about the answer do not have to be rewritten
        around a navigation step that no longer exists. The tab it used to open
        is the only one left, and it is already showing.
        """
        self.page.wait_for_timeout(150)
        return self

    def text(self) -> str:
        return self.page.inner_text("body")


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(viewport={"width": 1400, "height": 1000})
    p = Page(context.new_page())
    yield p
    context.close()


def seed_run(base_url: str, group_id: str = "vil-test") -> dict:
    """Run the verification over HTTP, so the results tab has numbers to show.

    Deliberately not done through the interface: these tests are about reading
    stored quantities, and driving the wizard to produce them would make a
    failure ambiguous.
    """
    import json
    import urllib.request

    body = json.dumps({"config": group_id,
                       "init": ["2026-01-01T00:00:00Z"]}).encode()
    request = urllib.request.Request(
        f"{base_url}/api/runs", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    started = json.loads(urllib.request.urlopen(request, timeout=30).read())
    assert started["started"], started

    for _ in range(200):
        with urllib.request.urlopen(f"{base_url}/api/jobs/{started['job']}", timeout=10) as r:
            view = json.loads(r.read())
        if view["state"] != "running":
            assert view["state"] == "done", view.get("error")
            return view
        time.sleep(0.1)
    raise AssertionError("the run never finished")


# ======================================================================
def test_three_thresholds_are_three_panels_not_three_lines(page, live_server):
    """Overlaid they read as three ways of scoring one forecast.

    Side by side they read as one score moving with the threshold.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1400)

    page.page.locator("#q-params .chip").nth(0).click()   # two thresholds now
    page.page.wait_for_timeout(200)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1800)

    src = page.page.locator("#chart").get_attribute("src")
    assert "panel=level" in src, "the second threshold is a panel, not a series"
    assert "series=level" not in src
    assert page.page.locator("#chart").evaluate("e => e.naturalWidth") > 0
    assert page.errors == []


def test_the_chart_is_drawn_at_the_width_it_is_shown_at(page, live_server):
    """Otherwise the browser scales it down and every label with it.

    The picture is then legible only to whoever opens the SVG on its own, which
    is not the person looking at the page.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1800)

    asked = int([p for p in page.page.locator("#chart").get_attribute("src").split("&")[1:]
                 if p.startswith("width=")][0].split("=")[1])
    drawn = page.page.locator("#chart").evaluate("e => e.naturalWidth")
    shown = page.page.locator("#chart").evaluate(
        "e => Math.round(e.getBoundingClientRect().width)")
    assert abs(drawn - shown) <= 2, (drawn, shown)
    assert abs(asked - shown) <= 2, (asked, shown)
    assert page.errors == []


def test_the_results_tab_answers_a_query(page, live_server):
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)

    # the config, and the metrics that config carries, are offered
    assert page.page.eval_on_selector("#q-config", "e => e.value") == "vil-test"
    methods = page.page.eval_on_selector_all("#q-method option", "e => e.map(x => x.value)")
    assert methods[:3] == ["ts", "bias", "ets"]
    assert "pod" not in methods, "a metric the config does not carry is not offered"

    # a yes_or_no metric carries thresholds, so the row is there: every threshold
    # the config stored, tiled, with the middle one on -- ten lines on a chart is
    # not a chart, and the rest are one click away
    chips = page.page.eval_on_selector_all(
        "#q-params .chip", "e => e.map(x => [x.textContent, x.getAttribute('aria-pressed')])")
    assert [c[0] for c in chips] == ["16", "74", "133", "181", "219"], chips
    assert [c[0] for c in chips if c[1] == "true"] == ["133"], chips
    assert page.page.text_content("#q-params .who") == "level (kg m-2)", \
        "a threshold means nothing without the unit it is a threshold on"

    page.page.click("#btn-query")
    page.page.wait_for_timeout(900)

    body = page.text()
    assert "THREAT SCORE" in body, "the result table did not render"
    assert "LEAD_MINUTES" in body, "the axis is a column of the table too"
    assert "valid samples" in body
    assert page.errors == []


def test_a_threshold_is_read_back_from_the_store_and_never_recomputed(page, live_server):
    """The done_when condition, through the interface.

    The run stored five thresholds. Picking the fourth asks the store a
    question it can already answer, so it comes back without anything being
    inferred again -- and the box only offers thresholds this config has, so
    there is no way to ask for one nobody ever computed.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)

    # 133 is the default; move to another one, which is still a read -- the store
    # holds every threshold the run computed, so nothing has to be inferred again
    page.page.click("#q-params .chip:text-is('133')")
    page.page.click("#q-params .chip:text-is('181')")
    page.page.wait_for_timeout(200)
    assert page.page.text_content("#q-params .who") == "level (kg m-2)"
    page.page.click("#btn-query")
    page.page.wait_for_timeout(900)

    assert "THREAT SCORE" in page.text()
    assert "level=181" in page.page.locator("#chart").get_attribute("src")
    assert page.page.locator("#chart").evaluate("e => e.naturalWidth") > 0
    assert page.errors == []


def test_the_chart_is_drawn_by_the_browser(page, live_server):
    """``naturalWidth`` is the whole assertion.

    An ``<img>`` whose request failed shows a broken icon and reports a width of
    zero, and no console error has to be raised for that to happen — so a chart
    endpoint that returns the wrong content type, or an SVG the browser refuses,
    looks like a working page with a small grey box on it.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)
    assert not page.page.locator("#chart-card").is_visible(), "no query, no chart"

    page.page.click("#btn-query")
    page.page.wait_for_timeout(1200)

    assert page.page.locator("#chart-card").is_visible()
    assert page.page.locator("#chart").evaluate("e => e.naturalWidth") > 0,         "the browser could not render what /api/chart returned"
    assert "x=lead_minutes" in page.page.locator("#chart").get_attribute("src")
    # the picture is for looking at; what is under it says what it is of
    assert "ts over" in page.text()
    assert page.page.locator("#chart-svg").count() == 0, \
        "no save-the-chart link: the table is what can be saved"
    assert page.errors == []


def test_the_chart_follows_the_axis_the_user_picked(page, live_server):
    """One mark per model, along whichever of the three axes was chosen.

    Switching the axis has to redraw rather than relabel, so the URL is read
    back and so is the picture the browser actually decoded.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)

    page.page.click("#btn-query")
    page.page.wait_for_timeout(1000)
    first = page.page.locator("#chart").get_attribute("src")
    assert "x=lead_minutes" in first
    assert "series=member" in first, "the models are the marks, not the axis"

    page.page.select_option("#q-axis", "hour")
    page.page.wait_for_timeout(1400)
    second = page.page.locator("#chart").get_attribute("src")
    assert "x=hour" in second, "the chosen axis did not reach the chart"
    assert page.page.locator("#chart").evaluate("e => e.naturalWidth") > 0
    assert page.errors == []


def test_two_axes_are_drawn_as_a_grid_and_the_form_toggle_goes_away(page, live_server):
    """A mesh is not a form to pick; it is what asking for two axes looks like.

    So the line/bar toggle disappears when the pair is chosen -- offering a line
    chart of a two-dimensional answer would only be offering to throw one of the
    axes away.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)

    assert page.page.locator("#q-kind .chip").count() == 2
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1400)

    page.page.select_option("#q-axis", "init_time+lead_minutes")
    page.page.wait_for_timeout(1600)
    assert page.page.locator("#q-kind .chip").count() == 0
    assert "drawn as a grid" in page.text()

    src = page.page.locator("#chart").get_attribute("src")
    assert "kind=mesh" in src
    assert "x=init_time" in src and "series=lead_minutes" in src
    assert page.page.locator("#chart").evaluate("e => e.naturalWidth") > 0
    assert page.errors == []


def test_the_bar_form_asks_for_bars(page, live_server):
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1000)

    page.page.click("#q-kind button:has-text('bar')")
    page.page.wait_for_timeout(1200)
    assert "kind=bar" in page.page.locator("#chart").get_attribute("src")
    assert page.page.locator("#chart").evaluate("e => e.naturalWidth") > 0
    assert page.errors == []


def test_the_form_redraws_at_once_and_the_filters_wait_for_the_button(page, live_server):
    """Two kinds of control, and they must not behave alike.

    The form is a way of looking at an answer and redraws on the click, the same
    rule the axis follows. The filters change what is *asked*, so they wait --
    and typing a lead range must not fire off a query somebody did not ask for.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1000)
    first = page.page.locator("#chart").get_attribute("src")

    # a filter: no redraw until the button
    page.page.fill("#q-lead-from", "20")
    page.page.wait_for_timeout(600)
    assert page.page.locator("#chart").get_attribute("src") == first,         "typing in a filter started a query"

    # the form: a redraw with no button
    page.page.click("#q-kind button:has-text('line')")
    page.page.wait_for_timeout(400)
    page.page.click("#q-kind button:has-text('bar')")
    page.page.wait_for_timeout(1200)
    assert "kind=bar" in page.page.locator("#chart").get_attribute("src")
    assert page.errors == []


def test_the_parameter_row_is_the_list_the_family_reads(page, live_server):
    """A level row belongs to a thresholded metric and to nothing else.

    The page does not decide this: it reads the family and the values off the
    config's own metric group, which is the same list the server checks against.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)

    # the first metric is a yes_or_no one: a level row, and no half window row
    assert page.page.text_content("#q-params .who").startswith("level")
    assert "half window" not in page.text()

    # a continuous metric has neither
    page.page.click('#q-methods button:text-is("mae")')
    page.page.wait_for_timeout(300)
    assert page.page.locator("#q-params .chip").count() == 0
    assert "no parameters" in page.text()
    assert page.errors == []


def test_a_metric_with_two_parameters_shows_both_rows(page, live_server):
    """A spatial metric needs a level *and* a half window.

    The page mounts its rows into one host, and the host is cleared each time --
    so a row mounted per turn of a loop leaves the last row and nothing else. Two
    parameters is the only case that shows it, and FSS is the only metric with
    two.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)

    page.page.click('#q-methods button:text-is("fss")')
    page.page.wait_for_timeout(400)
    rows = page.page.eval_on_selector_all("#q-params .who", "e => e.map(x => x.textContent)")
    assert len(rows) == 2, rows
    assert rows[0].startswith("level"), rows
    assert rows[1].startswith("half window"), rows
    # and both rows carry chips, not just the last one
    chips = page.page.eval_on_selector_all("#q-params .param-row",
                                          "e => e.map(r => r.querySelectorAll('.chip').length)")
    assert all(n >= 2 for n in chips), chips
    assert page.errors == []


def test_unselecting_every_threshold_is_refused_rather_than_widened(page, live_server):
    """No value is not "no filter".

    Sending nothing would widen the query to every threshold the config ever
    stored, which is the opposite of what clearing the last one asks for.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)
    page.page.click("#q-params .chip[aria-pressed='true']")   # clear the last one
    page.page.wait_for_timeout(200)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(700)
    assert "Pick at least one level" in page.text()
    assert page.page.locator("#chart-card").is_visible() is False
    assert page.errors == []


def test_the_defaults_come_off_the_config_and_not_off_the_clock(page, live_server):
    """A window with something in it.

    "The last week" taken from the wall clock is the right default for a live
    system and an empty page for a replayed one, and the page cannot tell the two
    apart -- so the window opens on the most recent week the config was actually
    verified over, and the leads on the leads the config asked for.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)

    lead_from = page.page.eval_on_selector("#q-lead-from", "e => Number(e.value)")
    lead_to = page.page.eval_on_selector("#q-lead-to", "e => Number(e.value)")
    assert lead_from <= lead_to and lead_to > 0, (lead_from, lead_to)
    assert page.page.text_content("#tab-results").count("minutes") >= 1, \
        "the lead boxes say what they are measured in"
    # The range is chosen in a panel of our own, and the panel is the only place
    # it can be accepted. The browser's picker draws its own footer -- Today, and
    # Clear -- and none of that can be changed, so it is not used at all.
    assert page.page.eval_on_selector("#q-init-from", "e => e.type") == "hidden"
    assert page.page.eval_on_selector("#q-init-from", "e => e.value") != ""
    assert page.page.eval_on_selector("#q-init-to", "e => e.value") != ""
    assert page.page.locator("#init-panel").is_hidden(), "nothing opens on its own"
    page.page.click("#btn-init-range")
    page.page.wait_for_timeout(300)
    assert page.page.locator("#init-panel select").count() == 10, "five per end"
    assert [b.strip() for b in page.page.eval_on_selector_all(
        "#init-panel button", "e => e.map(x => x.textContent)")] == ["OK"], \
        "one button, and it is the one the picker was missing"
    page.page.click("#btn-init-ok")
    page.page.wait_for_timeout(300)
    assert page.page.locator("#init-panel").is_hidden()
    assert page.errors == []


def test_the_results_tab_says_so_when_nothing_matches(page, live_server):
    """An empty answer is an answer. It must not look like a failure.

    A filter that matches nothing still produces one row of zeroes, which the
    page must recognise as nothing rather than render as a blank table.
    """
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)
    page.page.eval_on_selector("#q-init-from", "e => { e.value = '2030-01-01T00:00'; }")
    page.page.click("#btn-query")
    page.page.wait_for_timeout(900)

    body = page.text()
    assert "No stored quantities match" in body
    assert "THREAT SCORE" not in body, "a table of nothing is not a table"
    assert page.errors == []
def test_a_one_model_config_needs_no_legend(page, live_server):
    """A key for one line is a key with nothing to distinguish.

    The fixture platform carries one model, so this is the case that can be
    driven here; the two-model legend was checked against the demonstration
    workspace, and its logic is pinned in ``test_web_page.py``.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1600)

    assert page.page.locator("#chart-legend").count() == 1,         "the slot is there for when there are two models"
    assert page.page.locator("#chart-legend .chip").count() == 0
    assert page.errors == []
