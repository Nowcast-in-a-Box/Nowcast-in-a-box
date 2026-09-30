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
        self.page.click(f'#tabs button[data-tab="{name}"]')
        self.page.wait_for_timeout(400)
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
def test_the_configs_tab_renders_the_config_and_says_nothing_unhappy(page, live_server):
    page.load(live_server)

    body = page.text()
    assert "NiB verification" in body
    assert "vil-test" in body, "the config table did not render"
    assert make_config().ruler.fingerprint in body, "the ruler fingerprint did not render"
    assert "Could not read" not in body
    assert page.errors == []


def test_the_wizard_renders_its_four_sections(page, live_server):
    """Four, and numbered 1 to 4. The preview is an action, not a section — it
    sits in the sticky bar at the bottom so it is reachable from any of them."""
    page.load(live_server).tab("create")

    body = page.text()
    for block in ("Identity", "Basic set", "Forecasts", "Metrics"):
        assert block in body, f"the wizard is missing {block!r}"
    assert "Preview plan" in body
    steps = page.page.eval_on_selector_all("#tab-create .card > h2 .step",
                                           "els => els.map(e => e.textContent.trim())")
    assert steps == ["1", "2", "3", "4"], steps
    assert page.errors == []


def test_the_wizard_offers_what_the_catalog_found(page, live_server):
    """Both pickers are lists of what this server can see. A datalist's options
    are not page text, so they are read directly."""
    page.load(live_server).tab("create")

    assert "radar-composite" in page.page.eval_on_selector_all(
        "#truth-options option", "els => els.map(e => e.value)")
    assert "mlcast-det" in page.page.eval_on_selector_all(
        "#member-pick option", "els => els.map(e => e.value)")


def test_adding_a_forecast_brings_its_leads_with_it(page, live_server):
    """The leads are the horizon the model's own run configs asked for, read in
    the time step fixed above: six frames at ten minutes, so ten to sixty."""
    page.load(live_server).tab("create")
    page.page.click("#btn-add-member")
    page.page.wait_for_timeout(300)

    assert page.page.eval_on_selector("#members input >> nth=0", "e => e.value") == "mlcast-det"
    # the forecast is what was picked, so it cannot be typed over
    assert page.page.eval_on_selector("#members input >> nth=0", "e => e.readOnly") is True
    # the leads are a proposal, because a config may want fewer than the model can produce
    assert page.page.eval_on_selector("#members input >> nth=1", "e => e.value") == "10, 20, 30, 40, 50, 60"
    assert page.page.eval_on_selector("#members input >> nth=1", "e => e.readOnly") is False
    assert page.errors == []


def test_the_leads_are_editable_after_being_filled_in(page, live_server):
    page.load(live_server).tab("create")
    page.page.click("#btn-add-member")
    page.page.wait_for_timeout(300)
    page.page.fill("#members input >> nth=1", "10, 30")
    page.page.wait_for_timeout(100)
    assert page.page.eval_on_selector("#members input >> nth=1", "e => e.value") == "10, 30"


def test_a_forecast_already_on_the_roster_is_not_offered_again(page, live_server):
    """Picking is the whole action, so the list it picks from has to shrink —
    otherwise the second click silently adds a duplicate member."""
    page.load(live_server).tab("create")
    page.page.click("#btn-add-member")
    page.page.wait_for_timeout(300)

    assert page.page.eval_on_selector("#member-pick", "e => e.disabled") is True
    assert "every model in this unit is on the roster" in page.text()


def test_only_models_in_the_rulers_unit_are_offered(page, live_server):
    """A model whose output is in another unit cannot join this config: every
    member has to be in the ruler's unit or the thresholds mean nothing. Offering
    it and then declaring it in the ruler's unit would build a config that says
    something untrue about the model and then verifies nothing."""
    page.load(live_server).tab("create")
    page.page.fill("#c-truth", "radar-composite")
    page.page.dispatch_event("#c-truth", "change")
    page.page.wait_for_timeout(400)

    before = page.page.eval_on_selector_all("#member-pick option", "e => e.map(o => o.value)")
    assert "mlcast-det" in before

    # a model in another unit, exactly as the catalog would report one
    page.page.evaluate("""() => {
      state.catalog.packages.packages.push({
        id: "satcast-det", kind: "model",
        ruler_defaults: {variable: "rain_rate", unit: "mm h-1"},
      });
      renderMemberPicker();
    }""")
    page.page.wait_for_timeout(200)
    after = page.page.eval_on_selector_all("#member-pick option", "e => e.map(o => o.value)")
    assert "mlcast-det" in after
    assert "satcast-det" not in after
    assert page.errors == []


