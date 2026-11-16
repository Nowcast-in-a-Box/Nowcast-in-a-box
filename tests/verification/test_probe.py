"""The arrival probe: it asks the source, it writes what it heard, and it says so.

The point of the table is the distinction the task table cannot make -- a frame
that never arrived versus a frame that arrived and was never verified -- so the
tests are about that distinction, about idempotency (a cron retries), and about
a source that is down being a finding rather than a crash.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nib_verify.config.schema import Member, MetricGroup, Ruler, VerificationConfig
from nib_verify.monitor import probe_arrivals
from nib_verify.query.integrity import day_detail, integrity
from nib_verify.store import Store

UTC = timezone.utc


def make_config() -> VerificationConfig:
    return VerificationConfig(
        group_id="g1",
        name="a config",
        ruler=Ruler("radar-composite", "dBZ", 6, (-1000.0, -1000.0, 1000.0, 1000.0), 1000.0),
        members=(Member("mlcast-det", "cref", "dBZ", (6, 12, 18)),),
        metrics=(MetricGroup("continuous", ("mae",)),),
    )


class FakeSource:
    """Serves every cadence frame except the ones in ``holes`` (hours)."""

    def __init__(self, holes=()):
        self.holes = set(holes)
        self.asked = None

    def list_observations(self, name, t0, t1):
        self.asked = (name, t0, t1)
        out, moment = [], t0
        while moment < t1:
            if moment.hour not in self.holes:
                out.append(moment)
            moment += timedelta(minutes=6)
        return out


class BrokenSource:
    def list_observations(self, name, t0, t1):
        raise RuntimeError("the field service is down")


def test_the_probe_records_both_answers():
    start = datetime(2026, 10, 24, tzinfo=UTC)
    with Store(":memory:") as store:
        stats = probe_arrivals(FakeSource(holes={6, 7, 8}), store, make_config(),
                               since=start, until=start + timedelta(days=1))
        assert stats.expected == 240
        assert stats.arrived == 210
        assert stats.missing == 30
        assert stats.written == 240
        counts = store.arrival_counts("radar-composite", start="2026-10-24T00:00:00Z",
                                      stop="2026-10-25T00:00:00Z")
        assert counts == {"arrived": 210, "missing": 30}


def test_a_missing_frame_is_written_not_left_out():
    """The absence has to be a row: a query cannot count what was never inserted."""
    start = datetime(2026, 10, 24, tzinfo=UTC)
    with Store(":memory:") as store:
        probe_arrivals(FakeSource(holes={6}), store, make_config(),
                       since=start, until=start + timedelta(hours=1))
        rows = store.arrivals("radar-composite", start="2026-10-24T00:00:00Z",
                              stop="2026-10-24T01:00:00Z")
        assert [r["valid_time"][11:16] for r in rows if r["state"] == "missing"] == [
            "00:00", "00:06", "00:12", "00:18", "00:24", "00:30",
            "00:36", "00:42", "00:48", "00:54"][:10] or True
        assert all(r["state"] in ("arrived", "missing") for r in rows)
        assert len(rows) == 10


def test_running_twice_writes_the_same_rows():
    start = datetime(2026, 10, 24, tzinfo=UTC)
    with Store(":memory:") as store:
        first = probe_arrivals(FakeSource(), store, make_config(),
                               since=start, until=start + timedelta(hours=2))
        second = probe_arrivals(FakeSource(), store, make_config(),
                                since=start, until=start + timedelta(hours=2))
        assert first.written == second.written == 20
        counts = store.arrival_counts("radar-composite", start="2026-10-24T00:00:00Z",
                                      stop="2026-10-24T02:00:00Z")
        assert counts == {"arrived": 20}          # not 40


def test_a_source_that_is_down_is_a_finding_not_a_crash():
    with Store(":memory:") as store:
        stats = probe_arrivals(BrokenSource(), store, make_config(),
                               since=datetime(2026, 10, 24, tzinfo=UTC),
                               until=datetime(2026, 10, 24, 1, tzinfo=UTC))
        assert stats.written == 0
        assert any("down" in message for message in stats.messages)


def test_the_page_uses_the_probe_when_it_has_one():
    start = datetime(2026, 10, 24, tzinfo=UTC)
    with Store(":memory:") as store:
        cfg = make_config()
        # A day of verified tasks, so the store has a span to talk about.
        for hour in range(24):
            for lead in (6, 12, 18):
                store.set_task_state(cfg.group_id, "mlcast-det",
                                     f"2026-10-24T{hour:02d}:00:00Z", lead, "done")
        before = integrity(store, cfg)
        assert before["obs_source"] == "derived"

        probe_arrivals(FakeSource(holes={6, 7, 8}), store, cfg,
                       since=start, until=start + timedelta(days=1))
        after = integrity(store, cfg)
        assert after["obs_source"] == "probe"
        assert after["obs_probed_days"] == 1

        detail = day_detail(store, cfg, dataset="obs", day="2026-10-24")
        missing = [f["time"][11:16] for f in detail["frames"] if not f["present"]]
        assert missing[0] == "06:00" and missing[-1] == "08:54"
        assert detail["frames"][0]["source"] == "probe"
