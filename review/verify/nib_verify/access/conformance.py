"""The contract's conformance checklist, as something you can run.

``spec/field-access.md`` ends with a list of things that must hold for a provider
to be usable. That list is the reason this module exists, so it should not also
be a paragraph a reviewer has to hold in their head: :func:`check_provider` runs
it against a live provider and returns a report.

It is used three ways:

* ``nib_verify datafeed check`` — after starting a service, see whether it is one.
* A test in ``tests/test_conformance.py`` — proving the reference service passes.
* The same test against deliberately broken providers — proving the checks are
  not vacuous. A checklist that cannot fail is decoration.

Every check calls only the five contract methods. Nothing here knows about HTTP,
files, or this module's internals, which is what makes it usable against the
platform's provider when it arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from .base import Capabilities, Field, FieldAccess, FieldUnavailable, describe_capabilities

UTC = timezone.utc

#: Days to try when looking for something a provider actually has. Spread out on
#: purpose: an archive is not obliged to cover any particular date, and a checker
#: that gave up because it guessed 2026 would be reporting its own guess.
CANDIDATE_DAYS = (
    datetime(2026, 1, 1, tzinfo=UTC),
    datetime(2025, 7, 1, tzinfo=UTC),
    datetime(2024, 1, 1, tzinfo=UTC),
)

#: How far to look for observations on a candidate day. An hour is enough to see
#: a cadence and cheap enough not to matter.
WINDOW = timedelta(hours=1)


@dataclass
class Check:
    """One line of the checklist.

    ``subjects`` is how many fields the line is about. Asking about one
    observation and three leads is four facts and one question, and a report
    that answered it four times would be harder to read than the thing it is
    reporting on.
    """

    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False
    subjects: int = 1

    @property
    def mark(self) -> str:
        return "skip" if self.skipped else ("ok  " if self.ok else "FAIL")

    @property
    def full_detail(self) -> str:
        if self.subjects > 1 and self.detail:
            return f"{self.detail} ({self.subjects} field(s))"
        return self.detail


@dataclass
class Report:
    """What a provider does and does not satisfy."""

    checks: list[Check] = field(default_factory=list)
    capabilities: Capabilities | None = None
    sampled: dict[str, str] = field(default_factory=dict)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and not c.skipped]

    @property
    def ok(self) -> bool:
        """True when nothing that ran failed. Skips are not passes."""
        return not self.failures

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "sampled": dict(self.sampled),
            "capabilities": (
                {
                    "cadence_minutes": self.capabilities.cadence_minutes,
                    "observation_names": list(self.capabilities.observation_names),
                    "model_ids": list(self.capabilities.model_ids),
                    "retrospective": self.capabilities.retrospective,
                    "units": dict(self.capabilities.units),
                }
                if self.capabilities
                else None
            ),
            "checks": [
                {"name": c.name, "ok": c.ok, "skipped": c.skipped,
                 "subjects": c.subjects, "detail": c.detail}
                for c in self.checks
            ],
        }

    def render(self) -> str:
        lines: list[str] = []
        if self.capabilities is not None:
            lines.append(describe_capabilities(self.capabilities))
        for key, value in self.sampled.items():
            lines.append(f"  sampled {key}: {value}")
        if lines:
            lines.append("")
        width = max((len(c.name) for c in self.checks), default=0)
        for check in self.checks:
            line = f"  [{check.mark}] {check.name.ljust(width)}"
            if check.full_detail:
                line += f"  {check.full_detail}"
            lines.append(line)
        failed = len(self.failures)
        lines.append("")
        lines.append(
            "conforms" if self.ok
            else f"does NOT conform: {failed} of {len(self.checks)} check(s) failed"
        )
        return "\n".join(lines)


# ======================================================================
def check_provider(
    access: FieldAccess,
    *,
    days: tuple[datetime, ...] = CANDIDATE_DAYS,
    max_leads: int = 3,
) -> Report:
    """Run the contract's checklist against ``access``.

    Never raises for a provider that misbehaves: a provider that throws where it
    should answer is a failed check, not a crashed checker. The one exception is
    a provider whose ``capabilities()`` cannot be read at all, because every
    other check needs it.
    """
    report = Report()

    try:
        caps = access.capabilities()
    except Exception as exc:  # noqa: BLE001 - the report is the error channel
        report.checks.append(
            Check("capabilities() answers", False, f"{type(exc).__name__}: {exc}")
        )
        return report
    report.capabilities = caps

    _check_capabilities(report, caps)
    name, time = _check_observations(report, access, caps, days)
    _check_predictions(report, access, caps, days, max_leads)
    _check_undeclared_missing(report, access)
    return report


# ----------------------------------------------------------------------
def _check_capabilities(report: Report, caps: Capabilities) -> None:
    report.checks.append(
        Check("capabilities() reports a cadence", caps.cadence_minutes > 0,
              f"{caps.cadence_minutes} minute(s)")
    )
    if not (caps.observation_names or caps.model_ids):
        report.checks.append(
            Check("capabilities() names something", False, "no observations and no models")
        )
        return
    report.checks.append(
        Check("capabilities() names something", True,
              f"{len(caps.observation_names)} observation(s), "
              f"{len(caps.model_ids)} model(s)")
    )

    # A unit is what makes a threshold mean anything, so a provider that does not
    # declare one cannot be validated against a config at all.
    missing = [n for n in (*caps.observation_names, *caps.model_ids) if not caps.unit_of(n)]
    report.checks.append(
        Check("every name declares a unit", not missing,
              "" if not missing else f"no unit for {missing}")
    )


def _check_observations(
    report: Report, access: FieldAccess, caps: Capabilities, days
) -> tuple[str | None, datetime | None]:
    if not caps.observation_names:
        report.checks.append(Check("observations can be listed", True,
                                   "none declared", skipped=True))
        return None, None

    name = caps.observation_names[0]
    times: list[datetime] = []
    time = None
    for day in days:
        try:
            times = access.list_observations(name, day, day + WINDOW)
        except Exception as exc:  # noqa: BLE001
            report.checks.append(
                Check("list_observations answers", False, f"{type(exc).__name__}: {exc}")
            )
            return None, None
        if times:
            time = times[0]
            report.sampled["observation"] = f"{name} @ {time.isoformat()}"
            report.checks.append(
                Check("list_observations answers", True, f"{len(times)} time(s) on {day.date()}")
            )
            break
    else:
        report.checks.append(
            Check("list_observations finds something", True,
                  f"nothing in {len(days)} candidate window(s)", skipped=True)
        )
        return name, None

    if len(times) >= 1:
        report.checks.append(
            Check("listed times are timezone-aware UTC", all(t.tzinfo is not None for t in times))
        )
    if len(times) >= 2:
        gaps = {(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])}
        report.checks.append(
            Check("every served time is on the cadence",
                  gaps == {float(caps.cadence_minutes)},
                  f"gaps {sorted(gaps)} vs cadence {caps.cadence_minutes}")
        )

    # ---------------------------------------------------------------- one field
    try:
        field = access.get_observation(name, time)
    except Exception as exc:  # noqa: BLE001
        report.checks.append(
            Check("get_observation answers", False, f"{type(exc).__name__}: {exc}")
        )
        return name, time

    _add_merged(report, _check_field(field, caps, expect_time=time, expect_name=name,
                                     kind="observation"))

    # ------------------------------------------------------- off-cadence
    # The point of the check is not that it raises, it is that it raises
    # *instead of* quietly producing an interpolated field.
    off = time + timedelta(seconds=1)
    try:
        stray = access.get_observation(name, off)
    except FieldUnavailable:
        report.checks.append(
            Check("an off-cadence time raises rather than interpolating", True,
                  "asked for +1 second")
        )
    except Exception as exc:  # noqa: BLE001
        report.checks.append(
            Check("an off-cadence time raises rather than interpolating", False,
                  f"raised {type(exc).__name__}, not FieldUnavailable: {exc}")
        )
    else:
        report.checks.append(
            Check("an off-cadence time raises rather than interpolating", False,
                  f"returned a field for {stray.valid_time.isoformat()} instead")
        )

    return name, time


def _check_predictions(
    report: Report, access: FieldAccess, caps: Capabilities, days, max_leads: int
) -> None:
    if not caps.model_ids:
        report.checks.append(Check("predictions can be listed", True,
                                   "none declared", skipped=True))
        return

    model = caps.model_ids[0]
    init, leads = None, []
    for day in days:
        try:
            leads = list(access.list_predictions(model, day))
        except Exception as exc:  # noqa: BLE001
            report.checks.append(
                Check("list_predictions answers", False, f"{type(exc).__name__}: {exc}")
            )
            return
        if leads:
            init = day
            break
    else:
        report.checks.append(
            Check("list_predictions finds a run", True,
                  f"nothing in {len(days)} candidate day(s)", skipped=True)
        )
        return

    report.sampled["prediction"] = f"{model} @ {init.isoformat()} leads {leads[:max_leads]}"
    bad = [x for x in leads if not isinstance(x, int) or x < 0]
    report.checks.append(
        Check("leads are whole, non-negative minutes", not bad, "" if not bad else f"{bad}")
    )

    # A listed lead that cannot be fetched is the failure that matters: the
    # planner trusts this list, so a wrong entry becomes a task that dies later.
    rows: list[tuple[str, bool, str]] = []
    unserved = []
    for lead in leads[:max_leads]:
        try:
            field = access.get_prediction(model, init, lead)
        except Exception as exc:  # noqa: BLE001
            unserved.append(f"lead {lead} ({type(exc).__name__}: {exc})")
            continue
        rows.extend(_check_field(field, caps, kind="prediction"))
        expected = init + timedelta(minutes=lead)
        if field.valid_time != expected:
            unserved.append(
                f"lead {lead} says valid_time {field.valid_time.isoformat()}, "
                f"expected {expected.isoformat()}"
            )
    _add_merged(report, rows)
    report.checks.append(
        Check("every listed lead can be fetched", not unserved,
              "" if not unserved else "; ".join(unserved))
    )

    stray = max(leads) + 11
    if stray in leads:
        stray = max(leads) + 1
    try:
        access.get_prediction(model, init, stray)
    except FieldUnavailable:
        report.checks.append(
            Check("an unlisted lead raises rather than inventing a frame", True,
                  f"asked for lead {stray}")
        )
    except Exception as exc:  # noqa: BLE001
        report.checks.append(
            Check("an unlisted lead raises rather than inventing a frame", False,
                  f"raised {type(exc).__name__}, not FieldUnavailable: {exc}")
        )
    else:
        report.checks.append(
            Check("an unlisted lead raises rather than inventing a frame", False,
                  f"lead {stray} was served but is not in {leads[:max_leads]}")
        )


def _check_field(
    field: Field,
    caps: Capabilities,
    *,
    expect_time: datetime | None = None,
    expect_name: str | None = None,
    kind: str,
) -> list[tuple[str, "bool | None", str]]:
    """The per-field guarantees, as rows for :func:`_add_merged`.

    Returned rather than appended so that asking about three leads produces
    three answers to one question, not fifteen lines saying the same thing.
    """
    rows: list[tuple[str, bool, str]] = [
        (f"a {kind} field carries its own grid",
         field.coords.shape == field.data.shape,
         f"{field.coords.shape}, extent {field.coords.extent}"),
        (f"a {kind} field declares a unit", bool(field.unit), repr(field.unit)),
    ]

    declared = caps.unit_of(expect_name or field.name)
    if declared is not None:
        rows.append((f"the {kind} unit is the one capabilities declares",
                     field.unit == declared,
                     f"{field.unit!r} vs {declared!r}"))

    if expect_time is not None:
        rows.append((f"the {kind} is for the time that was asked for",
                     field.valid_time == expect_time, field.valid_time.isoformat()))
    elif kind == "prediction":
        rows.append((f"the {kind} is for the lead that was asked for",
                     True, field.valid_time.isoformat()))

    # Missing must be a hole, not a zero dressed up as one.
    values = field.values()
    missing = field.missing
    if not missing.any():
        rows.append(("missing points come back as NaN, never as zero", None,
                     "nothing missing in the sampled field"))
        return rows

    # Two decidable halves of one property. Nothing outside the mask may be
    # non-finite, and the masked points must actually read as NaN.
    leaked = int((~missing & ~np.isfinite(values)).sum())
    holes = int((~np.isnan(values[missing])).sum())
    rows.append(("missing points come back as NaN, never as zero",
                 leaked == 0 and holes == 0,
                 f"{int(missing.sum())} missing; {leaked} non-finite outside the mask, "
                 f"{holes} masked point(s) not NaN"))
    return rows


def _add_merged(report: Report, rows: list[tuple[str, bool, str]]) -> None:
    """One line per property, however many fields were checked for it.

    ``ok=None`` means the property could not be exercised on this provider —
    nothing was missing, so "missing comes back as NaN" has no witness. That is
    a skip, not a pass. Reporting it as a pass would repeat, one level up, the
    mistake the checklist exists to catch.
    """
    order: list[str] = []
    merged: dict[str, dict] = {}
    for name, ok, detail in rows:
        entry = merged.setdefault(
            name, {"ok": True, "detail": "", "count": 0, "checked": False, "failed": False}
        )
        if name not in order:
            order.append(name)
        entry["ok"] = entry["ok"] and ok is not False
        entry["count"] += 1
        entry["checked"] = entry["checked"] or ok is not None
        # A failure is the most useful thing to show, so it displaces whatever
        # was there; otherwise the first detail is kept, because it is the one
        # that says what was sampled.
        if detail and ok is False and not entry["failed"]:
            entry["detail"], entry["failed"] = detail, True
        elif detail and not entry["detail"]:
            entry["detail"] = detail

    for name in order:
        entry = merged[name]
        existing = next((c for c in report.checks if c.name == name), None)
        if existing is None:
            report.checks.append(
                Check(name, entry["ok"], entry["detail"],
                      skipped=not entry["checked"], subjects=entry["count"])
            )
            continue
        # The same property, checked on another field. One line, not two: a
        # reader wants to know whether the provider is right, not how many times
        # it was asked. This is why asking about an observation and three leads
        # produces four facts and one question.
        existing.subjects += entry["count"]
        existing.skipped = existing.skipped and not entry["checked"]
        if not entry["ok"]:
            existing.ok = False
            if entry["detail"]:
                existing.detail = entry["detail"]
        elif existing.ok and not existing.detail:
            existing.detail = entry["detail"]


def _check_undeclared_missing(report: Report, access: FieldAccess) -> None:
    """Did the provider say what it was missing?

    The decoder widens the mask when a payload carries NaN it did not declare,
    and records a warning. Passing that silently would be the exact failure the
    contract warns about: NaN compared against a threshold is False, lands in
    the correct-negative column, and makes the score look better.
    """
    warnings = list(getattr(access, "warnings", []) or [])
    report.checks.append(
        Check("the provider declares what it is missing", not warnings,
              "" if not warnings else warnings[0])
    )