def test_a_member_carries_its_own_units_not_the_rulers(page, live_server):
    """Taken from the model's package. Taken from the ruler it would be the same
    string by construction, and a model in another unit would arrive looking
    correct."""
    page.load(live_server).tab("create")
    page.page.fill("#c-id", "u-test")
    page.page.fill("#c-name", "unit test")
    page.page.fill("#c-truth", "radar-composite")
    page.page.dispatch_event("#c-truth", "change")
    page.page.wait_for_timeout(300)
    page.page.click("#btn-add-member")
    page.page.wait_for_timeout(300)
    page.page.evaluate("""() => {
      const pkg = state.catalog.packages.packages.find(p => p.id === "mlcast-det");
      pkg.ruler_defaults.variable = "reflectivity";
      pkg.ruler_defaults.unit = "dBZ";
    }""")
    members = page.page.evaluate("() => buildSpec().members")
    assert members[0]["unit"] == "dBZ", members
    assert members[0]["variable"] == "reflectivity", members


def test_removing_a_forecast_puts_it_back_in_the_list(page, live_server):
    page.load(live_server).tab("create")
    page.page.click("#btn-add-member")
    page.page.wait_for_timeout(300)
    page.page.click("#members button.danger")
    page.page.wait_for_timeout(300)

    assert "No forecasts on the roster yet." in page.text()
    assert "mlcast-det" in page.page.eval_on_selector_all(
        "#member-pick option", "els => els.map(e => e.value)")


def test_choosing_a_ground_truth_fills_the_ruler_in(page, live_server):
    """The package already declares its unit and its step, so the form asks it
    rather than asking a person to retype them."""
    page.load(live_server).tab("create")
    assert page.page.eval_on_selector("#c-unit", "e => e.value") == ""

    page.page.fill("#c-truth", "radar-composite")
    page.page.dispatch_event("#c-truth", "change")
    page.page.wait_for_timeout(300)

    assert page.page.eval_on_selector("#c-unit", "e => e.value") == "kg m-2"
    assert page.page.eval_on_selector("#c-step", "e => e.value") == "10"
    assert "filled from the package" in page.text()


def test_the_unit_cannot_be_typed_over_and_what_is_not_declared_stays_empty(page, live_server):
    """A region name is not an extent. The boxes the package cannot answer for
    stay empty and say so, because a guessed mask is a silently invented one."""
    page.load(live_server).tab("create")
    page.page.fill("#c-truth", "radar-composite")
    page.page.dispatch_event("#c-truth", "change")
    page.page.wait_for_timeout(300)

    assert page.page.eval_on_selector("#c-unit", "e => e.readOnly") is True
    for selector in ("#c-lon0", "#c-lat0", "#c-lon1", "#c-lat1", "#c-res"):
        assert page.page.eval_on_selector(selector, "e => e.value") == "", selector
    assert "not declared" in page.text()
    assert page.errors == []


def test_switching_ground_truth_clears_what_the_new_one_does_not_declare(page, live_server):
    """Otherwise the previous package's unit is left sitting under the new
    package's name, which is worse than an empty box."""
    page.load(live_server).tab("create")
    page.page.fill("#c-truth", "radar-composite")
    page.page.dispatch_event("#c-truth", "change")
    page.page.wait_for_timeout(300)
    assert page.page.eval_on_selector("#c-unit", "e => e.value") == "kg m-2"

    page.page.fill("#c-truth", "no-such-package")
    page.page.dispatch_event("#c-truth", "change")
    page.page.wait_for_timeout(300)
    assert page.page.eval_on_selector("#c-unit", "e => e.value") == ""
    assert "not a package this server can see" in page.text()


