"""Completeness per dataset: all time, the last week, and per day.

What "complete" means here, stated once so the table cannot drift from the store:

* a forecast member is complete at one init time when every lead in its roster
  has a recorded result, so expected = len(leads) per init hour;
* the observation is complete in one hour when every cadence frame was used,
  so expected = 60 / time_step_minutes per hour.

Days at the edge of the record are prorated by the hours the record actually
covers, otherwise a half-day at the start drags the percentage down and a frame
that lands on the next day pushes it above 100.

A frame counts as used when some recorded task lands on it (init + lead) and it
is not listed in ``obs_gap``. This is a record of what the store knows, not a
probe of the data source: a day with nothing recorded reads as 0%, which is the
honest answer for this store and not a claim that no data existed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..config.schema import VerificationConfig

UTC = timezone.utc
OBS = "obs"


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pct(done: int, expected: int) -> float | None:
    if expected <= 0:
        return None
    return round(100.0 * done / expected, 1)


def _span(store, cfg) -> tuple[datetime, datetime] | None:
    row = store.conn.execute(
        "SELECT min(init_time), max(init_time) FROM task WHERE group_id = ?",
        (cfg.group_id,),
    ).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    first = _parse(str(row[0]))
    last = _parse(str(row[1]))
    longest = max((max(member.leads) for member in cfg.members), default=0)
    return first, last + timedelta(minutes=int(longest))


def _hours_in_day(day: date, start: datetime, stop: datetime) -> float:
    lo = max(datetime(day.year, day.month, day.day, tzinfo=UTC), start)
    hi = min(datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1), stop)
    return max(0.0, (hi - lo).total_seconds() / 3600.0)


def _probe_days(store, dataset: str, start: datetime, stop: datetime) -> dict[str, dict[str, int]]:
    """Rows the probe wrote, per day: arrived/missing counts, or empty when none."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    try:
        rows = store.conn.execute(
            "SELECT substr(valid_time, 1, 10) AS day, state, count(*) FROM arrival"
            " WHERE dataset = ? AND valid_time >= ? AND valid_time < ?"
            " GROUP BY day, state",
            (dataset, _stamp(start), _stamp(stop)),
        ).fetchall()
    except Exception:                     # a store written before the table existed
        return {}
    for day, state, count in rows:
        out[str(day)][str(state)] = int(count)
    return out


def _quantity_table(store, group_id: str) -> str:
    """Whichever quantities table carries valid_time rows for this group."""
    for table in ("q_continuous", "q_yes_or_no", "q_spatial"):
        row = store.conn.execute(
            f"SELECT 1 FROM {table} WHERE group_id = ? LIMIT 1", (group_id,)).fetchone()
        if row:
            return table
    return ""


def _counts(store, cfg: VerificationConfig, start: datetime, stop: datetime) -> dict[str, Any]:
    """Per day: what each dataset has, and what that day should have held.

    Aggregated in SQL and not in Python: reading 534k rows to call strptime on
    each of them took twelve seconds, which is a hung page, not a slow one.
    """
    step = max(1, int(cfg.ruler.time_step_minutes))
    per_hour_obs = 60 // step

    member_leads = {member.name: len(member.leads) for member in cfg.members}
    fore_done: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for day, member, count in store.conn.execute(
        "SELECT substr(init_time, 1, 10) AS day, member, count(*) FROM task"
        " WHERE group_id = ? AND state = 'done' AND init_time >= ? AND init_time < ?"
        " GROUP BY day, member",
        (cfg.group_id, _stamp(start), _stamp(stop)),
    ):
        fore_done[str(day)][str(member)] = int(count)

    # The probe's answer wins when it exists: it asked the source, while the
    # quantities only say that something was verified. Mixed safely -- a day the
    # probe has not covered yet falls back to the old derivation.
    probed = _probe_days(store, cfg.ruler.ground_truth, start, stop)
    obs_present: dict[str, int] = {}
    table = _quantity_table(store, cfg.group_id)
    if table:
        for day, count in store.conn.execute(
            f"SELECT substr(valid_time, 1, 10) AS day, count(DISTINCT valid_time) FROM {table}"
            " WHERE group_id = ? AND valid_time >= ? AND valid_time < ?"
            " GROUP BY day",
            (cfg.group_id, _stamp(start), _stamp(stop)),
        ):
            obs_present[str(day)] = int(count)

    gaps: dict[str, int] = defaultdict(int)
    for (valid_time,) in store.conn.execute(
        "SELECT valid_time FROM obs_gap WHERE group_id = ? AND valid_time >= ? AND valid_time < ?",
        (cfg.group_id, _stamp(start), _stamp(stop)),
    ):
        gaps[str(valid_time)[:10]] += 1

    days: dict[str, dict[str, Any]] = {}
    day = start.date()
    while day <= stop.date():
        hours = _hours_in_day(day, start, stop)
        key = day.isoformat()
        expected_obs = int(round(hours * per_hour_obs))
        if key in probed:
            arrived = probed[key].get("arrived", 0)
            missing = probed[key].get("missing", 0)
            expected_obs = arrived + missing or expected_obs
            frames = arrived
        else:
            frames = max(0, obs_present.get(key, 0) - gaps.get(key, 0))
        days[key] = {
            "date": key,
            "hours": round(hours, 2),
            "obs": {"done": min(frames, expected_obs), "expected": expected_obs},
            "members": {
                name: {"done": min(fore_done[key].get(name, 0), int(round(hours * leads))),
                       "expected": int(round(hours * leads))}
                for name, leads in member_leads.items()
            },
        }
        day += timedelta(days=1)
    return days


