"""Planning: a config plus the runs that exist becomes a set of tasks.

The planner's job is to be *specific about refusals*. A user who picks a member
and sees "nothing to do" needs to be told which reason applies, so every test
here checks the reason code as well as the count.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta, timezone

import pytest

from nib_verify.access import CountingAccess
from nib_verify.catalog.packages import discover as discover_packages
from nib_verify.catalog.runs import RunBook, RunConfig
from nib_verify.config import Member
from nib_verify.core.planner import (
    BEYOND_HORIZON,
    INACTIVE,
    INCOMPATIBLE,
    NO_START_TIME,
    NOT_ON_ROSTER,
    plan,
    planned_runs,
)

from .conftest import (
    MODEL_MANIFEST,
    T0,
    make_access,
    make_config,
    write_json,
)

UTC = timezone.utc


def _run_dict(*, model="mlcast-det", data="radar-composite",
              start="2026-01-01T00:00:00Z", horizon=6, sample_id=None) -> dict:
    run = {"forecast_horizon": horizon}
    if start:
        run["start_time"] = start
    if sample_id:
        run["sample_id"] = sample_id
    return {"version": 1, "data": {"id": data}, "model": {"id": model}, "run": run}


def _runbook(*payloads) -> RunBook:
    return RunBook(runs=tuple(RunConfig.from_mapping(p) for p in payloads))


# ======================================================================
# the happy path
# ======================================================================
def test_a_matching_run_expands_into_tasks(platform):
    cfg = make_config()          # member mlcast-det, leads 10/20/30, cadence 10
    result = plan(cfg, platform.runbook())

    assert not result.rejections
    assert len(result.tasks) == 6, "2 runs x 3 leads"
    assert result.members == ["mlcast-det"]
    assert result.leads == [10, 20, 30]
    assert len(result.init_times()) == 2
    assert result.tasks[0].valid_time == T0 + timedelta(minutes=10)


def test_the_plan_can_be_handed_to_the_executor(platform):
    result = plan(make_config(), platform.runbook())
    refs = planned_runs(result)
    assert len(refs) == 2
    assert {r.member for r in refs} == {"mlcast-det"}
    assert {r.init_time for r in refs} == set(result.init_times())


def test_the_plan_serialises(platform):
    payload = plan(make_config(), platform.runbook()).as_dict()
    assert payload["tasks"] == 6
    assert payload["span"]["from"] == "2026-01-01T00:00:00Z"
    assert payload["span"]["to"] == "2026-01-01T01:00:00Z"
    assert payload["leads"] == [10, 20, 30]
    assert len(payload["tasks_detail"]) == 6
    assert payload["reasons"] == {}


def test_planning_with_no_runs_is_an_empty_plan_not_an_error():
    result = plan(make_config(), RunBook())
    assert result.is_empty
    assert result.rejections == []
    assert result.as_dict()["span"] == {"from": None, "to": None}


# ======================================================================
# refusals, each with its reason
# ======================================================================
def test_a_model_that_is_not_on_the_roster_is_refused():
    result = plan(make_config(), _runbook(_run_dict(model="something-else")))
    assert result.is_empty
    assert result.reasons() == {NOT_ON_ROSTER: 1}
    assert "not on the roster" in result.rejections[0].detail


def test_a_retired_member_is_refused():
    cfg = make_config()
    retired = cfg.with_member(cfg.member("mlcast-det"), retire_on=date(2025, 12, 31))
    result = plan(retired, _runbook(_run_dict()))
    assert result.is_empty
    assert result.reasons() == {INACTIVE: 1}
    assert "2025-12-31" in result.rejections[0].detail


def test_a_member_that_has_not_started_yet_is_refused():
    cfg = make_config()
    future = replace(cfg.member("mlcast-det"), active_from=date(2026, 6, 1))
    result = plan(replace(cfg, members=(future,)), _runbook(_run_dict()))
    assert result.reasons() == {INACTIVE: 1}


def test_a_run_identified_only_by_sample_id_is_refused():
    result = plan(make_config(), _runbook(_run_dict(start=None, sample_id="case-7")))
    assert result.is_empty
    assert result.reasons() == {NO_START_TIME: 1}


def test_a_short_horizon_clips_the_requested_leads():
    result = plan(make_config(), _runbook(_run_dict(horizon=2)))
    assert [t.lead_minutes for t in result.tasks] == [10, 20]
    assert any("left out" in note for note in result.notes)
    assert result.rejections == []


def test_a_horizon_that_covers_no_requested_lead_at_all_is_refused():
    cfg = make_config()
    late = replace(cfg.member("mlcast-det"), leads=(60, 90))
    result = plan(replace(cfg, members=(late,)), _runbook(_run_dict(horizon=3)))
    assert result.is_empty
    assert result.reasons() == {BEYOND_HORIZON: 1}
    assert "horizon 3 frames" in result.rejections[0].detail


def test_an_unknown_horizon_is_taken_at_face_value():
    """A missing horizon is not a reason to silently drop real data."""
    result = plan(make_config(), _runbook(_run_dict(horizon=None)))
    assert [t.lead_minutes for t in result.tasks] == [10, 20, 30]


def test_a_member_with_no_leads_cannot_even_be_built():
    """``Member`` guarantees a non-empty lead list, so the planner need not check."""
    from nib_verify.config import ConfigError

    cfg = make_config()
    with pytest.raises(ConfigError, match="no lead times"):
        replace(cfg.member("mlcast-det"), leads=())


# ======================================================================
# the catalog's opinion
# ======================================================================
def test_a_pair_the_matcher_refuses_is_refused_here_too(platform):
    cfg = make_config()
    book = _runbook(_run_dict())
    assert len(plan(cfg, book, catalog=platform.catalog()).tasks) == 3

    model_path = platform.packages[1] / "deep" / "down" / "model" / "manifest.json"
    write_json(model_path, {**MODEL_MANIFEST, "requires": "something-else.v1"})

    result = plan(cfg, book, catalog=discover_packages(platform.packages))
    assert result.is_empty
    assert result.reasons() == {INCOMPATIBLE: 1}
    assert "something-else.v1" in result.rejections[0].detail


def test_a_run_from_another_source_is_refused_by_name(platform):
    """The ruler names the observation source. A prediction made from a different
    one is not what the config asked for, and scoring it against the ruler's truth
    would produce a number about an experiment nobody described."""
    result = plan(make_config(), _runbook(_run_dict(data="satellite-vil")),
                  catalog=platform.catalog())
    assert not result.tasks
    assert result.reasons() == {"other-source": 1}
    assert "satellite-vil" in result.rejections[0].detail


def test_an_unknown_package_is_not_a_verdict(platform):
    """If the catalog has not heard of a package, that is not a mismatch.

    Asked of the pair check directly: the planner has an earlier reason to refuse
    a run that used a different source, and it would answer first.
    """
    from nib_verify.catalog.runs import RunConfig
    from nib_verify.core.planner import _pair_problem

    run = RunConfig.from_mapping(_run_dict(data="never-seen"))
    assert _pair_problem(platform.catalog(), run) is None


# ======================================================================
# probing the provider
# ======================================================================
def test_probe_reports_what_the_provider_can_actually_serve():
    access = CountingAccess(make_access())
    result = plan(make_config(), _runbook(_run_dict()), access=access, probe=True)

    assert result.coverage["expected"] == 3
    assert result.coverage["available"] == 3
    assert result.coverage["unavailable"] == 0
    assert access.prediction_reads == 0, "coverage asks the calendar, not the fields"


def test_probe_off_is_the_default():
    access = CountingAccess(make_access())
    assert plan(make_config(), _runbook(_run_dict()), access=access).coverage is None


def test_probe_counts_frames_the_observations_do_not_cover():
    access = CountingAccess(make_access(unavailable_times=(T0 + timedelta(minutes=20),)))
    result = plan(make_config(), _runbook(_run_dict()), access=access, probe=True)
    assert result.coverage["available"] == 2
    assert result.coverage["unavailable"] == 1
    assert len(result.coverage["missing_examples"]) == 1


def test_probe_reports_a_provider_that_cannot_answer_at_all():
    cfg = make_config()
    cfg = replace(cfg, ruler=replace(cfg.ruler, ground_truth="not-a-thing"))
    result = plan(cfg, _runbook(_run_dict(data="not-a-thing")),
                  access=make_access(), probe=True)
    assert result.coverage["available"] == 0
    assert "error" in result.coverage


# ======================================================================
# several members at once
# ======================================================================
def test_two_members_plan_independently():
    cfg = make_config()
    cfg = replace(cfg, members=(
        cfg.member("mlcast-det"),
        Member(name="simvp", variable="vil", unit="kg m-2", leads=(10,)),
    ))
    result = plan(cfg, _runbook(_run_dict(model="mlcast-det"),
                                _run_dict(model="simvp")))
    assert len(result.tasks) == 4, "3 for one member, 1 for the other"
    assert result.members == ["mlcast-det", "simvp"]
    assert result.leads == [10, 20, 30]


def test_every_reason_carries_its_own_sentence_to_the_client():
    """The page is not allowed to hold a second copy of these -- it did, and it
    fell a reason behind, so one kind of rejection rendered with nothing said
    about it."""
    from nib_verify.core import planner as P

    assert set(P.REASONS) == set(P.REASON_TEXT), "a reason without a sentence"
    for reason in P.REASONS:
        payload = P.Rejection(model_id="m", reason=reason, detail="d").as_dict()
        assert payload["meaning"] == P.REASON_TEXT[reason]
        assert payload["meaning"], reason


def test_the_plan_says_which_models_it_turned_away():
    """`12 not-on-roster` was the whole report, and it is the shape of report
    that hides things: two radar models on a satellite ruler were in the twelve
    and the page named neither."""
    cfg = make_config()          # roster: mlcast-det only
    result = plan(cfg, _runbook(_run_dict(model="mlcast-det"),
                                _run_dict(model="mlcast-alt"),
                                _run_dict(model="satcast-det")))

    assert result.as_dict()["reasons"] == {"not-on-roster": 2}
    turned_away = {r["model"] for r in result.as_dict()["rejections"]}
    assert turned_away == {"mlcast-alt", "satcast-det"}
    # and every one of them can say what it was
    assert all(r["meaning"] for r in result.as_dict()["rejections"])