def test_opening_a_config_shows_its_ruler_roster_and_metrics(page, live_server):
    page.load(live_server)
    page.page.click("#configs-list button.link")
    page.page.wait_for_timeout(700)

    body = page.text()
    assert "Basic set" in body
    assert "locked" in body
    assert "Forecasts" in body
    assert "append-only" in body
    assert page.errors == []


def test_a_preview_plans_and_reports(page, live_server):
    page.load(live_server).tab("create")
    page.page.fill("#c-id", "shot-config")
    page.page.fill("#c-name", "A config from the wizard")
    # Ground truth first: the unit and the step come with it and the extent does
    # not, which is the whole shape of the section.
    page.page.fill("#c-truth", "radar-composite")
    page.page.dispatch_event("#c-truth", "change")
    page.page.wait_for_timeout(200)
    for element, value in (("#c-lon0", "110"), ("#c-lat0", "30"),
                           ("#c-lon1", "120"), ("#c-lat1", "42"), ("#c-res", "0.5")):
        page.page.fill(element, value)

    page.page.click("#btn-add-member")
    page.page.wait_for_timeout(300)
    page.page.fill("#members input >> nth=1", "10, 20, 30")

    # the three families are already there; only their parameters are missing
    page.page.fill("#m-levels-yes_or_no", "181, 219")
    page.page.fill("#m-levels-spatial", "181")
    page.page.fill("#m-windows-spatial", "2, 4")
    page.page.click("#btn-preview")
    page.page.wait_for_timeout(900)

    body = page.text()
    assert "tasks" in body
    assert "Every run was rejected" not in body
    assert page.errors == []


def test_the_three_families_are_already_there_and_ask_only_for_their_parameters(page, live_server):
    """Nothing to pick. The groups are there when the tab opens, and the only
    boxes are the parameters the families actually read."""
    page.load(live_server).tab("create")
    page.page.wait_for_timeout(400)

    families = page.page.eval_on_selector_all(
        "#metric-groups .groupbox", "els => els.map(e => e.dataset.family)")
    assert families == ["continuous", "yes_or_no", "spatial"]

    assert page.page.locator("#m-levels-yes_or_no").count() == 1
    assert page.page.locator("#m-windows-spatial").count() == 1
    # continuous reads neither, so it is not handed a dead box to wonder about
    assert page.page.locator("#m-levels-continuous").count() == 0
    assert page.page.locator("#m-windows-continuous").count() == 0
    assert "No parameters" in page.text()

    # both parameters are marked required before anything is submitted
    assert "*" in page.page.inner_text("#m-levels-yes_or_no >> xpath=../label")
    assert "*" in page.page.inner_text("#m-windows-spatial >> xpath=../label")
    assert page.errors == []


def test_every_metric_in_a_family_is_verified(page, live_server):
    """There is nothing to tick, so the family's whole list has to be on screen."""
    page.load(live_server).tab("create")
    page.page.wait_for_timeout(400)

    def chips(family):
        return set(page.page.eval_on_selector_all(
            f"#metric-groups .groupbox[data-family={family}] .chip",
            "els => els.map(e => e.textContent)"))

    assert chips("continuous") == {"me", "mae", "rmse"}
    assert chips("yes_or_no") == {"bias", "dts", "ets", "far", "pod", "ts"}
    assert chips("spatial") == {"fss"}


def test_the_families_are_titled_by_the_question_they_answer(page, live_server):
    """Nobody reading the screen has to know what 'yes_or_no' means."""
    page.load(live_server).tab("create")
    page.page.wait_for_timeout(400)
    titles = page.page.eval_on_selector_all(
        "#metric-groups .groupbox strong", "els => els.map(e => e.textContent)")
    assert titles == ["Continuous", "Yes or no", "Spatial"]


