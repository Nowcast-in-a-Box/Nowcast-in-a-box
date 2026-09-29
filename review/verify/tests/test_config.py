"""The config and its three mutability rules."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from nib_verify.config import (
    ConfigError,
    Member,
    MetricGroup,
    Ruler,
    VerificationConfig,
    dump,
    load,
)
from nib_verify.config.schema import _dropped_metrics

from .conftest import make_config


# ----------------------------------------------------------------------
# the ruler
# ----------------------------------------------------------------------
def test_ruler_fingerprint_changes_when_the_ruler_changes():
    base = make_config().ruler
    assert base.fingerprint == make_config().ruler.fingerprint

    for changed in (
        replace(base, ground_truth="mrms"),
        replace(base, extent=(110.0, 30.0, 121.0, 42.0)),
        replace(base, resolution=0.25),
        replace(base, time_step_minutes=5),
        replace(base, unit="dBZ"),
    ):
        assert changed.fingerprint != base.fingerprint


def test_ruler_rejects_a_unitless_config():
    with pytest.raises(ConfigError, match="unit is required"):
        replace(make_config().ruler, unit="")


def test_ruler_rejects_a_backwards_extent():
    with pytest.raises(ConfigError, match="lon1 >= lon0"):
        replace(make_config().ruler, extent=(120.0, 30.0, 110.0, 42.0))


def test_ruler_rejects_a_nonpositive_step():
    with pytest.raises(ConfigError):
        replace(make_config().ruler, time_step_minutes=0)


# ----------------------------------------------------------------------
# the roster
# ----------------------------------------------------------------------
def test_member_in_a_different_unit_is_refused():
    bad = Member(name="other", variable="vil", unit="dBZ", leads=(10,))
    with pytest.raises(ConfigError, match="thresholds would be meaningless"):
        replace(make_config(), members=(bad,))


def test_member_needs_at_least_one_lead():
    with pytest.raises(ConfigError, match="no lead times"):
        Member(name="x", variable="vil", unit="kg m-2", leads=())


def test_roster_can_grow():
    cfg = make_config()
    grown = cfg.with_member(
        Member(name="simvp", variable="vil", unit="kg m-2", leads=(10, 20))
    )
    assert [m.name for m in grown.members] == ["mlcast-det", "simvp"]
    assert grown.ruler == cfg.ruler, "adding a member must not move the ruler"


def test_roster_can_retire_a_member_without_deleting_it():
    cfg = make_config()
    retired = cfg.with_member(
        Member(name="mlcast-det", variable="vil", unit="kg m-2", leads=(10,)),
        retire_on=date(2026, 3, 1),
    )
    assert retired.member("mlcast-det").active_to == date(2026, 3, 1)
    assert len(retired.members) == 1, "the history stays, only the window closes"


def test_active_window_is_respected():
    m = Member(name="x", variable="vil", unit="kg m-2", leads=(10,),
               active_from=date(2026, 2, 1), active_to=date(2026, 3, 1))
    assert not m.active_on(date(2026, 1, 31))
    assert m.active_on(date(2026, 2, 1))
    assert m.active_on(date(2026, 3, 1))
    assert not m.active_on(date(2026, 3, 2))


def test_roster_rejects_a_duplicate_name():
    cfg = make_config()
    with pytest.raises(ConfigError, match="already on the roster"):
        cfg.with_member(Member(name="mlcast-det", variable="vil",
                               unit="kg m-2", leads=(10,)))


def test_roster_rejects_a_backwards_window():
    with pytest.raises(ConfigError, match="ends before it starts"):
        Member(name="x", variable="vil", unit="kg m-2", leads=(10,),
               active_from=date(2026, 3, 1), active_to=date(2026, 2, 1))


# ----------------------------------------------------------------------
# the metric list
# ----------------------------------------------------------------------
def test_metrics_can_be_appended():
    cfg = make_config()
    grown = cfg.with_metric_group(
        MetricGroup(family="categorical", items=("pod",), levels=(5.0,))
    )
    assert len(grown.metrics) == len(cfg.metrics) + 1
    assert grown.metrics_for("pod")
    assert grown.ruler == cfg.ruler


def test_appending_an_existing_metric_is_refused():
    cfg = make_config()
    with pytest.raises(ConfigError, match="append-only"):
        cfg.with_metric_group(
            MetricGroup(family="categorical", items=("ts",), levels=(5.0,))
        )


def test_a_metric_needing_a_level_without_one_is_refused():
    with pytest.raises(ConfigError, match="needs at least one level"):
        MetricGroup(family="categorical", items=("ts",))


def test_a_metric_needing_a_window_without_one_is_refused():
    with pytest.raises(ConfigError, match="needs at least one half_window"):
        MetricGroup(family="spatial", items=("fss",), levels=(5.0,))


def test_an_unknown_family_is_refused():
    with pytest.raises(ConfigError, match="unknown metric family"):
        MetricGroup(family="nonsense", items=("ts",), levels=(5.0,))


def test_the_same_metric_twice_is_refused():
    group = MetricGroup(family="categorical", items=("ts",), levels=(5.0,))
    with pytest.raises(ConfigError, match="more than once"):
        replace(make_config(), metrics=(group, group))


# ----------------------------------------------------------------------
# change detection
# ----------------------------------------------------------------------
def test_changing_the_ruler_forces_a_new_config():
    before = make_config()
    after = replace(before, ruler=replace(before.ruler, resolution=0.25))
    assert after.allows_modification_of(before) == ("ruler",)


def test_dropping_a_metric_forces_a_new_config():
    before = make_config()
    trimmed = replace(
        before,
        metrics=tuple(
            replace(g, items=tuple(i for i in g.items if i != "ets"))
            if g.family == "categorical" else g
            for g in before.metrics
        ),
    )
    problems = trimmed.allows_modification_of(before)
    assert problems == ("metrics:ets",)


def test_growing_the_roster_needs_no_new_config():
    before = make_config()
    after = before.with_member(
        Member(name="simvp", variable="vil", unit="kg m-2", leads=(10,))
    )
    assert after.allows_modification_of(before) == ()


def test_adding_a_metric_needs_no_new_config():
    before = make_config()
    after = before.with_metric_group(
        MetricGroup(family="categorical", items=("far",), levels=(5.0,))
    )
    assert after.allows_modification_of(before) == ()


# ----------------------------------------------------------------------
# serialisation
# ----------------------------------------------------------------------
def test_round_trip_through_dict():
    cfg = make_config()
    again = VerificationConfig.from_dict(cfg.to_dict())
    assert again.ruler == cfg.ruler
    assert again.members == cfg.members
    assert again.metrics == cfg.metrics
    assert again.ruler_fingerprint == cfg.ruler_fingerprint


def test_round_trip_through_a_file(tmp_path):
    cfg = make_config()
    path = tmp_path / "vil-test.json"
    dump(cfg, path)
    assert json.loads(path.read_text(encoding="utf-8"))["group"]["id"] == "vil-test"
    assert load(path) == cfg


def test_from_dict_names_the_missing_key():
    payload = make_config().to_dict()
    del payload["ruler"]["time_step_minutes"]
    with pytest.raises(ConfigError, match="time_step_minutes"):
        VerificationConfig.from_dict(payload)


def test_from_dict_accepts_iso_dates_for_the_roster():
    payload = make_config().to_dict()
    payload["members"][0]["active_from"] = "2026-02-01"
    payload["members"][0]["active_to"] = "2026-03-01"
    cfg = VerificationConfig.from_dict(payload)
    assert cfg.member("mlcast-det").active_from == date(2026, 2, 1)


# ----------------------------------------------------------------------
# derived views used by the runner
# ----------------------------------------------------------------------
def test_families_and_levels_are_deduplicated_in_order():
    cfg = make_config()
    assert cfg.families() == ("cat", "cont", "mom", "spatial")
    assert cfg.levels() == (16.0, 74.0, 133.0, 181.0, 219.0)
    assert cfg.half_windows() == (1, 2)
    assert cfg.levels_for("cont") == ()
    assert cfg.half_windows_for("cat") == ()


def test_metrics_for_reports_membership():
    cfg = make_config()
    assert cfg.metrics_for("ts")
    assert not cfg.metrics_for("pod")


def test_dropped_metrics_helper_is_symmetric():
    before = make_config()
    after = replace(
        before,
        metrics=tuple(
            MetricGroup(family="categorical", items=("bias",), levels=(5.0,))
            if g.family == "categorical" else g
            for g in before.metrics
        ),
    )
    assert _dropped_metrics(before, after) == {"ts", "ets"}
    assert _dropped_metrics(after, before) == set()


# ======================================================================
# notes is gone
# ======================================================================
def test_to_dict_has_no_notes_field():
    """The field was deleted: it was written by the form and never read by
    anything. Asking for it on the form was a question with no answer path."""
    assert "notes" not in make_config().to_dict()


def test_a_stray_notes_key_in_an_old_file_is_ignored():
    """Configs written by older versions keep loading — the reader ignores keys
    it does not know, exactly as it does for any other unknown key."""
    raw = make_config().to_dict()
    raw["notes"] = "written by an older version of the module"
    cfg = VerificationConfig.from_dict(raw)
    assert cfg.group_id == "vil-test"
    assert cfg.name == make_config().name
