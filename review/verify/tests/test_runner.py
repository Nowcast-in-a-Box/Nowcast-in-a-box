"""Executor behaviour: isolation, gaps, and the unit guard."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from nib_verify.access import CountingAccess
from nib_verify.config import Member, VerificationConfig
from nib_verify.runner import RunRef, run_tasks, todo
from nib_verify.runner.lock import Locked, SingleInstanceLock
from nib_verify.store import Store

from .conftest import T0, make_access, make_config


def _runs(n=1):
    return [RunRef(member="mlcast-det", init_time=T0 + timedelta(hours=i))
            for i in range(n)]


def test_a_unit_mismatch_fails_loudly_rather_than_quietly():
    """Thresholds written in dBZ against a field in kg m-2 must not pass."""
    cfg = make_config()
    assert cfg.ruler.unit == "kg m-2"
    access = CountingAccess(make_access(unit="dBZ"))  # provider disagrees
    store = Store(":memory:")

    stats = run_tasks(access, store, cfg, _runs())

    assert stats.ran == 0
    assert stats.failed == 3
    assert store.task_state(cfg.group_id, "mlcast-det", "2026-01-01T00:00:00Z", 10) == "failed"
    assert any("meaningless" in m for m in stats.messages)


def test_missing_observations_are_recorded_not_skipped_silently():
    access = CountingAccess(make_access(unavailable_times=(T0 + timedelta(minutes=20),)))
    store = Store(":memory:")
    cfg = make_config()

    stats = run_tasks(access, store, cfg, _runs())

    assert stats.total == 3
    assert stats.ran == 2
    assert stats.unavailable == 1
    assert stats.obs_missing == 1
    assert store.task_state(cfg.group_id, "mlcast-det",
                            "2026-01-01T00:00:00Z", 20) == "unavailable"
    assert store.obs_gaps(cfg.group_id) == ["2026-01-01T00:20:00Z"]


def test_a_failure_does_not_stop_the_rest():
    access = CountingAccess(make_access(unavailable_times=(T0 + timedelta(minutes=10),)))
    store = Store(":memory:")
    cfg = make_config()

    stats = run_tasks(access, store, cfg, _runs())

    assert stats.ran == 2, "one bad frame must not take the run down"
    assert stats.unavailable == 1


def test_a_retired_member_is_not_scheduled():
    cfg = make_config()
    retired = cfg.with_member(
        Member(name="mlcast-det", variable="vil", unit="kg m-2", leads=(10, 20, 30)),
        retire_on=date(2025, 12, 31),
    )
    access = CountingAccess(make_access())
    store = Store(":memory:")

    stats = run_tasks(access, store, retired, _runs())

    assert stats.total == 0
    assert access.field_reads == 0


def test_an_unknown_member_is_recorded_as_unavailable():
    base = make_config()
    cfg = VerificationConfig(
        group_id="unknown-member",
        name="x",
        ruler=base.ruler,
        members=(
            Member(name="mlcast-det", variable="vil", unit="kg m-2", leads=(10,)),
            Member(name="ghost", variable="vil", unit="kg m-2", leads=(10,)),
        ),
        metrics=base.metrics,
    )
    access = CountingAccess(make_access())
    store = Store(":memory:")

    stats = run_tasks(access, store, cfg, [
        RunRef(member="mlcast-det", init_time=T0),
        RunRef(member="ghost", init_time=T0),
    ])

    assert stats.ran == 1
    assert stats.unavailable == 1
    assert store.task_state(cfg.group_id, "ghost", "2026-01-01T00:00:00Z", 10) == "unavailable"


def test_progress_callback_sees_every_task():
    seen: list[str] = []
    access = CountingAccess(make_access())
    store = Store(":memory:")
    cfg = make_config()

    run_tasks(access, store, cfg, _runs(), on_progress=seen.append)

    assert len(seen) == 3
    assert all("mlcast-det" in line for line in seen)


def test_task_state_machine_records_done():
    access = CountingAccess(make_access())
    store = Store(":memory:")
    cfg = make_config()

    run_tasks(access, store, cfg, _runs())

    assert store.task_counts(cfg.group_id) == {"done": 3}
    assert len(store.tasks(cfg.group_id, states=["done"])) == 3


def test_recomputing_overwrites_rather_than_appends():
    access = CountingAccess(make_access())
    store = Store(":memory:")
    cfg = make_config()

    run_tasks(access, store, cfg, _runs())
    before = store.count_rows("yes_or_no", filters={"group_id": cfg.group_id})
    run_tasks(access, store, cfg, _runs(), force=True)
    after = store.count_rows("yes_or_no", filters={"group_id": cfg.group_id})

    assert before == after, "a re-run must replace rows, not pile them up"


# ----------------------------------------------------------------------
# planner helper
# ----------------------------------------------------------------------
def test_todo_filters_by_roster_and_provider():
    cfg = make_config()
    access = make_access()
    refs = todo(access, cfg, init_times=[T0, T0 + timedelta(hours=1)])
    assert len(refs) == 2
    assert {r.member for r in refs} == {"mlcast-det"}


def test_todo_skips_members_the_provider_does_not_have():
    cfg = VerificationConfig(
        group_id="g", name="n", ruler=make_config().ruler,
        members=(Member(name="ghost", variable="vil", unit="kg m-2", leads=(10,)),),
        metrics=make_config().metrics,
    )
    assert todo(make_access(), cfg, init_times=[T0]) == []


# ----------------------------------------------------------------------
# the lock
# ----------------------------------------------------------------------
def test_lock_refuses_a_second_holder(tmp_path):
    first = SingleInstanceLock(tmp_path).acquire()
    try:
        with pytest.raises(Locked):
            SingleInstanceLock(tmp_path).acquire()
    finally:
        first.release()
    # released: a new holder is fine
    SingleInstanceLock(tmp_path).acquire().release()


def test_lock_takes_over_a_stale_pid_file(tmp_path):
    (tmp_path / "verify.lock").write_text("999999999", encoding="utf-8")
    SingleInstanceLock(tmp_path).acquire().release()


def test_a_store_from_an_older_version_is_refused_not_quietly_ignored(tmp_path):
    """The tables are named after the families. A file from before the names were
    unified would answer every query with a gap, which reads as "no data" rather
    than as "wrong file"."""
    import sqlite3

    import pytest

    from nib_verify.store.db import Store

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE q_cat (group_id TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="q_cat"):
        Store(path)