def test_a_family_reads_only_the_parameters_it_has(page, live_server):
    """Each group carries its own boxes, so a threshold typed for one family
    cannot end up attached to another that has no threshold at all."""
    page.load(live_server).tab("create")
    page.page.wait_for_timeout(400)
    page.page.fill("#m-levels-yes_or_no", "181, 219")
    page.page.fill("#m-levels-spatial", "181")
    page.page.fill("#m-windows-spatial", "2, 4")

    spec = page.page.evaluate("""() => {
        document.querySelector("#c-id").value = "x";
        document.querySelector("#c-name").value = "x";
        state.memberRows = [{name: "mlcast-det", leads: "10"}];
        return buildSpec().metrics;
    }""")
    by = {m["family"]: m for m in spec}
    assert by["continuous"]["levels"] == []
    assert by["continuous"]["half_windows"] == []
    assert by["yes_or_no"]["levels"] == [181, 219]
    assert by["yes_or_no"]["half_windows"] == [], "continuous' windows leaked in"
    assert by["spatial"]["levels"] == [181]
    assert by["spatial"]["half_windows"] == [2, 4]


def test_a_family_is_dropped_and_put_back_with_one_click(page, live_server):
    """The one action left in this section, and it is undoable: a removed family
    that could not come back would be a trap."""
    page.load(live_server).tab("create")
    page.page.wait_for_timeout(400)
    assert page.page.locator("#metric-add button").count() == 0

    page.page.click("#metric-groups .groupbox >> nth=0 >> button.danger")
    page.page.wait_for_timeout(300)
    assert page.page.eval_on_selector_all(
        "#metric-add button", "els => els.map(e => e.textContent)") == ["+ Continuous"]

    page.page.click("#metric-add button")
    page.page.wait_for_timeout(300)
    assert page.page.eval_on_selector_all(
        "#metric-groups .groupbox", "els => els.map(e => e.dataset.family)"
    ) == ["yes_or_no", "spatial", "continuous"]
    assert page.page.locator("#metric-add button").count() == 0
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

    page.page.click("#q-g .chip:has-text('lead_minutes')")
    page.page.wait_for_timeout(200)
    assert "1. lead_minutes" in page.text(), "the expansion order is shown"

    page.page.click("#btn-query")
    page.page.wait_for_timeout(900)

    body = page.text()
    assert "THREAT SCORE" in body, "the result table did not render"
    assert "lead_minutes" in body
    assert "valid samples" in body
    assert page.errors == []


def test_a_new_threshold_over_stored_numbers_needs_no_rerun(page, live_server):
    """The done_when condition, through the interface.

    The run stored five levels. Asking for a sixth the config never named must
    come straight out of the stored table — so the query succeeds even though
    nothing has been computed for it.
    """
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)

    page.page.click("#q-g .chip:has-text('level')")
    page.page.fill("#q-level", "133")
    page.page.click("#btn-query")
    page.page.wait_for_timeout(900)

    body = page.text()
    assert "THREAT SCORE" in body
    assert "LEVEL" in body, "the threshold is a column"
    assert "133" in body, "a level the config never named came out of the store"
    assert "1 record(s)" in body
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

    page.page.click("#q-g .chip:has-text('lead_minutes')")
    page.page.wait_for_timeout(200)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1200)

    assert page.page.locator("#chart-card").is_visible()
    assert page.page.locator("#chart").evaluate("e => e.naturalWidth") > 0,         "the browser could not render what /api/chart returned"
    assert "x: lead_minutes" in page.text()
    assert page.page.locator("#chart-svg").get_attribute("href").startswith("/api/chart?")
    assert page.errors == []


def test_the_chart_follows_the_expansion_the_user_picked(page, live_server):
    """Two expansions means the last is x and the one before it separates the
    lines, so switching the expansions has to redraw rather than re-label."""
    seed_run(live_server)
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)

    page.page.click("#q-g .chip:has-text('lead_minutes')")
    page.page.wait_for_timeout(200)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1000)
    first = page.page.locator("#chart").get_attribute("src")
    assert "x=lead_minutes" in first
    assert "series=" not in first, "one expansion, one line"

    page.page.click("#q-g .chip:has-text('level')")
    page.page.wait_for_timeout(200)
    page.page.click("#btn-query")
    page.page.wait_for_timeout(1000)
    second = page.page.locator("#chart").get_attribute("src")
    assert "x=level" in second, "the last expansion moved to the x axis"
    assert "series=lead_minutes" in second, "the earlier one became the lines"
    assert page.page.locator("#chart").evaluate("e => e.naturalWidth") > 0
    assert page.errors == []


