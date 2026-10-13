"""Task execution: one frame pair in, additive quantities out.

The executor is the only place that reads fields.  It never runs inference, and
it never touches a prediction array it was not handed.  A failure is recorded
against the task and returned in the stats — it is never raised at the caller,
because verification must not be able to block anything upstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Sequence

from ..access.base import FieldAccess, FieldUnavailable, PredictionUnavailable
from ..config.schema import ConfigError, VerificationConfig
from ..core import quantities as Q
from ..core.regrid import align_pair, target_coords
from ..store.db import Store, iso_utc

UTC = timezone.utc

ProgressFn = Callable[[str], None]


def _stamp(when: datetime | None) -> str | None:
    return None if when is None else iso_utc(when)


@dataclass(frozen=True)
class RunRef:
    """One init time for one member."""

    member: str
    init_time: datetime


@dataclass
class RunStats:
    total: int = 0
    ran: int = 0
    skipped: int = 0
    unavailable: int = 0
    failed: int = 0
    obs_missing: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        if self.started_at is None or self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def note(self, message: str, limit: int = 50) -> None:
        if len(self.messages) < limit:
            self.messages.append(message)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "ran": self.ran,
            "skipped": self.skipped,
            "unavailable": self.unavailable,
            "failed": self.failed,
            "obs_missing": self.obs_missing,
            "seconds": round(self.seconds, 3),
            "started_at": _stamp(self.started_at),
            "finished_at": _stamp(self.finished_at),
            "messages": self.messages,
        }


def covers_quantities(
    store: Store, cfg: VerificationConfig, member: str, init_iso: str, lead: int
) -> bool:
    for family in cfg.families():
        filters = {
            "group_id": cfg.group_id,
            "member": member,
            "init_time": init_iso,
            "lead_minutes": lead,
        }
        spec = Q.FAMILY_OF[family]
        expected = 1
        if spec.needs_level:
            filters["level"] = list(cfg.levels())
            expected *= len(cfg.levels())
        if spec.needs_window:
            filters["half_window"] = list(cfg.half_windows())
            expected *= len(cfg.half_windows())
        if store.count_rows(family, filters=filters) != expected:
            return False
    return True


def run_tasks(
    access: FieldAccess,
    store: Store,
    cfg: VerificationConfig,
    runs: Iterable[RunRef],
    *,
    force: bool = False,
    on_progress: ProgressFn | None = None,
) -> RunStats:
    """Execute every (member, init, lead) the roster asks for.

    A task already recorded as ``done`` is skipped unless ``force`` is set.  That
    is what makes a second pass over a subset of levels free: the rows are
    already there and the provider is never called.
    """
    stats = RunStats()
    stats.started_at = datetime.now(UTC)
    target = target_coords(cfg.ruler.extent, cfg.ruler.resolution, cfg.ruler.crs)
    families = cfg.families()
    levels = cfg.levels()
    half_windows = cfg.half_windows()

    for run in runs:
        member = cfg.member(run.member)
        if not member.active_on(run.init_time.date()):
            continue
        for lead in member.leads:
            stats.total += 1
            init_iso = iso_utc(run.init_time)
            if (
                not force
                and store.task_state(cfg.group_id, member.name, init_iso, lead)
                == "done"
                and covers_quantities(store, cfg, member.name, init_iso, lead)
            ):
                stats.skipped += 1
                continue

            valid_time = run.init_time + timedelta(minutes=int(lead))
            store.set_task_state(cfg.group_id, member.name, init_iso, lead, "running")

            try:
                _run_one(
                    access=access,
                    store=store,
                    cfg=cfg,
                    member=member.name,
                    init_time=run.init_time,
                    lead=int(lead),
                    valid_time=valid_time,
                    target=target,
                    families=families,
                    levels=levels,
                    half_windows=half_windows,
                )
            except FieldUnavailable as exc:
                stats.unavailable += 1
                if not isinstance(exc, PredictionUnavailable):
                    stats.obs_missing += 1
                    store.mark_obs_gap(
                        cfg.group_id, cfg.ruler.ground_truth, iso_utc(valid_time)
                    )
                store.set_task_state(
                    cfg.group_id, member.name, init_iso, lead, "unavailable", str(exc)
                )
                stats.note(f"unavailable {member.name} {init_iso} +{lead}: {exc}")
            except Exception as exc:  # noqa: BLE001 — isolation is the point
                stats.failed += 1
                store.set_task_state(
                    cfg.group_id, member.name, init_iso, lead, "failed", repr(exc)
                )
                stats.note(f"failed {member.name} {init_iso} +{lead}: {exc!r}")
            else:
                stats.ran += 1
                store.set_task_state(cfg.group_id, member.name, init_iso, lead, "done")

            if on_progress:
                on_progress(
                    f"{member.name} {init_iso} +{lead} "
                    f"({stats.ran} done / {stats.total} total)"
                )

    # The pass is recorded before it is returned, and in the store rather than in
    # the process, because "when was this last verified" is a question about the
    # record: a page reload or a restart of the daemon must not erase the answer.
    stats.finished_at = datetime.now(UTC)
    store.record_run(
        cfg.group_id,
        started_at=iso_utc(stats.started_at),
        finished_at=iso_utc(stats.finished_at),
        seconds=stats.seconds,
        stats=stats.as_dict(),
        messages=stats.messages,
    )
    return stats


def _run_one(
    *,
    access: FieldAccess,
    store: Store,
    cfg: VerificationConfig,
    member: str,
    init_time: datetime,
    lead: int,
    valid_time: datetime,
    target,
    families: Sequence[str],
    levels: Sequence[float],
    half_windows: Sequence[int],
) -> None:
    truth = access.get_observation(cfg.ruler.ground_truth, valid_time)
    if truth.unit != cfg.ruler.unit:
        # Every threshold in the config is written in the ruler's unit. If the
        # provider hands back something else, the comparisons would still run —
        # and silently mean nothing.
        raise ConfigError(
            f"provider returned {truth.unit!r} but the ruler is in "
            f"{cfg.ruler.unit!r}; thresholds would be meaningless"
        )
    prediction = access.get_prediction(member, init_time, lead)

    t, p, valid = align_pair(truth, prediction, target)

    if not bool(valid.any()):
        raise FieldUnavailable(
            f"no overlapping valid points at {valid_time.isoformat()}"
        )

    key = {
        "group_id": cfg.group_id,
        "member": member,
        "init_time": iso_utc(init_time),
        "valid_time": iso_utc(valid_time),
        "lead_minutes": int(lead),
    }

    payload = Q.accumulate(
        t,
        p,
        valid,
        families=tuple(families),
        levels=tuple(levels),
        half_windows=tuple(half_windows),
    )

    for family in families:
        spec = Q.FAMILY_OF[family]
        result = payload.get(family)
        if not result:
            continue
        if spec.extra_key:
            # A keyed family: the block is stored once per point in its key.
            for point, values in result.items():
                parts = point if isinstance(point, tuple) else (point,)
                store.upsert(
                    family, {**key, **dict(zip(spec.extra_key, parts))}, values
                )
        else:
            store.upsert(family, key, result)
    store.commit()


def todo(
    access: FieldAccess,
    cfg: VerificationConfig,
    *,
    init_times: Sequence[datetime],
) -> list[RunRef]:
    """Every ``(member, init_time)`` pair this config would act on."""
    out: list[RunRef] = []
    for init_time in init_times:
        day: date = init_time.date()
        for member in cfg.members:
            if not member.active_on(day):
                continue
            if member.name not in access.capabilities().model_ids:
                continue
            out.append(RunRef(member=member.name, init_time=init_time))
    return out
