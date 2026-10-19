-- Verification store.
--
-- Design rules encoded here:
--
-- 1. Only additive quantities are stored.  There is no column that holds a
--    ratio, a score, or an average.  Metrics are derived at query time.
-- 2. Every row is keyed by the full dimension tuple, so any sub-selection is a
--    SUM over a subset of rows, never a re-derivation.
-- 3. Writes are upserts.  Recomputing a period overwrites; it never appends.
-- 4. One row serves a whole family.  Adding "ets" next to "csi" costs no row;
--    adding a level costs rows, because a level is a threshold.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- ---------------------------------------------------------------------
-- task state machine: pending -> running -> done | skipped | failed | unavailable
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task (
    group_id     TEXT    NOT NULL,
    member       TEXT    NOT NULL,
    init_time    TEXT    NOT NULL,
    lead_minutes INTEGER NOT NULL,
    state        TEXT    NOT NULL,
    detail       TEXT,
    updated_at   TEXT    NOT NULL,
    PRIMARY KEY (group_id, member, init_time, lead_minutes)
);

CREATE INDEX IF NOT EXISTS ix_task_state ON task(group_id, state);

-- observations that were expected for a group but were not available
CREATE TABLE IF NOT EXISTS obs_gap (
    group_id   TEXT NOT NULL,
    obs_name   TEXT NOT NULL,
    valid_time TEXT NOT NULL,
    PRIMARY KEY (group_id, obs_name, valid_time)
);

-- ---------------------------------------------------------------------
-- yes_or_no: H F M C, one row per threshold
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS q_yes_or_no (
    group_id     TEXT    NOT NULL,
    member       TEXT    NOT NULL,
    init_time    TEXT    NOT NULL,
    valid_time   TEXT    NOT NULL,
    lead_minutes INTEGER NOT NULL,
    level        REAL    NOT NULL,
    H            REAL    NOT NULL,
    F            REAL    NOT NULL,
    M            REAL    NOT NULL,
    C            REAL    NOT NULL,
    PRIMARY KEY (group_id, member, init_time, lead_minutes, level)
);

CREATE INDEX IF NOT EXISTS ix_yes_or_no_vt ON q_yes_or_no(group_id, valid_time, lead_minutes);
CREATE INDEX IF NOT EXISTS ix_yes_or_no_mb ON q_yes_or_no(group_id, member, lead_minutes);

-- ---------------------------------------------------------------------
-- continuous: T E A S, one row per (member, init, lead)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS q_continuous (
    group_id     TEXT    NOT NULL,
    member       TEXT    NOT NULL,
    init_time    TEXT    NOT NULL,
    valid_time   TEXT    NOT NULL,
    lead_minutes INTEGER NOT NULL,
    T            REAL    NOT NULL,
    E            REAL    NOT NULL,
    A            REAL    NOT NULL,
    S            REAL    NOT NULL,
    PRIMARY KEY (group_id, member, init_time, lead_minutes)
);

CREATE INDEX IF NOT EXISTS ix_continuous_vt ON q_continuous(group_id, valid_time, lead_minutes);

-- ---------------------------------------------------------------------
-- moments: T MX MY SX SY SXY
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS q_moments (
    group_id     TEXT    NOT NULL,
    member       TEXT    NOT NULL,
    init_time    TEXT    NOT NULL,
    valid_time   TEXT    NOT NULL,
    lead_minutes INTEGER NOT NULL,
    T            REAL    NOT NULL,
    MX           REAL    NOT NULL,
    MY           REAL    NOT NULL,
    SX           REAL    NOT NULL,
    SY           REAL    NOT NULL,
    SXY          REAL    NOT NULL,
    PRIMARY KEY (group_id, member, init_time, lead_minutes)
);

-- ---------------------------------------------------------------------
-- spatial: pob pfo fbs, one row per (threshold, half-window)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS q_spatial (
    group_id     TEXT    NOT NULL,
    member       TEXT    NOT NULL,
    init_time    TEXT    NOT NULL,
    valid_time   TEXT    NOT NULL,
    lead_minutes INTEGER NOT NULL,
    level        REAL    NOT NULL,
    half_window  INTEGER NOT NULL,
    pob          REAL    NOT NULL,
    pfo          REAL    NOT NULL,
    fbs          REAL    NOT NULL,
    n_valid      REAL    NOT NULL,
    PRIMARY KEY (group_id, member, init_time, lead_minutes, level, half_window)
);

CREATE INDEX IF NOT EXISTS ix_spatial_vt ON q_spatial(group_id, valid_time, lead_minutes);

-- ---------------------------------------------------------------------
-- One row per verification pass that finished.
--
-- The task table says what is done.  This says when it was done and how it went,
-- and it lives in the store rather than in the process because "when was this
-- last verified" is a question about the record.  A page reload or a restart of
-- the daemon must not be able to erase the answer, and in memory it could.
--
-- Nothing here is additive and nothing here is summed: a pass is an event, not a
-- quantity.  It carries the counts the pass ended with, which is what somebody
-- opening the page the next morning is asking for.
--
-- A pass therefore has no primary key.  Two passes can start in the same second
-- and they are still two passes; keying on the timestamp would let the second
-- one replace the first, which is the quietest way to lose the record of a run
-- somebody watched fail.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_record (
    group_id    TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT    NOT NULL,
    seconds     REAL    NOT NULL,
    total       INTEGER NOT NULL,
    ran         INTEGER NOT NULL,
    skipped     INTEGER NOT NULL,
    unavailable INTEGER NOT NULL,
    failed      INTEGER NOT NULL,
    obs_missing INTEGER NOT NULL,
    messages    TEXT    NOT NULL DEFAULT ''
);

-- `rowid` cannot appear in an index; the query uses it only to break a tie.
CREATE INDEX IF NOT EXISTS ix_run_record_when ON run_record(group_id, started_at DESC);

-- Aggregates read every row of a range, and over a year a range is nearly the
-- whole table, so there is nothing to seek past. A covering index is the one
-- thing that helps: it carries the counted columns, so the scan never touches
-- the table. Measured over a year of hourly runs: yes/no 2509 -> 1207 ms,
-- spatial 14963 -> 2360 ms. It costs about a third of the database in size.
CREATE INDEX IF NOT EXISTS ix_yon_cover ON q_yes_or_no
    (group_id, member, lead_minutes, init_time, H, F, M, C);
CREATE INDEX IF NOT EXISTS ix_spa_cover ON q_spatial
    (group_id, member, lead_minutes, level, half_window, init_time, pob, pfo, fbs);
CREATE INDEX IF NOT EXISTS ix_con_cover ON q_continuous
    (group_id, member, lead_minutes, init_time, T, E, A, S);
