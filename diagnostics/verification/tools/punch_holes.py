"""Punch holes in the year store so the coverage page has something to find.

Every hole is a story a real operator would recognise:

* three whole hours missing (a scheduler that stopped for three cycles);
* one member missing its far leads for half a day (an inference that died);
* a stretch of observation frames missing (a data feed that dropped out);
* a member missing scattered leads (a run that half finished).

``--restore`` puts every row back from the backup this script writes, so the
store can be clean again for the next demo.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path("_tmp/real-demo/verify.sqlite")
BACKUP = Path("_tmp/real-demo/_punch_backup.json")

# (day, hour-or-None, member-or-None, lead predicate)
HOLES_TASK = [
    # ① 三个整小时都没跑（2026-10-22 03/04/05 UTC）—— 最近一周内，近一周那列也会掉
    ("2026-10-22", ["03", "04", "05"], None, None),
    # ② nowcastnet-plus 在 2026-10-20 上午缺远端时效（54 分钟以后）
    ("2026-10-20", [f"{h:02d}" for h in range(0, 12)], "nowcastnet-plus", "far"),
    # ③ nowcastnet 在 2026-09-15 零散缺几个时效
    ("2026-09-15", [f"{h:02d}" for h in range(6, 12)], "nowcastnet", "scatter"),
    # ④ nowcastnet-lite 整整三天没跑（2026-09-20..22）—— 表上能看出低一截
    ("2026-09-20", None, "nowcastnet-lite", None),
    ("2026-09-21", None, "nowcastnet-lite", None),
    ("2026-09-22", None, "nowcastnet-lite", None),
    # ⑤ 分散在全年，让逐日曲线上能看出几个分开的凹口
    ("2026-01-15", [f"{h:02d}" for h in range(2, 8)], None, None),
    ("2026-03-08", [f"{h:02d}" for h in range(14, 20)], None, None),
    ("2026-04-20", [f"{h:02d}" for h in range(0, 12)], "nowcastnet-plus", "far"),
    ("2026-06-10", None, "nowcastnet-lite", None),
    ("2026-07-28", [f"{h:02d}" for h in range(9, 13)], "nowcastnet", "scatter"),
]

# 实况缺帧：整天里挖两段（dropout）
GAPS = [
    ("2026-08-05", 10 * 60, 24),   # 10:00 起 24 帧（144 分钟 ≈ 2.4 小时）
    ("2026-08-05", 18 * 60, 12),
    ("2026-10-24", 6 * 60, 30),    # 最近一周内，整三小时实况断流
    ("2026-10-24", 15 * 60, 40),
]


def connect():
    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row
    return db


def stamps(day: str, hours):
    if hours is None:
        return [f"{day}T{h:02d}:00:00Z" for h in range(24)]
    return [f"{day}T{h}:00:00Z" for h in hours]


def apply(db) -> dict:
    backup = {"task": [], "obs_gap": []}
    deleted = 0
    for day, hours, member, rule in HOLES_TASK:
        inits = stamps(day, hours)
        for init in inits:
            sql = "SELECT * FROM task WHERE init_time = ?"
            args = [init]
            if member:
                sql += " AND member = ?"
                args.append(member)
            rows = [dict(r) for r in db.execute(sql, args)]
            if rule == "far":
                rows = [r for r in rows if int(r["lead_minutes"]) >= 54]
            elif rule == "scatter":
                rows = [r for r in rows if int(r["lead_minutes"]) in (54, 60, 66, 72, 108)]
            for row in rows:
                db.execute(
                    "DELETE FROM task WHERE group_id=? AND member=? AND init_time=? AND lead_minutes=?",
                    (row["group_id"], row["member"], row["init_time"], row["lead_minutes"]))
                backup["task"].append(row)
                deleted += 1
        print(f"  {day} {hour_range(hours)} {member or 'all members'} → 删 {deleted} 行（累计）")

    added = 0
    for day, start_minute, count in GAPS:
        hour, minute = divmod(start_minute, 60)
        for i in range(count):
            total = hour * 60 + minute + i * 6
            stamp = f"{day}T{total // 60:02d}:{total % 60:02d}:00Z"
            exists = db.execute(
                "SELECT 1 FROM obs_gap WHERE group_id=(SELECT group_id FROM task LIMIT 1)"
                " AND obs_name='cma_radar' AND valid_time=?", (stamp,)).fetchone()
            if exists:
                continue
            db.execute(
                "INSERT OR IGNORE INTO obs_gap (group_id, obs_name, valid_time)"
                " SELECT DISTINCT group_id, 'cma_radar', ? FROM task", (stamp,))
            backup["obs_gap"].append(stamp)
            added += 1
        print(f"  {day} 实况缺帧 {start_minute//60:02d}:{start_minute%60:02d} 起 {count} 帧")
    db.commit()
    BACKUP.write_text(json.dumps(backup), encoding="utf-8")
    print(f"  共删除 task {deleted} 行 · 插入 obs_gap {added} 条 → 备份 {BACKUP}")
    return backup


def hour_range(hours):
    if hours is None:
        return "00-23"
    return f"{hours[0]}..{hours[-1]}"


def restore(db) -> None:
    if not BACKUP.exists():
        sys.exit("  没有备份文件，无法还原")
    backup = json.loads(BACKUP.read_text(encoding="utf-8"))
    for row in backup["task"]:
        db.execute(
            "INSERT OR REPLACE INTO task (group_id, member, init_time, lead_minutes,"
            " state, detail, updated_at) VALUES (?,?,?,?,?,?,?)",
            (row["group_id"], row["member"], row["init_time"], row["lead_minutes"],
             row["state"], row["detail"], row["updated_at"]))
    for stamp in backup["obs_gap"]:
        db.execute("DELETE FROM obs_gap WHERE valid_time = ?", (stamp,))
    db.commit()
    print(f"  还原 task {len(backup['task'])} 行 · 删除 obs_gap {len(backup['obs_gap'])} 条")
    BACKUP.unlink()


if __name__ == "__main__":
    db = connect()
    if "--restore" in sys.argv:
        restore(db)
    else:
        apply(db)
    db.close()
