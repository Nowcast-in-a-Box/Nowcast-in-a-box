"""Ask the data source what it holds, and write the answer down.

This is the one thing the verification store cannot work out on its own. A task
row says "this frame was verified"; it does not say whether the frame arrived and
nobody ran, or the frame never arrived at all. Only the source can answer that,
so the probe asks it -- over a window, on a schedule -- and records the answers
in ``arrival``. Once those rows exist the page can tell the two failures apart.

The probe never reads a run directory: it talks to the field-access interface
(``access.list_observations``) so the answer is about the source and not about
whichever run happened to look. It is idempotent -- running it twice over the
same window writes the same rows -- so a cron that retries, or two schedulers
that overlap, cannot corrupt the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config.schema import VerificationConfig

UTC = timezone.utc


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _floor(moment: datetime, step_minutes: int) -> datetime:
    moment = moment.astimezone(UTC)
    drop = moment.minute % step_minutes
    return moment.replace(minute=moment.minute - drop, second=0, microsecond=0)


@dataclass
class ProbeStats:
    """What one pass over the source found."""

    dataset: str = ""
    checked_at: str = ""
    window_from: str = ""
    window_to: str = ""
    expected: int = 0
    arrived: int = 0
    missing: int = 0
    written: int = 0
    messages: list[str] = field(default_factory=list)

    def as_dict(self, limit: int = 20) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "checked_at": self.checked_at,
            "window": {"from": self.window_from, "to": self.window_to},
            "expected": self.expected,
            "arrived": self.arrived,
            "missing": self.missing,
            "written": self.written,
            "messages": self.messages[:limit],
        }


def probe_arrivals(
    access,
    store,
    cfg: VerificationConfig,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    window_hours: float = 6.0,
    dataset: str | None = None,
) -> ProbeStats:
    """Ask the source for the frames of one window and record each answer.

    ``until`` defaults to the end of the last cadence step before now, and
    ``since`` to ``window_hours`` before it. The window is walked at the ruler's
    own cadence, so "expected" is exactly the number of frames that should exist,
    and a frame the source does not list is written as ``missing`` rather than
    simply left out.
    """
    step = max(1, int(cfg.ruler.time_step_minutes))
    name = dataset or cfg.ruler.ground_truth
    checked = datetime.now(UTC).replace(microsecond=0)

    stop = _floor(until or checked, step)
    start = _floor(since or (stop - timedelta(hours=window_hours)), step)
    if start >= stop:
        start = stop - timedelta(minutes=step)

    stats = ProbeStats(dataset=name, checked_at=_stamp(checked),
                       window_from=_stamp(start), window_to=_stamp(stop))

    expected: list[datetime] = []
    moment = start
    while moment < stop:
        expected.append(moment)
        moment += timedelta(minutes=step)
    stats.expected = len(expected)

    try:
        present = set(access.list_observations(name, start, stop))
    except Exception as problem:                     # a source that is down is a
        stats.messages.append(f"{type(problem).__name__}: {problem}")
        return stats                                 # finding, not a crash

    for moment in expected:
        here = any(abs((moment - seen).total_seconds()) < 1 for seen in present)
        state = "arrived" if here else "missing"
        store.mark_arrival(valid_time=_stamp(moment), dataset=name, kind="obs",
                           state=state, checked_at=stats.checked_at)
        stats.written += 1
        if here:
            stats.arrived += 1
        else:
            stats.missing += 1
    store.conn.commit()

    if stats.missing:
        stats.messages.append(
            f"{stats.missing} of {stats.expected} frames are not in the source")
    return stats
