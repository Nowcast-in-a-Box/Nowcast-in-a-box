"""Storage for additive quantities and task state.

A single SQLite file.  It holds sums and counts only — see ``schema.sql`` for
the rules this design depends on.  Everything the query layer needs is a
``GROUP BY`` plus a ``SUM``; nothing here knows what a "CSI" is.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..core.quantities import FAMILIES

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

UTC = timezone.utc

TASK_STATES = ("pending", "running", "done", "skipped", "failed", "unavailable")

#: non-value columns, per family, in key order. Read off the family itself, so
#: the store cannot disagree with what was written into it.
KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    f.name: f.key_columns for f in FAMILIES
}

#: additive columns, per family — the only things ever summed
VALUE_COLUMNS: dict[str, tuple[str, ...]] = {
    f.name: f.columns for f in FAMILIES
}

TABLE_OF: dict[str, str] = {f.name: f.table for f in FAMILIES}

#: columns that may appear in a WHERE clause
FILTERABLE = frozenset(
    {"group_id", "member", "init_time", "valid_time", "lead_minutes",
     "level", "half_window", "state"}
)

#: columns that may appear in a GROUP BY
GROUPABLE = frozenset(
    {"group_id", "member", "init_time", "valid_time", "lead_minutes",
     "level", "half_window"}
)


@dataclass(frozen=True)
class Between:
    """Inclusive range filter, so a two-element list is never ambiguous."""

    lo: Any
    hi: Any


def iso_utc(t: datetime) -> str:
    """Fixed-width UTC stamp — safe to compare as a string."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """Thin, explicit wrapper over the SQLite file."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._refuse_an_older_layout()
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def _refuse_an_older_layout(self) -> None:
        """Say so if this file was written when the families had other names.

        The tables are named after the families, so a store from before the
        names were unified has data under names nothing reads any more. Creating
        the new tables alongside the old ones would leave a store that answers
        every query with a gap — which reads as "no data", not as "wrong file".
        """
        known = set(TABLE_OF.values())
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'q\\_%' ESCAPE '\\'"
        ).fetchall()
        stale = sorted(r["name"] for r in rows if r["name"] not in known)
        if stale:
            raise ValueError(
                f"{self.path} was written by an older version: it holds "
                f"{', '.join(stale)}, and the quantities now live in "
                f"{', '.join(sorted(known))}. Rebuild the workspace — nothing "
                "here can be carried over, because the names are the keys."
            )

    # -- lifecycle ------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes ---------------------------------------------------------
    def upsert(self, family: str, key: Mapping[str, Any], values: Mapping[str, Any]) -> None:
        """Insert or overwrite one quantity row.

        Overwrite, not append: recomputing a period must be idempotent.
        """
        columns = list(KEY_COLUMNS[family]) + list(VALUE_COLUMNS[family])
        placeholders = ", ".join("?" for _ in columns)
        params = [_coerce(key.get(c, values.get(c))) for c in columns]
        sql = (
            f"INSERT OR REPLACE INTO {TABLE_OF[family]} "
            f"({', '.join(columns)}) VALUES ({placeholders})"
        )
        self.conn.execute(sql, params)

    def upsert_many(self, family: str, rows: Iterable[tuple[Mapping, Mapping]]) -> int:
        n = 0
        for key, values in rows:
            self.upsert(family, key, values)
            n += 1
        self.conn.commit()
        return n

    def commit(self) -> None:
        self.conn.commit()

    # -- existence ------------------------------------------------------
    def has(self, family: str, key: Mapping[str, Any]) -> bool:
        cols = KEY_COLUMNS[family]
        where = " AND ".join(f"{c} = ?" for c in cols)
        sql = f"SELECT 1 FROM {TABLE_OF[family]} WHERE {where} LIMIT 1"
        row = self.conn.execute(sql, [_coerce(key.get(c)) for c in cols]).fetchone()
        return row is not None

    def has_frame(self, group_id: str, member: str, init_time: str, lead_minutes: int,
                  family: str) -> bool:
        """True when any row exists for this frame in ``family``."""
        key = {
            "group_id": group_id,
            "member": member,
            "init_time": init_time,
            "lead_minutes": lead_minutes,
        }
        cols = [c for c in KEY_COLUMNS[family] if c in key]
        where = " AND ".join(f"{c} = ?" for c in cols)
        sql = f"SELECT 1 FROM {TABLE_OF[family]} WHERE {where} LIMIT 1"
        row = self.conn.execute(sql, [_coerce(key[c]) for c in cols]).fetchone()
        return row is not None

    # -- task state -----------------------------------------------------
    def set_task_state(self, group_id: str, member: str, init_time: str,
                       lead_minutes: int, state: str, detail: str | None = None) -> None:
        if state not in TASK_STATES:
            raise ValueError(f"unknown task state {state!r}")
        self.conn.execute(
            "INSERT OR REPLACE INTO task "
            "(group_id, member, init_time, lead_minutes, state, detail, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (group_id, member, init_time, int(lead_minutes), state, detail,
             iso_utc(datetime.now(UTC))),
        )
        self.conn.commit()

    def task_state(self, group_id: str, member: str, init_time: str,
                   lead_minutes: int) -> str | None:
        row = self.conn.execute(
            "SELECT state FROM task WHERE group_id = ? AND member = ? "
            "AND init_time = ? AND lead_minutes = ?",
            (group_id, member, init_time, int(lead_minutes)),
        ).fetchone()
        return row["state"] if row else None

    def tasks(self, group_id: str, states: Sequence[str] | None = None) -> list[dict]:
        sql = "SELECT * FROM task WHERE group_id = ?"
        params: list[Any] = [group_id]
        if states:
            sql += f" AND state IN ({', '.join('?' for _ in states)})"
            params.extend(states)
        sql += " ORDER BY init_time, member, lead_minutes"
        return [dict(r) for r in self.conn.execute(sql, params)]

    def task_counts(self, group_id: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS n FROM task WHERE group_id = ? GROUP BY state",
            (group_id,),
        ).fetchall()
        return {r["state"]: int(r["n"]) for r in rows}

    # -- observation gaps ----------------------------------------------
    def mark_obs_gap(self, group_id: str, obs_name: str, valid_time: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO obs_gap (group_id, obs_name, valid_time) VALUES (?, ?, ?)",
            (group_id, obs_name, valid_time),
        )

    def obs_gaps(self, group_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT valid_time FROM obs_gap WHERE group_id = ? ORDER BY valid_time",
            (group_id,),
        ).fetchall()
        return [r["valid_time"] for r in rows]

    # -- reads ----------------------------------------------------------
    def sum_rows(
        self,
        family: str,
        *,
        filters: Mapping[str, Any] | None = None,
        group_by: Sequence[str] = (),
    ) -> list[dict]:
        """``SELECT group_by, SUM(values) ... GROUP BY group_by``.

        This is the only read path.  A sub-selection is always a sum over a
        subset of rows, because every row carries the full dimension tuple.
        """
        cols = list(group_by)
        for c in cols:
            if c not in GROUPABLE:
                raise ValueError(f"cannot group by {c!r}")
        values = list(VALUE_COLUMNS[family])

        select = ", ".join(cols + [f"SUM({v}) AS {v}" for v in values])
        sql = f"SELECT {select} FROM {TABLE_OF[family]}"
        where, params = _build_where(filters or {})
        if where:
            sql += f" WHERE {where}"
        if cols:
            sql += f" GROUP BY {', '.join(cols)}"
            sql += f" ORDER BY {', '.join(cols)}"

        out = []
        for row in self.conn.execute(sql, params):
            rec = {k: row[k] for k in cols}
            for v in values:
                rec[v] = float(row[v]) if row[v] is not None else 0.0
            out.append(rec)
        return out

    def count_rows(self, family: str, *, filters: Mapping[str, Any] | None = None) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {TABLE_OF[family]}"
        where, params = _build_where(filters or {})
        if where:
            sql += f" WHERE {where}"
        return int(self.conn.execute(sql, params).fetchone()["n"])

    def families_present(self, group_id: str) -> list[str]:
        found = []
        for family, table in TABLE_OF.items():
            n = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE group_id = ?", (group_id,)
            ).fetchone()["n"]
            if n:
                found.append(family)
        return found

    def delete_group(self, group_id: str) -> dict[str, int]:
        """Remove every trace of one config, and nothing else.

        Deleting the whole store file would take every other config's numbers
        with it, so this is scoped by ``group_id``.
        """
        removed: dict[str, int] = {}
        for family, table in TABLE_OF.items():
            cur = self.conn.execute(f"DELETE FROM {table} WHERE group_id = ?", (group_id,))
            if cur.rowcount:
                removed[family] = cur.rowcount
        for table in ("task", "obs_gap"):
            cur = self.conn.execute(f"DELETE FROM {table} WHERE group_id = ?", (group_id,))
            if cur.rowcount:
                removed[table] = cur.rowcount
        self.conn.commit()
        return removed


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _build_where(filters: Mapping[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, want in filters.items():
        if column not in FILTERABLE:
            raise ValueError(f"cannot filter on {column!r}")
        if want is None:
            continue
        if isinstance(want, Between):
            clauses.append(f"{column} BETWEEN ? AND ?")
            params.extend([_coerce(want.lo), _coerce(want.hi)])
        elif isinstance(want, (list, tuple, set, frozenset)):
            items = list(want)
            if not items:
                clauses.append("0 = 1")
                continue
            clauses.append(f"{column} IN ({', '.join('?' for _ in items)})")
            params.extend(_coerce(x) for x in items)
        else:
            clauses.append(f"{column} = ?")
            params.append(_coerce(want))
    return " AND ".join(clauses), params


def _coerce(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        return value
    return str(value)
