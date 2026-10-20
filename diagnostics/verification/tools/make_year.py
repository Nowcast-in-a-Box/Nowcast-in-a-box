"""Fill the verification store with a year of intermediate results.

The shapes come from the one real day that is in the store; everything else is a
perturbation of it. This is for measuring how the query side behaves across time
ranges, and for nothing else -- no model was run and no claim about skill survives
from here.

Two things are held fixed, because they belong to the observation and not to the
forecast:

* ``O = H + M`` and ``N = F + C`` -- how many points are above the level in the
  truth, and how many are below. Every member is verified against the same truth,
  so a member may only change how it splits O into hits and misses.
* ``T`` -- the number of points compared.

Everything else moves: skill falls with lead, drifts with the hour of the day and
the time of year, and wanders from day to day.

Usage:  python tools/make_year.py --db STORE --template REAL_STORE [--days 365]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # the package root
from nib_verify.core.metrics import fss as score_fss   # noqa: E402

# ---------------------------------------------------------------- the members
#
# depth is how much skill is lost by the far end of the forecast: 0 would be a
# forecast that never degrades. The real member keeps whatever the real day had,
# so it is the reference the other two are read against.
MEMBERS = [
    {"name": "nowcastnet",      "depth": 0.00, "far": 1.00, "note": "the real run"},
    {"name": "nowcastnet-plus", "depth": -0.35, "far": 0.85, "note": "better everywhere"},
    {"name": "nowcastnet-lite", "depth": 0.55, "far": 1.45, "note": "worse, and decays faster"},
]

TAU = 45.0            # minutes; skill is half gone around here on the real curve


def parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


# ---------------------------------------------------------------- the weather
#
# One number per (day, hour), shared by every member: the day is what it is, and
# the members differ in how well they cope with it.
def day_factor(day: int, rng: random.Random) -> float:
    """How good the day is, on top of the member's own degradation.

    Slow seasonal drift and a day-to-day wander, and nothing else: a day that
    stands out from its neighbours by falling off a cliff is not weather.
    """
    seasonal = 0.050 * math.sin(2.0 * math.pi * (day - 170) / 365.0)
    wander = 0.045 * rng.gauss(0.0, 1.0)
    return seasonal + wander


def hour_factor(hour: int) -> float:
    # Afternoon convection is harder to place: hits fall and false alarms rise.
    return 0.055 * math.cos(2.0 * math.pi * (hour - 15) / 24.0)


def skill(lead: int, member: dict, day: int, hour: int, drifts: dict) -> tuple[float, float]:
    """Return (hit multiplier, false-alarm multiplier) for one task."""
    aging = lead / (lead + TAU)
    degrade = 1.0 - member["depth"] * aging
    weather = day_factor(day, drifts["rng"]) + hour_factor(hour)
    hit = degrade * (1.0 + weather)
    far = (1.0 / degrade) * (1.0 - weather) * member["far"]
    return clip(hit, 0.05, 3.0), clip(far, 0.05, 3.0)


# ---------------------------------------------------------------- generation
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="_tmp/real-demo/verify.sqlite")
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--template", default=None)
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    backup = db_path.with_suffix(".sqlite.real-backup")
    if db_path.exists() and not backup.exists():
        backup.write_bytes(db_path.read_bytes())
        print(f"  真实数据已备份 -> {backup.name}")

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    # The shapes come from the backup, never from the database being written.
    # Read from the target and a second run treats its own half-finished output
    # as the template: 400k rows in, twenty billion rows out.
    src = Path(args.template).resolve() if args.template else (
        backup if backup.exists() else db_path)
    print(f"  模板来源: {src.name}")
    tpl = sqlite3.connect(str(src))
    tpl.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=OFF")

    # ---- read the real day, before anything is touched ----
    group = db.execute("SELECT DISTINCT group_id FROM q_yes_or_no").fetchone()["group_id"]
    yon = [dict(r) for r in tpl.execute(
        "SELECT lead_minutes, level, H, F, M, C FROM q_yes_or_no"
        " WHERE group_id=? AND member='nowcastnet' ORDER BY lead_minutes, level", (group,))]
    con = [dict(r) for r in tpl.execute(
        "SELECT lead_minutes, T, E, A, S FROM q_continuous"
        " WHERE group_id=? AND member='nowcastnet' ORDER BY lead_minutes", (group,))]
    spa = [dict(r) for r in tpl.execute(
        "SELECT lead_minutes, level, half_window, pob, pfo, fbs, n_valid FROM q_spatial"
        " WHERE group_id=? AND member='nowcastnet' ORDER BY lead_minutes, level, half_window", (group,))]
    if not (yon and con and spa):
        print("  库里没有可当模板的真实数据", file=sys.stderr)
        return 1
    leads = sorted({r["lead_minutes"] for r in yon})
    print(f"  模板: {len(leads)} 个时效 · {len(yon)} 行 yes/no · {len(con)} 行 continuous · {len(spa)} 行 spatial")

    # ---- clear the group so the year is uniform ----
    for table in ("q_yes_or_no", "q_continuous", "q_spatial", "q_moments", "task", "run_record"):
        db.execute(f"DELETE FROM {table} WHERE group_id=?", (group,))

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=args.days)
    inits = []
    t = start
    while t < now:
        inits.append(t)
        t += timedelta(hours=1)
    print(f"  起报时刻: {len(inits)} 个（{stamp(inits[0])} … {stamp(inits[-1])}）· 逐小时")

    rng = random.Random(args.seed)
    drifts = {"rng": rng}
    rng_daily = random.Random(args.seed + 1)

    rows_y, rows_c, rows_s, rows_t = [], [], [], []
    started = time.time()
    for idx, init in enumerate(inits):
        day = idx // 24
        hour = init.hour
        # a slow wander so consecutive days are not independent
        if idx % 24 == 0:
            drifts["rng"] = random.Random(args.seed + day)
            drifts["walk"] = rng_daily.gauss(0.0, 1.0)
        for member in MEMBERS:
            for lead in leads:
                valid = stamp(init + timedelta(minutes=lead))
                hit_m, far_m = skill(lead, member, day, hour, drifts)
                for r in yon:
                    if r["lead_minutes"] != lead:
                        continue
                    h, f, m, c = (float(r[k]) for k in ("H", "F", "M", "C"))
                    obs, non = h + m, f + c
                    if obs + non <= 0:
                        continue
                    # A level can have nothing above it in the truth -- H and M are
                    # then both zero and every point sits in F/C. Not an error.
                    hit_rate = h / obs if obs else 0.0
                    far_rate = f / non if non else 0.0
                    H = round(obs * clip(hit_rate * hit_m, 0.0, 1.0))
                    M = obs - H
                    F = round(non * clip(far_rate * far_m, 0.0, 1.0))
                    C = non - F
                    rows_y.append((group, member["name"], stamp(init), valid, lead,
                                   r["level"], H, F, M, C))
                for r in con:
                    if r["lead_minutes"] != lead:
                        continue
                    T = float(r["T"])
                    if T <= 0:
                        continue
                    E, A, S = float(r["E"]), float(r["A"]), float(r["S"])
                    f_hit, f_far = hit_m, far_m
                    E2 = E * (2.0 - clip(f_hit, 0.1, 2.0))
                    A2 = A / max(f_hit, 0.1)
                    S2 = S / max(f_hit, 0.1) ** 2
                    # the arithmetic has to stay legal: |E| <= A and A^2 <= T*S
                    A2 = max(A2, abs(E2))
                    S2 = max(S2, (A2 * A2) / T)
                    rows_c.append((group, member["name"], stamp(init), valid, lead,
                                   T, E2, A2, S2))
                for r in spa:
                    if r["lead_minutes"] != lead:
                        continue
                    pob, pfo, fbs = (float(r[k]) for k in ("pob", "pfo", "fbs"))
                    nv = float(r["n_valid"])
                    # fbs enters FSS multiplicatively, so scaling it with skill sends
                    # the score to -1500. Perturb pob and pfo only -- both of them are
                    # fractions -- and keep fbs as the real row had it.
                    # FSS = 1 - fbs / (pob + pfo). The numerator is the only thing
                    # that can move it legally: scaling pob and pfo apart from each
                    # other shrinks their sum while fbs stays put, which pushes the
                    # score negative -- and a row that is rejected falls back to the
                    # measured value, so every member came out identical.
                    # So leave pob and pfo alone and move fbs, clamped to their sum.
                    denom = pob + pfo
                    worse = clip(clip(far_m, 0.1, 2.0) / clip(hit_m, 0.1, 2.0), 0.05, 4.0)
                    fbs2 = clip(fbs * worse, 0.0, denom) if denom > 0 else fbs
                    pob2, pfo2 = pob, pfo
                    rows_s.append((group, member["name"], stamp(init), valid, lead,
                                   r["level"], r["half_window"], pob2, pfo2, fbs2, nv))
                rows_t.append((group, member["name"], stamp(init), lead, "done", None,
                               stamp(now)))
        if len(rows_y) > 400_000:
            flush(db, rows_y, rows_c, rows_s, rows_t)
            rows_y, rows_c, rows_s, rows_t = [], [], [], []
            print(f"    {idx + 1}/{len(inits)} 起报时刻 · {time.time() - started:.0f}s")
    flush(db, rows_y, rows_c, rows_s, rows_t)

    # a month of run history, so the page has something to list
    for month in range(12):
        when = stamp(now - timedelta(days=30 * (11 - month)))
        db.execute(
            "INSERT OR REPLACE INTO run_record (group_id, started_at, finished_at, seconds,"
            " total, ran, skipped, unavailable, failed, obs_missing, messages)"
            " VALUES (?,?,?,?,?,?,0,0,0,0,'')",
            (group, when, when, round(len(leads) * len(MEMBERS) / 12.0, 3),
             len(leads) * len(MEMBERS), len(leads) * len(MEMBERS)))
    db.commit()
    print("  ANALYZE …")
    db.execute("ANALYZE")
    db.commit()

    counts = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("q_yes_or_no", "q_continuous", "q_spatial", "task", "run_record")}
    size = db_path.stat().st_size / 1e6
    print(f"  完成，用时 {time.time() - started:.0f}s")
    print(f"    q_yes_or_no {counts['q_yes_or_no']:,}")
    print(f"    q_spatial   {counts['q_spatial']:,}")
    print(f"    q_continuous{counts['q_continuous']:,}")
    print(f"    task        {counts['task']:,}")
    print(f"    总行数      {sum(counts.values()):,}    库大小 {size:.0f} MB")
    db.close()

    # ---- the config has to name the members, or the page cannot ask for them ----
    cfg_path = db_path.parent / "configs" / f"{group}.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    have = {m["name"] for m in cfg["members"]}
    template = cfg["members"][0]
    for member in MEMBERS:
        if member["name"] not in have:
            entry = dict(template)
            entry["name"] = member["name"]
            cfg["members"].append(entry)
            print(f"    配置里加了成员 {member['name']}（{member['note']}）")
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"  配置 -> {cfg_path}")
    return 0


def flush(db, rows_y, rows_c, rows_s, rows_t) -> None:
    db.executemany(
        "INSERT OR REPLACE INTO q_yes_or_no (group_id, member, init_time, valid_time,"
        " lead_minutes, level, H, F, M, C) VALUES (?,?,?,?,?,?,?,?,?,?)", rows_y)
    db.executemany(
        "INSERT OR REPLACE INTO q_continuous (group_id, member, init_time, valid_time,"
        " lead_minutes, T, E, A, S) VALUES (?,?,?,?,?,?,?,?,?)", rows_c)
    db.executemany(
        "INSERT OR REPLACE INTO q_spatial (group_id, member, init_time, valid_time,"
        " lead_minutes, level, half_window, pob, pfo, fbs, n_valid)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows_s)
    db.executemany(
        "INSERT OR REPLACE INTO task (group_id, member, init_time, lead_minutes, state,"
        " detail, updated_at) VALUES (?,?,?,?,?,?,?)", rows_t)
    db.commit()


if __name__ == "__main__":
    raise SystemExit(main())
