"""Planning: a config plus the runs that exist becomes a set of tasks.

    one config  +  many runs  →  many tasks

This is the step the web interface runs before it writes anything, which is why
it produces *reasons* and not just a count. A user who picks a member and sees
"nothing to do" needs to be told which of the five possible reasons applies.

What is decided here, and what is not:

* Here: which ``(member, init_time, lead)`` triples the config asks for, and
  which requested triples the runs cannot supply.
* Not here: whether the provider can actually serve a given frame. That is
  `field_access`'s answer, and a task that turns out to be unservable is
  recorded as ``unavailable`` by the executor rather than being planned away.
  A plan is a statement about intent; the run is a statement about fact.

The distinction matters because files can be pruned after a run. Planning on
"does the file exist" would make the plan wrong the moment it was written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from ..access.base import FieldAccess, FieldUnavailable
from ..catalog.matching import check_pair
from ..catalog.runs import RunBook, RunConfig
from ..config.schema import VerificationConfig

UTC = timezone.utc

#: why a requested task is not in the plan
NOT_ON_ROSTER = "not-on-roster"
INACTIVE = "inactive"
NO_START_TIME = "no-start-time"
BEYOND_HORIZON = "beyond-horizon"
INCOMPATIBLE = "incompatible-pair"

REASON_TEXT = {
    NOT_ON_ROSTER: "the run's model is not on this config's roster",
    INACTIVE: "the member was not on the roster on that date",
    NO_START_TIME: "the run has no start_time (it is identified by sample_id)",
    BEYOND_HORIZON: "no requested lead falls inside the run's forecast horizon",
    INCOMPATIBLE: "the data and model packages do not match",
}


@dataclass(frozen=True)
class PlannedTask:
    group_id: str
    member: str
    init_time: datetime
    lead_minutes: int

    @property
    def valid_time(self) -> datetime:
        return self.init_time + timedelta(minutes=self.lead_minutes)

    def as_dict(self) -> dict:
        return {
            "member": self.member,
            "init_time": _stamp(self.init_time),
            "lead_minutes": self.lead_minutes,
            "valid_time": _stamp(self.valid_time),
        }


@dataclass(frozen=True)
class Rejection:
    """A run that the config looked at and could not use."""

    model_id: str
    reason: str
    detail: str
    init_time: datetime | None = None
    path: str | None = None

    def as_dict(self) -> dict:
        return {
            "model": self.model_id,
            "reason": self.reason,
            "detail": self.detail,
            "init_time": _stamp(self.init_time) if self.init_time else None,
            "path": self.path,
        }


@dataclass
class Plan:
    group_id: str
    cadence_minutes: int
    tasks: list[PlannedTask] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    coverage: dict[str, Any] | None = None

    @property
    def is_empty(self) -> bool:
        return not self.tasks

    @property
    def members(self) -> list[str]:
        return sorted({t.member for t in self.tasks})

    @property
    def leads(self) -> list[int]:
        return sorted({t.lead_minutes for t in self.tasks})

    def init_times(self) -> list[datetime]:
        return sorted({t.init_time for t in self.tasks})

    def for_member(self, member: str) -> list[PlannedTask]:
        return [t for t in self.tasks if t.member == member]

    def reasons(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rejections:
            out[r.reason] = out.get(r.reason, 0) + 1
        return out

    def as_dict(self) -> dict:
        first = self.tasks[0].init_time if self.tasks else None
        last = self.tasks[-1].init_time if self.tasks else None
        return {
            "group_id": self.group_id,
            "cadence_minutes": self.cadence_minutes,
            "tasks": len(self.tasks),
            "init_times": len(self.init_times()),
            "members": self.members,
            "leads": self.leads,
            "span": {
                "from": _stamp(first) if first else None,
                "to": _stamp(last) if last else None,
            },
            "tasks_detail": [t.as_dict() for t in self.tasks],
            "rejections": [r.as_dict() for r in self.rejections],
            "reasons": self.reasons(),
            "notes": list(self.notes),
            "coverage": self.coverage,
        }


# ----------------------------------------------------------------------
def plan(
    cfg: VerificationConfig,
    runbook: RunBook | Iterable[RunConfig],
    *,
    catalog=None,
    access: FieldAccess | None = None,
    probe: bool = False,
) -> Plan:
    """Work out which tasks this config asks for, given the runs that exist.

    ``catalog`` enables the Matcher's pair check when both packages are known.
    ``probe`` additionally asks the provider which frames it can actually serve;
    that is a real round trip per frame, so it is off by default.
    """
    runs = list(runbook)
    result = Plan(group_id=cfg.group_id, cadence_minutes=cfg.ruler.time_step_minutes)

    roster = {m.name: m for m in cfg.members}

    for run in runs:
        member = roster.get(run.model_id)
        if member is None:
            result.rejections.append(_reject(run, NOT_ON_ROSTER,
                                             f"{run.model_id} is not on the roster"))
            continue

        if run.start_time is None:
            result.rejections.append(_reject(run, NO_START_TIME, "run.start_time is absent"))
            continue

        if not member.active_on(run.start_time.date()):
            result.rejections.append(_reject(
                run, INACTIVE,
                f"{member.name} is active {member.active_from or '...'} .. "
                f"{member.active_to or '...'}",
            ))
            continue

        if catalog is not None:
            problem = _pair_problem(catalog, run)
            if problem:
                result.rejections.append(_reject(run, INCOMPATIBLE, problem))
                continue

        allowed = _leads_inside_horizon(run, member.leads, cfg.ruler.time_step_minutes)
        if not allowed:
            result.rejections.append(_reject(
                run, BEYOND_HORIZON,
                f"horizon {run.forecast_horizon} frames x "
                f"{cfg.ruler.time_step_minutes} min covers none of {list(member.leads)}",
            ))
            continue

        dropped = sorted(set(member.leads) - set(allowed))
        if dropped:
            result.notes.append(
                f"{run.when}: lead(s) {dropped} fall outside the run horizon and "
                "were left out"
            )

        for lead in allowed:
            result.tasks.append(PlannedTask(
                group_id=cfg.group_id,
                member=member.name,
                init_time=run.start_time,
                lead_minutes=int(lead),
            ))

    result.tasks.sort(key=lambda t: (t.init_time, t.member, t.lead_minutes))

    if probe and access is not None:
        result.coverage = _coverage(cfg, result, access)

    return result


def planned_runs(plan_result: Plan):
    """Collapse a plan back into the run references the executor wants."""
    from ..runner.executor import RunRef

    seen: dict[tuple[str, datetime], RunRef] = {}
    for task in plan_result.tasks:
        seen.setdefault((task.member, task.init_time),
                        RunRef(member=task.member, init_time=task.init_time))
    return list(seen.values())


# ----------------------------------------------------------------------
def _leads_inside_horizon(
    run: RunConfig, wanted: Sequence[int], cadence_minutes: int
) -> list[int]:
    """Requested leads, clipped to what the run's horizon could have produced.

    A run with an unknown horizon is taken at face value: the provider will say
    no if it cannot serve the lead, and guessing here would drop real data.
    """
    if not run.forecast_horizon:
        return sorted(wanted)
    limit = int(run.forecast_horizon) * max(cadence_minutes, 1)
    return sorted(lead for lead in wanted if 0 < lead <= limit)


def _pair_problem(catalog, run: RunConfig) -> str | None:
    data = catalog.get(run.data_id)
    model = catalog.get(run.model_id)
    if data is None or model is None:
        return None  # unknown packages are not our verdict to give
    match = check_pair(data, model)
    return match.errors[0] if match.errors else None


def _coverage(cfg: VerificationConfig, result: Plan, access: FieldAccess) -> dict:
    """Ask the provider which planned frames it can actually serve."""
    init_times = result.init_times()
    if not init_times:
        return {"expected": 0, "available": 0, "unavailable": []}

    observed: set[datetime] = set()
    try:
        observed = set(access.list_observations(
            cfg.ruler.ground_truth, init_times[0], init_times[-1] + timedelta(hours=6)
        ))
    except FieldUnavailable as exc:
        return {"expected": len(result.tasks), "available": 0,
                "unavailable": [], "error": str(exc)}

    missing: list[dict] = []
    available = 0
    for task in result.tasks:
        if task.valid_time not in observed:
            if len(missing) < 200:
                missing.append(task.as_dict())
            continue
        available += 1

    return {
        "expected": len(result.tasks),
        "available": available,
        "unavailable": len(result.tasks) - available,
        "missing_examples": missing,
        "observation_times_available": len(observed),
    }


def _reject(run: RunConfig, reason: str, detail: str) -> Rejection:
    return Rejection(
        model_id=run.model_id,
        reason=reason,
        detail=detail,
        init_time=run.start_time,
        path=str(run.path) if run.path else None,
    )


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