def test_the_results_tab_says_so_when_nothing_matches(page, live_server):
    """An empty answer is an answer. It must not look like a failure.

    A filter that matches nothing still produces one row of zeroes, which the
    page must recognise as nothing rather than render as a blank table.
    """
    page.load(live_server).tab("results")
    page.page.wait_for_timeout(700)
    page.page.fill("#q-time-from", "2030-01-01T00:00:00Z")
    page.page.click("#btn-query")
    page.page.wait_for_timeout(900)

    body = page.text()
    assert "No stored quantities match" in body
    assert "THREAT SCORE" not in body, "a table of nothing is not a table"
    assert page.errors == []


def test_the_page_reports_a_run_to_completion(page, live_server):
    """The one path that touches the executor, the lock and the job registry."""
    page.load(live_server)
    page.page.click("#configs-list button.link")
    page.page.wait_for_timeout(700)

    page.page.fill("#run-init", "2026-01-01T00:00:00Z")
    page.page.click("#btn-run")

    # poll until the verdict appears, rather than guessing at a sleep
    for _ in range(60):
        if "task(s):" in page.page.inner_text("#run-verdict"):
            break
        page.page.wait_for_timeout(200)
    else:
        pytest.fail(f"the run never reported: {page.page.inner_text('body')[-400:]}")

    body = page.text()
    assert "3 task(s): 3 ran" in body
    assert "0 failed" in body
    assert "done: 3" in body, "the stored counts were not refreshed"
    assert page.errors == []


def test_a_run_twice_over_skips_the_second_time(page, live_server):
    """Reaching the store, and proving a finished task is not recomputed."""
    page.load(live_server)
    page.page.click("#configs-list button.link")
    page.page.wait_for_timeout(700)
    page.page.fill("#run-init", "2026-01-01T00:00:00Z")

    previous = ""
    for expected in ("3 task(s): 3 ran", "0 ran, 3 skipped"):
        page.page.click("#btn-run")
        for _ in range(80):
            text = page.page.inner_text("#run-verdict")
            if "task(s):" in text and text != previous:
                break
            page.page.wait_for_timeout(200)
        else:
            pytest.fail(f"the run never reported {expected!r}")
        previous = text
        assert expected in text, text


def test_the_detail_panel_shows_nothing_the_config_does_not_have(page, live_server):
    """`variable` was on the ruler once and stopped deciding anything, so it was
    deleted from the schema. The panel went on rendering it, and the browser
    showed the word `undefined` in a card whose whole point is being the part that
    cannot be argued with."""
    page.load(live_server).tab("configs")
    page.page.click("#configs-list button.link")
    page.page.wait_for_timeout(400)
    text = page.text()
    assert "undefined" not in text
    assert "null" not in text
    assert "NaN" not in text
    panel = page.page.eval_on_selector("#config-detail .grid", "e => e.textContent")
    assert "variable" not in panel, panel


@pytest.fixture
def slow_server(platform, tmp_path):
    """The same app, with a provider that takes its time.

    A run over the fixture's handful of tasks finishes in milliseconds, which is
    too fast to reload a page in the middle of. Slowing the two calls that read a
    field is the whole difference, and it is the field reads -- not the timing --
    that the test is about.
    """
    import uvicorn

    inner = make_access()

    class Slow:
        def capabilities(self):
            return inner.capabilities()

        def list_observations(self, name, start, end):
            return inner.list_observations(name, start, end)

        def get_observation(self, name, valid_time):
            time.sleep(0.35)
            return inner.get_observation(name, valid_time)

        def get_prediction(self, model_id, init_time, lead_minutes):
            time.sleep(0.35)
            return inner.get_prediction(model_id, init_time, lead_minutes)

    app = create_app(
        root=tmp_path / "verify-root",
        configs=tmp_path / "configs",
        packages=platform.packages,
        runs=[platform.runs_root],
        host=load_host(platform.config_path),
        access=Slow(),
    )
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


