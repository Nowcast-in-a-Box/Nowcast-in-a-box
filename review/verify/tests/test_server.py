"""The web service.

The API is thin, so most of what is worth testing is not the endpoints but the
three rules it refuses to break: the ruler cannot be edited, metrics are only
added, and an empty plan caused by rejections is reported as a failure rather
than as a quiet success.

Requests go through ``TestClient``, so everything here runs in-process — no
socket, no port, no uvicorn.
"""

from __future__ import annotations

import json

import pytest

from nib_verify.catalog.hostconfig import load as load_host
from nib_verify.server import create_app
from nib_verify.server.library import ConfigLibrary as Library

from .conftest import make_access, make_config


@pytest.fixture
def client(platform, tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(
        root=tmp_path / "verify-root",
        configs=tmp_path / "configs",
        packages=platform.packages,
        runs=[platform.runs_root],
        host=load_host(platform.config_path),
        access=make_access(),
    )
    with TestClient(app) as c:
        c.app_state = app.state
        yield c


def spec(**overrides) -> dict:
    payload = make_config().to_dict()
    payload.update(overrides)
    return payload


def create(client, **overrides):
    response = client.post("/api/configs", json={"spec": spec(**overrides)})
    assert response.status_code == 201, response.text
    return response.json()


def wait(client, job_id: str, timeout: float = 30.0) -> dict:
    job = client.app_state.jobs.wait(job_id, timeout=timeout)
    assert job.state != "running", "job did not finish"
    return job.view()


# ======================================================================
# meta
# ======================================================================
def test_health_names_the_root_and_the_host(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["host"]["review"]["port"] == 8766
    assert body["running_jobs"] == 0


def test_the_review_page_is_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "NiB verification" in page.text
    assert "app.js" in page.text


def test_the_static_files_are_served(client):
    for name in ("app.js", "style.css"):
        assert client.get(f"/static/{name}").status_code == 200


def test_the_metric_catalogue_is_grouped_by_family(client):
    body = client.get("/api/metrics").json()
    families = {f["family"] for f in body["families"]}
    assert "categorical" in families
    methods = {m["method"] for m in body["metrics"]}
    assert {"ts", "me", "corr", "fss"} <= methods


def test_dimensions_come_from_the_metric(client):
    assert "level" in client.get("/api/metrics/ts/dimensions").json()["dimensions"]
    assert "level" not in client.get("/api/metrics/me/dimensions").json()["dimensions"]


def test_the_catalog_reports_packages_runs_and_pairs(client):
    body = client.get("/api/catalog").json()
    assert {p["id"] for p in body["packages"]["packages"]} == {"radar-composite", "mlcast-det"}
    assert body["runs"]["count"] == 2
    assert len(body["ok_pairs"]) == 1


# ======================================================================
# configs
# ======================================================================
def test_a_config_can_be_created_listed_and_read(client):
    create(client, group={"id": "a-config", "name": "A config"})

    listing = client.get("/api/configs").json()
    assert [c["group_id"] for c in listing["configs"]] == ["a-config"]
    assert listing["broken"] == []

    detail = client.get("/api/configs/a-config").json()
    assert detail["name"] == "A config"
    assert detail["ruler"]["ground_truth"] == "radar-composite"
    assert detail["ruler_fingerprint"] == make_config().ruler_fingerprint
    assert detail["stored"]["families"] == []


def test_creating_the_same_config_twice_is_refused(client):
    create(client, group={"id": "a-config", "name": "A config"})
    response = client.post("/api/configs", json={"spec": spec(group={"id": "a-config", "name": "x"})})
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_the_ruler_cannot_be_edited_in_place(client):
    """The whole point of the fingerprint. Numbers must stay comparable.

    ``strict`` is off so that only the ruler guard can answer; the catalog guard
    is tested separately just below.
    """
    create(client, group={"id": "a-config", "name": "A config"})
    changed = spec(group={"id": "a-config", "name": "A config"})
    changed["ruler"] = {**changed["ruler"], "resolution": 0.25}

    response = client.post("/api/configs",
                           json={"spec": changed, "overwrite": True, "strict": False})
    assert response.status_code == 400
    assert "ruler" in response.json()["detail"]
    assert "not be comparable" in response.json()["detail"]

    assert client.get("/api/configs/a-config").json()["ruler"]["resolution"] == 0.5


def test_a_step_that_contradicts_the_catalog_is_refused_before_writing(client):
    """A second guard, and a more specific message than the ruler guard's."""
    create(client, group={"id": "a-config", "name": "A config"})
    changed = spec(group={"id": "a-config", "name": "A config"})
    changed["ruler"] = {**changed["ruler"], "time_step_minutes": 5}

    response = client.post("/api/configs", json={"spec": changed, "overwrite": True})
    assert response.status_code == 400
    assert "runs at 10 min" in response.json()["detail"]


def test_a_metric_cannot_be_removed_in_place(client):
    create(client, group={"id": "a-config", "name": "A config"})
    shrunk = spec(group={"id": "a-config", "name": "A config"})
    shrunk["metrics"] = shrunk["metrics"][:1]

    response = client.post("/api/configs", json={"spec": shrunk, "overwrite": True})
    assert response.status_code == 400
    assert "metrics:" in response.json()["detail"]


def test_members_can_be_added_and_retired(client):
    create(client, group={"id": "a-config", "name": "A config"})

    added = client.post("/api/configs/a-config/roster",
                        json={"name": "simvp", "leads": [10, 20]})
    assert added.status_code == 201, added.text
    assert "simvp" in added.json()["config"]["member_names"]

    retired = client.post("/api/configs/a-config/roster/simvp/retire",
                          json={"on": "2026-06-01"})
    assert retired.status_code == 200
    detail = client.get("/api/configs/a-config").json()
    member = next(m for m in detail["members"] if m["name"] == "simvp")
    assert member["active_to"] == "2026-06-01"


def test_a_member_cannot_be_added_in_a_different_unit(client):
    """Refused rather than silently overridden: thresholds live on the ruler."""
    create(client, group={"id": "a-config", "name": "A config"})
    response = client.post("/api/configs/a-config/roster",
                           json={"name": "simvp", "leads": [10], "unit": "mm/h"})
    assert response.status_code == 400
    assert "meaningless" in response.json()["detail"]

    detail = client.get("/api/configs/a-config").json()
    assert "simvp" not in detail["member_names"]


def test_metrics_are_appended(client):
    create(client, group={"id": "a-config", "name": "A config"})
    before = client.get("/api/configs/a-config").json()["metrics"]

    response = client.post("/api/configs/a-config/metrics",
                           json={"family": "categorical", "items": ["pod"],
                                 "levels": [181]})
    assert response.status_code == 201, response.text

    after = client.get("/api/configs/a-config").json()["metrics"]
    assert len(after) == len(before) + 1
    assert after[-1]["family"] == "categorical"
    assert after[-1]["storage_family"] == "cat"
    assert after[-1]["items"] == ["pod"]


def test_a_metric_already_present_is_not_added_twice(client):
    """Append-only is not append-anything."""
    create(client, group={"id": "a-config", "name": "A config"})
    response = client.post("/api/configs/a-config/metrics",
                           json={"family": "spatial", "items": ["fss"],
                                 "levels": [181], "half_windows": [4]})
    assert response.status_code == 400
    assert "already present" in response.json()["detail"]


def test_a_neighbourhood_metric_needs_both_parameters(client):
    """FSS is computed on a thresholded field inside a window, so it needs both."""
    create(client, group={"id": "a-config", "name": "A config"})
    response = client.post("/api/configs/a-config/metrics",
                           json={"family": "spatial", "items": ["fss"], "levels": [181]})
    assert response.status_code == 400
    assert "half_window" in response.json()["detail"]


def test_a_metric_that_needs_a_level_without_one_is_refused(client):
    create(client, group={"id": "a-config", "name": "A config"})
    response = client.post("/api/configs/a-config/metrics",
                           json={"family": "categorical", "items": ["ts"], "levels": []})
    assert response.status_code == 400
    assert "level" in response.json()["detail"]


def test_an_unknown_config_is_a_404(client):
    assert client.get("/api/configs/nope").status_code == 404
    assert client.delete("/api/configs/nope").status_code == 404


def test_a_group_id_that_is_not_a_file_name_is_refused(client):
    response = client.post("/api/configs",
                           json={"spec": spec(group={"id": "../escape", "name": "bad"})})
    assert response.status_code == 400


def test_a_run_can_be_repeated_but_two_at_once_are_refused(client):
    """Two concurrent runs would write to one store; the second is turned away."""
    create(client, group={"id": "a-config", "name": "A config"})
    first = client.post("/api/runs", json={
        "config": "a-config", "init": ["2026-01-01T00:00:00Z"],
    }).json()
    assert first["started"] is True

    second = client.post("/api/runs", json={
        "config": "a-config", "init": ["2026-01-01T00:00:00Z"],
    }).json()
    assert second["started"] is False
    assert second["reason"] == "busy"

    wait(client, first["job"])


def test_deleting_a_config_removes_only_its_rows(client):
    create(client, group={"id": "one", "name": "One"})
    create(client, group={"id": "two", "name": "Two"})
    for group_id in ("one", "two"):
        job = client.post("/api/runs", json={
            "config": group_id, "init": ["2026-01-01T00:00:00Z"],
        }).json()["job"]
        wait(client, job)

    body = client.delete("/api/configs/one").json()
    assert body["removed_rows"]
    assert client.get("/api/configs/one").status_code == 404
    assert client.get("/api/configs/two").json()["stored"]["tasks"] == {"done": 3}


# ======================================================================
# planning
# ======================================================================
def test_a_preview_plans_against_the_discovered_runs(client):
    body = client.post("/api/plan", json={"spec": spec()}).json()
    assert body["tasks"] == 6
    assert body["runs_discovered"] == 2
    assert body["rejections"] == []


def test_a_preview_of_a_retired_member_reports_the_reason(client):
    payload = spec()
    payload["members"] = [{**payload["members"][0], "active_to": "2025-12-31"}]
    body = client.post("/api/plan", json={"spec": payload}).json()
    assert body["tasks"] == 0
    assert body["reasons"] == {"inactive": 2}   # the two runs the server can see
    assert body["rejections"][0]["detail"]


def test_a_preview_can_probe_the_provider(client):
    body = client.post("/api/plan", json={"spec": spec(), "probe": True}).json()
    assert body["coverage"]["expected"] == 6
    assert body["coverage"]["available"] == 6


def test_a_preview_can_name_a_saved_config(client):
    create(client, group={"id": "a-config", "name": "A config"})
    body = client.post("/api/plan", json={"config": "a-config"}).json()
    assert body["group_id"] == "a-config"
    assert body["tasks"] == 6


def test_a_preview_needs_something_to_plan(client):
    response = client.post("/api/plan", json={})
    assert response.status_code == 400
    assert "config id or a spec" in response.json()["detail"]


def test_the_preview_warns_about_a_truth_source_the_catalog_lacks(client):
    payload = spec()
    payload["ruler"] = {**payload["ruler"], "ground_truth": "not-a-package"}
    body = client.post("/api/plan", json={"spec": payload}).json()
    assert any("not-a-package" in w for w in body["warnings"])


# ======================================================================
# running
# ======================================================================
def test_a_run_completes_and_reports_its_stats(client):
    create(client, group={"id": "a-config", "name": "A config"})
    response = client.post("/api/runs",
                           json={"config": "a-config", "init": ["2026-01-01T00:00:00Z"]})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["started"] is True
    assert body["runs"] == 1

    view = wait(client, body["job"])
    assert view["state"] == "done", view.get("error")
    assert view["result"]["stats"]["ran"] == 3
    assert view["lines"], "progress was reported"


def test_polling_only_returns_new_lines(client):
    create(client, group={"id": "a-config", "name": "A config"})
    job_id = client.post("/api/runs", json={
        "config": "a-config", "init": ["2026-01-01T00:00:00Z"],
    }).json()["job"]
    wait(client, job_id)

    first = client.get(f"/api/jobs/{job_id}").json()
    assert len(first["lines"]) == first["cursor"]

    second = client.get(f"/api/jobs/{job_id}", params={"after": first["cursor"]}).json()
    assert second["lines"] == []
    assert second["cursor"] == first["cursor"]


def test_a_run_with_nothing_to_do_does_not_start_a_job(client):
    create(client, group={"id": "a-config", "name": "A config"})
    # no --init, and the run configs are discovered, so this one does have work
    assert client.post("/api/runs", json={"config": "a-config"}).json()["started"] is True

    retired = spec(group={"id": "b-config", "name": "B"})
    retired["members"] = [{**retired["members"][0], "active_to": "2025-12-31"}]
    client.post("/api/configs", json={"spec": retired, "strict": False})

    idle = client.post("/api/runs", json={"config": "b-config"}).json()
    assert idle["started"] is False
    assert idle["reason"] == "nothing to do"
    assert idle["plan"]["reasons"] == {"inactive": 2}


def test_a_failing_job_is_reported_not_raised(client):
    """Whatever goes wrong in the background belongs on the job, not in a 500."""
    def explode(report):
        report("about to fail")
        raise RuntimeError("no data today")

    job = client.app_state.jobs.submit("test", "deliberate failure", explode)
    view = wait(client, job.id)
    assert view["state"] == "failed"
    assert "no data today" in view["error"]
    assert any("about to fail" in line for line in view["lines"])
    assert client.get("/api/jobs").status_code == 200


def test_an_unknown_job_is_a_404(client):
    assert client.get("/api/jobs/does-not-exist").status_code == 404


# ======================================================================
# querying
# ======================================================================
def test_scores_aggregate_what_a_run_stored(client):
    create(client, group={"id": "a-config", "name": "A config"})
    job = client.post("/api/runs", json={
        "config": "a-config", "init": ["2026-01-01T00:00:00Z"],
    }).json()["job"]
    wait(client, job)

    body = client.get("/api/scores", params=[
        ("config", "a-config"), ("method", "ts"),
        ("level", "181,219"), ("g", "lead_minutes"), ("g", "level"),
    ]).json()

    assert body["dims"] == ["lead_minutes", "level"]
    assert len(body["records"]) == 6
    assert {r["lead_minutes"] for r in body["records"]} == {10, 20, 30}
    assert {r["level"] for r in body["records"]} == {181, 219}
    assert all(0.0 <= r["value"] <= 1.0 for r in body["records"])
    assert all(r["n_valid"] > 0 for r in body["records"])
    assert body["meta"]["group"]["ruler"]["fingerprint"] == make_config().ruler_fingerprint
    assert body["coverage"]["tasks"] == {"done": 3}


def test_asking_for_fewer_thresholds_does_not_change_the_numbers(client):
    """The done_when condition, over HTTP.

    The run stored five levels. Asking for one of them must give exactly what
    asking for two of them gives for the shared one — because the query reads
    the same sums either way and never re-reads a field.
    """
    create(client, group={"id": "a-config", "name": "A config"})
    job = client.post("/api/runs", json={
        "config": "a-config", "init": ["2026-01-01T00:00:00Z"],
    }).json()["job"]
    wait(client, job)

    def at_181(levels):
        body = client.get("/api/scores", params=[
            ("config", "a-config"), ("method", "ts"), ("level", levels),
            ("g", "level"),
        ]).json()
        return next(r["value"] for r in body["records"] if r["level"] == 181)

    assert at_181("181") == at_181("181,219")
    assert at_181("181") == at_181("16,74,133,181,219")


def test_a_threshold_outside_the_configured_levels_can_still_be_asked_for(client):
    """A stored contingency table answers any threshold; nothing was stored for 133."""
    create(client, group={"id": "a-config", "name": "A config"})
    job = client.post("/api/runs", json={
        "config": "a-config", "init": ["2026-01-01T00:00:00Z"],
    }).json()["job"]
    wait(client, job)

    body = client.get("/api/scores", params=[
        ("config", "a-config"), ("method", "ts"), ("level", "133"),
    ]).json()
    assert len(body["records"]) == 1
    assert body["records"][0]["value"] is not None


def test_a_metric_the_config_does_not_carry_is_refused(client):
    create(client, group={"id": "a-config", "name": "A config"})
    response = client.get("/api/scores", params={"config": "a-config", "method": "pod"})
    assert response.status_code == 400
    assert "not part of config" in response.json()["detail"]


def test_an_unknown_dimension_is_refused_with_the_alternatives(client):
    create(client, group={"id": "a-config", "name": "A config"})
    response = client.get("/api/scores", params=[
        ("config", "a-config"), ("method", "me"), ("g", "level"),
    ])
    assert response.status_code == 400
    assert "member" in response.json()["detail"]


def test_a_query_without_a_method_is_refused(client):
    create(client, group={"id": "a-config", "name": "A config"})
    response = client.get("/api/scores", params={"config": "a-config"})
    assert response.status_code == 400


# ======================================================================
# the library underneath
# ======================================================================
def test_the_library_round_trips_a_config(tmp_path):
    library = Library(tmp_path, create=True)
    cfg = make_config()
    library.create(cfg)

    again = library.get(cfg.group_id)
    assert again == cfg
    assert library.summary(again)["ruler_fingerprint"] == cfg.ruler_fingerprint
    assert [c.group_id for c in library.list()] == [cfg.group_id]


def test_the_library_reports_unreadable_files_separately(tmp_path):
    library = Library(tmp_path, create=True)
    library.create(make_config())
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")

    assert len(library.list()) == 1
    assert len(library.broken()) == 1
    assert "broken.json" in library.broken()[0]["name"]


def test_the_library_refuses_a_ruler_change(tmp_path):
    from dataclasses import replace

    from nib_verify.config import ConfigError

    library = Library(tmp_path, create=True)
    cfg = library.create(make_config())
    changed = replace(cfg, ruler=replace(cfg.ruler, time_step_minutes=5))

    with pytest.raises(ConfigError, match="not be comparable"):
        library.save(changed)
    assert library.get(cfg.group_id).ruler.time_step_minutes == 10


def test_a_config_is_json_on_disk(tmp_path):
    library = Library(tmp_path, create=True)
    library.create(make_config())
    raw = json.loads((tmp_path / "vil-test.json").read_text(encoding="utf-8"))
    assert raw["group"]["id"] == "vil-test"
    assert raw["ruler"]["time_step_minutes"] == 10


# ======================================================================
# charts
# ======================================================================
def chart(client, **params) -> "object":
    return client.get("/api/chart", params=params)


def test_a_chart_comes_back_as_an_image(client):
    """Not JSON with an SVG in it. The page loads this with ``<img src>``, so the
    content type is the interface."""
    create(client, group={"id": "chart-config", "name": "Chart"})
    wait(client, client.post("/api/runs", json={
        "config": "chart-config", "init": ["2026-01-01T00:00:00Z"],
    }).json()["job"])

    response = chart(client, config="chart-config", method="ts",
                     x="lead_minutes", series="member", level="181")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.startswith("<?xml")
    assert "<svg" in response.text


def test_the_chart_is_the_same_query_as_the_table(client):
    """Whatever the table drew its numbers from, the chart draws the same ones —
    both go through ``query_scores``, and a chart of different data would be a
    second, quieter way to ask."""
    create(client, group={"id": "same-config", "name": "Same"})
    wait(client, client.post("/api/runs", json={
        "config": "same-config", "init": ["2026-01-01T00:00:00Z"],
    }).json()["job"])

    params = {"config": "same-config", "method": "ts", "level": "181"}
    table = client.get("/api/scores", params={**params, "g": "lead_minutes"}).json()
    svg = chart(client, **params, x="lead_minutes").text

    assert table["records"], "the table has to have something in it"
    # one marker per stored quantity, which is one per point in the table
    assert svg.count("<circle") == len(table["records"])
    # and every lead the table returned is a tick on the chart, spelled the same
    for record in table["records"]:
        assert f">{record['lead_minutes']:g}<" in svg


def test_a_chart_with_no_samples_still_answers_with_a_picture(client):
    """A 404 for "nothing verified yet" would leave a broken image in the page and
    nothing to read. The answer is a picture that says so."""
    create(client, group={"id": "empty-config", "name": "Empty"})
    response = chart(client, config="empty-config", method="ts", x="lead_minutes")
    assert response.status_code == 200
    assert "no samples" in response.text


def test_a_chart_needs_a_method(client):
    response = chart(client, config="vil-test")
    assert response.status_code == 400
    assert "method is required" in response.text


def test_a_chart_needs_a_config(client):
    """A query must name a config, chart or table. Without one there is no ruler
    and the numbers would mean nothing."""
    response = chart(client, method="ts")
    assert response.status_code == 400
    assert "must name a config" in response.text


def test_a_chart_refuses_a_column_the_metric_cannot_expand_by(client):
    create(client, group={"id": "bad-x", "name": "Bad x"})
    response = chart(client, config="bad-x", method="ts", x="nonsense")
    assert response.status_code == 400
    assert "cannot expand by" in response.text


def test_a_chart_refuses_a_series_the_metric_cannot_expand_by(client):
    create(client, group={"id": "bad-s", "name": "Bad series"})
    response = chart(client, config="bad-s", method="ts", series="nonsense")
    assert response.status_code == 400
    assert "cannot expand by" in response.text


def test_the_chart_size_is_bounded(client):
    """The size ends up in every coordinate and in the SVG header, so it is
    clamped rather than trusted."""
    create(client, group={"id": "size-config", "name": "Size"})
    assert 'width="4000"' in chart(client, config="size-config", method="ts",
                                   width="999999").text
    assert 'width="200"' in chart(client, config="size-config", method="ts",
                                  width="1").text
    assert "whole number" in chart(client, config="size-config", method="ts",
                                   width="wide").text


# ======================================================================
# the ground truth fills the ruler in
# ======================================================================
def bare(**ruler):
    """A spec that names a ground truth and supplies as little else as possible."""
    return {
        "group": {"id": "derived", "name": "Derived"},
        "ruler": {"ground_truth": "radar-composite", **ruler},
        "members": [{"name": "mlcast-det", "leads": [10, 20, 30]}],
        "metrics": [{"family": "categorical", "items": ["ts"], "levels": [181.0]}],
    }


def post(client, spec):
    return client.post("/api/configs", json={"spec": spec, "strict": False})


def test_the_ground_truth_fills_the_unit_and_the_step(client):
    """The package declared them; nobody should have to look them up and retype
    them, and a caller posting only an extent still gets a usable config."""
    response = post(client, bare(extent=[110, 30, 120, 42], resolution=0.5))
    assert response.status_code == 201, response.text

    ruler = client.get("/api/configs/derived").json()["ruler"]
    assert ruler["unit"] == "kg m-2"
    assert ruler["time_step_minutes"] == 10
    assert ruler["ground_truth"] == "radar-composite"


def test_what_the_caller_gave_is_never_overwritten(client):
    """The step is editable on purpose: a config may want a coarser one than the
    source produces. Filling blanks must not undo that."""
    response = post(client, bare(unit="mm", time_step_minutes=30,
                                 extent=[110, 30, 120, 42], resolution=0.5))
    assert response.status_code == 201, response.text

    ruler = client.get("/api/configs/derived").json()["ruler"]
    assert ruler["unit"] == "mm"
    assert ruler["time_step_minutes"] == 30


def test_an_extent_the_package_does_not_declare_is_not_invented(client):
    """The manifest names a region, not a grid. A guessed extent is a guessed
    mask, and the mask decides which samples the numbers are made of."""
    response = post(client, bare())
    assert response.status_code == 400
    assert "extent" in response.text


def test_a_ground_truth_the_server_cannot_see_fills_nothing_in(client):
    """It refuses rather than inventing a unit for a package nobody found."""
    response = post(client, {**bare(), "ruler": {
        "ground_truth": "no-such-package",
        "extent": [110, 30, 120, 42], "resolution": 0.5,
    }})
    assert response.status_code == 400
    assert "unit" in response.text


def test_the_ruler_fingerprint_moves_when_the_unit_is_filled_in(client):
    """The two paths must agree, or a config made through the interface and the
    same config made by hand would not be comparable."""
    from nib_verify.config.schema import VerificationConfig

    assert post(client, bare(extent=[110, 30, 120, 42], resolution=0.5)).status_code == 201
    filled = client.get("/api/configs/derived").json()

    by_hand = VerificationConfig.from_dict({
        **bare(extent=[110, 30, 120, 42], resolution=0.5),
        "group": {"id": "derived", "name": "Derived"},
        "ruler": {"ground_truth": "radar-composite", "unit": "kg m-2",
                  "time_step_minutes": 10, "extent": [110, 30, 120, 42],
                  "resolution": 0.5},
    })
    assert filled["ruler_fingerprint"] == by_hand.ruler.fingerprint


def test_a_package_advertises_what_it_can_answer_for(client):
    """The page reads these off the catalog rather than guessing them, so the
    form and the service cannot disagree about what a ground truth implies."""
    packages = client.get("/api/catalog").json()["packages"]["packages"]
    radar = next(p for p in packages if p["id"] == "radar-composite")
    assert radar["ruler_defaults"] == {
        "variable": "vil", "unit": "kg m-2", "time_step_minutes": 10,
        "extent": None, "resolution": None,
    }
