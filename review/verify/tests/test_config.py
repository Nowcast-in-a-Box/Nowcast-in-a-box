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


def test_a_member_on_the_roster_can_have_its_leads_changed():
    """Leads say which frames would be verified *next*. They do not say what a
    stored number means, because every stored row carries its own lead, so
    shortening the list cannot orphan anything.

    This was refused outright until the page grew an Edit button and the button
    could not do anything.
    """
    cfg = make_config()
    edited = cfg.with_member(Member(name="mlcast-det", variable="vil",
                                    unit="kg m-2", leads=(10, 90)))
    assert edited.member("mlcast-det").leads == (10, 90)
    assert len(edited.members) == len(cfg.members), "it edited, it did not append"
    assert cfg.member("mlcast-det").leads == (10, 20, 30), "the original moved"


def test_a_members_unit_and_variable_cannot_be_changed_under_it():
    """Both decide what the numbers already in the store are numbers *of*, so a
    silent change would re-attribute rows that are already written."""
    cfg = make_config()
    with pytest.raises(ConfigError, match="already on the roster as"):
        cfg.with_member(Member(name="mlcast-det", variable="rain_rate",
                               unit="mm h-1", leads=(10,)))


def test_a_retired_member_cannot_have_its_leads_changed():
    cfg = make_config().with_member(
        Member(name="mlcast-det", variable="vil", unit="kg m-2", leads=(10,)),
        retire_on=date(2026, 5, 1))
    with pytest.raises(ConfigError, match="was retired on"):
        cfg.with_member(Member(name="mlcast-det", variable="vil",
                               unit="kg m-2", leads=(10, 90)))


def test_editing_the_leads_leaves_the_roster_dates_where_they_were():
    """The dates are the record of when the member was on the roster. A leads
    edit is not allowed to reopen one or move one."""
    on = Member(name="mlcast-det", variable="vil", unit="kg m-2",
                leads=(10, 20), active_from=date(2026, 1, 1))
    cfg = replace(make_config(), members=(on,))
    edited = cfg.with_member(Member(name="mlcast-det", variable="vil",
                                    unit="kg m-2", leads=(90,),
                                    active_from=date(2026, 6, 1)))
    got = edited.member("mlcast-det")
    assert got.leads == (90,)
    assert got.active_from == date(2026, 1, 1)


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
        MetricGroup(family="yes_or_no", items=("pod",), levels=(5.0,))
    )
    assert len(grown.metrics) == len(cfg.metrics) + 1
    assert grown.metrics_for("pod")
    assert grown.ruler == cfg.ruler


def test_appending_an_existing_metric_is_refused():
    cfg = make_config()
    with pytest.raises(ConfigError, match="append-only"):
        cfg.with_metric_group(
            MetricGroup(family="yes_or_no", items=("ts",), levels=(5.0,))
        )


def test_a_metric_needing_a_level_without_one_is_refused():
    with pytest.raises(ConfigError, match="needs at least one level"):
        MetricGroup(family="yes_or_no", items=("ts",))


def test_a_metric_needing_a_window_without_one_is_refused():
    with pytest.raises(ConfigError, match="needs at least one half_window"):
        MetricGroup(family="spatial", items=("fss",), levels=(5.0,))


def test_an_unknown_family_is_refused():
    with pytest.raises(ConfigError, match="unknown metric family"):
        MetricGroup(family="nonsense", items=("ts",), levels=(5.0,))


def test_the_same_metric_twice_is_refused():
    group = MetricGroup(family="yes_or_no", items=("ts",), levels=(5.0,))
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
            if g.family == "yes_or_no" else g
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
        MetricGroup(family="yes_or_no", items=("far",), levels=(5.0,))
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
    assert cfg.families() == ("yes_or_no", "continuous", "moments", "spatial")
    assert cfg.levels() == (16.0, 74.0, 133.0, 181.0, 219.0)
    assert cfg.half_windows() == (1, 2)
    assert cfg.levels_for("continuous") == ()
    assert cfg.half_windows_for("yes_or_no") == ()


def test_metrics_for_reports_membership():
    cfg = make_config()
    assert cfg.metrics_for("ts")
    assert not cfg.metrics_for("pod")


def test_dropped_metrics_helper_is_symmetric():
    before = make_config()
    after = replace(
        before,
        metrics=tuple(
            MetricGroup(family="yes_or_no", items=("bias",), levels=(5.0,))
            if g.family == "yes_or_no" else g
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


# ======================================================================
# the ruler has no variable
# ======================================================================
def test_the_ruler_does_not_carry_a_variable():
    """It was never compared to anything: the unit guard looks at the unit and
    regrid looks at the space. The ground truth package already names its field."""
    assert not hasattr(make_config().ruler, "variable")
    assert "variable" not in make_config().to_dict()["ruler"]


def test_a_ruler_written_with_a_variable_still_loads():
    raw = make_config().to_dict()
    raw["ruler"]["variable"] = "vil"
    cfg = VerificationConfig.from_dict(raw)
    assert cfg.ruler.ground_truth == "radar-composite"
    assert cfg.ruler.unit == "kg m-2"


def test_the_fingerprint_covers_the_five_things_that_are_left():
    """The fingerprint is what makes two configs' numbers comparable, so it has
    to change when the ruler changes and not when anything else does."""
    from dataclasses import replace

    from nib_verify.config.schema import Ruler

    base = Ruler(ground_truth="a", unit="kg m-2", time_step_minutes=10,
                 extent=(110.0, 30.0, 120.0, 42.0), resolution=0.5)
    assert len(base.fingerprint) == 16
    assert replace(base).fingerprint == base.fingerprint
    for field, value in (("ground_truth", "b"), ("unit", "dBZ"),
                         ("time_step_minutes", 5), ("resolution", 1.0),
                         ("extent", (0.0, 0.0, 1.0, 1.0))):
        assert replace(base, **{field: value}).fingerprint != base.fingerprint, field


# ----------------------------------------------------------------------
# a metric has to live in the block it reads
# ----------------------------------------------------------------------
@pytest.mark.parametrize("family, item", [
    ("continuous", "ts"),
    ("moments", "mae"),
    ("spatial", "bias"),
    ("yes_or_no", "fss"),
    ("yes_or_no", "corr"),
])
def test_a_metric_cannot_be_filed_under_a_family_it_does_not_read(family, item):
    """The bug two vocabularies made invisible.

    A metric sitting in a family whose block its columns are not in is a formula
    over columns that are not there. Nothing raises: the query finds no rows and
    every answer comes back as a gap. It was accepted for as long as the family a
    config names and the family a metric declares came from two different tables,
    because then the two could not be compared at all.
    """
    with pytest.raises(ConfigError, match="belongs to the"):
        MetricGroup(family=family, items=(item,), levels=(5.0,), half_windows=(1,))


def test_every_metric_in_the_registry_is_where_it_says_it_is():
    from nib_verify.core.metrics import REGISTRY, spec_for

    for name in REGISTRY:
        spec = spec_for(name)                      # raises if it is not
        assert set(spec.columns) <= set(spec.block.columns), name


def test_a_config_names_the_families_the_store_names():
    """One name, so this is a comparison rather than a translation."""
    from nib_verify.core.quantities import FAMILY_OF
    from nib_verify.store.db import TABLE_OF

    cfg = make_config()
    assert set(cfg.families()) <= set(FAMILY_OF)
    assert {FAMILY_OF[f].table for f in cfg.families()} <= set(TABLE_OF.values())
    assert FAMILY_OF["yes_or_no"].table == "q_yes_or_no"