def test_a_page_that_reloads_attaches_to_the_run_it_started(browser, slow_server):
    """The work is in the daemon and the page is not.

    Reloading used to lose the run entirely: the tab went quiet, the person who
    started it clicked Run again, and the answer was "busy" -- busy with their
    own job, which they had no way to see.
    """
    # `browser` and not `page`: the `page` fixture brings its own server, and two
    # servers in one test would share the config directory and collide.
    context = browser.new_context(viewport={"width": 1400, "height": 1000})
    page = Page(context.new_page())
    page.load(slow_server).tab("configs")
    page.page.click("#configs-list button.link")
    page.page.wait_for_timeout(400)
    page.page.click("#btn-run")
    page.page.wait_for_timeout(600)

    page.page.reload(wait_until="networkidle")
    page.page.wait_for_timeout(300)
    page.page.click("#configs-list button.link")
    page.page.wait_for_timeout(900)

    log = page.page.eval_on_selector("#run-log", "e => e.textContent") or ""
    assert "attached to a run that was already going" in log, log
    assert page.page.eval_on_selector("#btn-run", "e => e.disabled") is True
    assert "in progress" in page.page.eval_on_selector(
        "#run-verdict", "e => e.textContent")

    # and it still finishes, on this page, and writes down that it did
    for _ in range(40):
        page.page.wait_for_timeout(500)
        if "task(s)" in page.page.eval_on_selector("#run-verdict", "e => e.textContent"):
            break
    assert "task(s)" in page.page.eval_on_selector("#run-verdict", "e => e.textContent")
    rows = page.page.eval_on_selector_all("#runs-body tbody tr", "e => e.length")
    assert rows == 1, rows
    assert page.errors == []
    context.close()


def test_the_runs_card_is_empty_before_anything_has_run(page, live_server):
    page.load(live_server).tab("configs")
    page.page.click("#configs-list button.link")
    page.page.wait_for_timeout(400)
    assert "No pass has finished" in page.page.eval_on_selector(
        "#runs-body", "e => e.textContent")
    assert page.errors == []


def test_the_plan_preview_shows_both_halves_of_the_decision(page, live_server, platform):
    """It showed the no's and hid the yes's.

    `tasks: 48` is a total with nothing behind it, and `not-on-roster: 12` did not
    say which twelve -- which is exactly the report shape that let two radar
    models sit on a satellite ruler with the page saying nothing about it.
    """
    # a run for a model the config's roster does not have, so there is something
    # for the second half of the plan to be about. Written here rather than into
    # the shared fixture, which several discovery tests count.
    from .conftest import run_config_text, write_yaml

    write_yaml(platform.runs_root / "c" / "run.yaml",
               run_config_text(model="not-on-the-roster"))
    page.load(live_server).tab("configs")
    page.page.click("#configs-list button.link")
    page.page.wait_for_timeout(400)
    page.page.evaluate("""async () => {
      mount($("#preview"), renderPlan(await api("/api/plan",
        { method: "POST", body: { config: state.selected.group_id } })));
    }""")
    page.page.wait_for_timeout(1200)
    text = page.page.eval_on_selector("#preview", "e => e.innerText")
    flat = " ".join(text.split())

    assert "What would be verified" in flat
    assert "What was left out" in flat
    # the accepted half: a row per member, with its leads and its frame count
    assert "mlcast-det" in flat
    assert "10, 20, 30" in flat
    # the rejected half names the models, and says what the reason means
    assert "not-on-roster" in flat
    assert "not on this config's roster" in flat
    # and the totals add up out loud instead of leaving the subtraction to us
    assert "usable" in flat and "rejected" in flat
    names = page.page.eval_on_selector_all(
        "#preview tbody tr td:nth-child(3)", "e => e.map(x => x.textContent)")
    assert any("not-on-the-roster" in n for n in names), names
    assert page.errors == []