def integrity(store, cfg: VerificationConfig) -> dict[str, Any]:
    """The table: one row per dataset, all time and the last seven days."""
    span = _span(store, cfg)
    if span is None:
        return {"config": cfg.group_id, "name": cfg.name, "rows": [], "span": None,
                "last_week": None, "note": "nothing recorded for this config yet"}
    start, stop = span
    week_start = datetime(stop.date().year, stop.date().month, stop.date().day,
                         tzinfo=UTC) - timedelta(days=6)
    week_start = max(start, week_start)
    days = _counts(store, cfg, start, stop)

    rows = []
    datasets = [(OBS, cfg.ruler.ground_truth, "obs")]
    datasets += [(member.name, member.name, "forecast") for member in cfg.members]
    for key, label, kind in datasets:
        total_done = total_expected = week_done = week_expected = 0
        for day_key, bucket in days.items():
            if kind == "obs":
                done, expected = bucket["obs"]["done"], bucket["obs"]["expected"]
            else:
                done, expected = bucket["members"][key]["done"], bucket["members"][key]["expected"]
            total_done += done
            total_expected += expected
            if _parse(f"{day_key}T00:00:00Z") >= week_start:
                week_done += done
                week_expected += expected
        rows.append({
            "key": key, "kind": kind, "label": label,
            "all_time": {"done": total_done, "expected": total_expected,
                         "pct": _pct(total_done, total_expected)},
            "last_week": {"done": week_done, "expected": week_expected,
                          "pct": _pct(week_done, week_expected)},
        })
    probed_days = len(_probe_days(store, cfg.ruler.ground_truth, start, stop))
    return {
        "config": cfg.group_id,
        "name": cfg.name,
        "obs_source": "probe" if probed_days else "derived",
        "obs_probed_days": probed_days,
        "span": {"from": _stamp(start), "to": _stamp(stop)},
        "last_week": {"from": _stamp(week_start), "to": _stamp(stop)},
        "step_minutes": int(cfg.ruler.time_step_minutes),
        "rows": rows,
        "note": None,
    }


def daily_series(store, cfg: VerificationConfig, *, dataset: str, days: int | None = None) -> dict[str, Any]:
    """One value per day: the curve a number in the table opens."""
    span = _span(store, cfg)
    if span is None:
        return {"dataset": dataset, "days": []}
    start, stop = span
    if days:
        start = max(start, datetime(stop.date().year, stop.date().month, stop.date().day,
                                    tzinfo=UTC) - timedelta(days=days - 1))
    buckets = _counts(store, cfg, start, stop)
    out = []
    for day_key in sorted(buckets):
        bucket = buckets[day_key]
        if dataset == OBS:
            done, expected = bucket["obs"]["done"], bucket["obs"]["expected"]
        else:
            entry = bucket["members"].get(dataset)
            if entry is None:
                continue
            done, expected = entry["done"], entry["expected"]
        out.append({"date": day_key, "done": done, "expected": expected,
                    "pct": _pct(done, expected)})
    return {"dataset": dataset, "days": out, "from": _stamp(start), "to": _stamp(stop)}


def day_detail(store, cfg: VerificationConfig, *, dataset: str, day: str) -> dict[str, Any]:
    """Inside one day: which frames, or which (init, lead) cells, have a record."""
    step = max(1, int(cfg.ruler.time_step_minutes))
    start = datetime.fromisoformat(day).replace(tzinfo=UTC)
    stop = start + timedelta(days=1)

    rows = store.conn.execute(
        "SELECT init_time, member, lead_minutes FROM task"
        " WHERE group_id = ? AND state = 'done' AND init_time >= ? AND init_time < ?",
        (cfg.group_id, _stamp(start - timedelta(hours=6)), _stamp(stop)),
    ).fetchall()
    gaps = {str(r[0]) for r in store.conn.execute(
        "SELECT valid_time FROM obs_gap WHERE group_id = ? AND valid_time >= ? AND valid_time < ?",
        (cfg.group_id, _stamp(start), _stamp(stop)),
    )}

    if dataset == OBS:
        total = (24 * 60) // step
        present: set[str] = set()
        for init_time, _member, lead in rows:
            valid = _parse(str(init_time)) + timedelta(minutes=int(lead))
            if start <= valid < stop:
                present.add(_stamp(valid))
        present -= gaps
        probed = {}
        try:
            for valid_time, state in store.conn.execute(
                "SELECT valid_time, state FROM arrival"
                " WHERE dataset = ? AND valid_time >= ? AND valid_time < ?",
                (cfg.ruler.ground_truth, _stamp(start), _stamp(stop)),
            ):
                probed[str(valid_time)] = str(state)
        except Exception:
            probed = {}
        frames = []
        for index in range(total):
            stamp = _stamp(start + timedelta(minutes=index * step))
            here = (probed[stamp] == "arrived") if stamp in probed else (stamp in present)
            frames.append({"time": stamp, "present": here,
                           "source": "probe" if stamp in probed else "derived"})
        return {"dataset": dataset, "day": day, "kind": "obs", "step_minutes": step,
                "frames": frames, "done": sum(1 for f in frames if f["present"]),
                "expected": len(frames)}

    member = next((m for m in cfg.members if m.name == dataset), None)
    if member is None:
        raise KeyError(f"unknown dataset {dataset!r}")
    leads = [int(lead) for lead in member.leads]
    inits = [_stamp(start + timedelta(hours=hour)) for hour in range(24)]
    have = set()
    for init_time, name, lead in rows:
        if str(name) == dataset and str(init_time) in inits and int(lead) in leads:
            have.add((str(init_time), int(lead)))
    cells = [[1 if (init, lead) in have else 0 for init in inits] for lead in leads]
    return {"dataset": dataset, "day": day, "kind": "forecast",
            "inits": inits, "leads": leads, "cells": cells,
            "done": sum(sum(row) for row in cells),
            "expected": len(inits) * len(leads)}
