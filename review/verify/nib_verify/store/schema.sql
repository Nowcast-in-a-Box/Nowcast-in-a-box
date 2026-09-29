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
