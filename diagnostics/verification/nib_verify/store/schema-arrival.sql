-- Added after the first stores were written, so it is applied on demand:
-- opening an existing store must not run DDL, or the WAL grows on every read
-- and the chart cache loses the signal it keys on.
-- ---------------------------------------------------------------------------
-- arrival: what the data source actually held, asked independently of a run.
--
-- No group_id: whether the radar delivered 10:06 is a property of the source and
-- not of whichever configuration happened to look. This is what lets the page
-- tell "the data never arrived" apart from "the run never happened" -- a
-- distinction nothing in the task table can make.
CREATE TABLE IF NOT EXISTS arrival (
    valid_time TEXT NOT NULL,
    dataset    TEXT NOT NULL,
    kind       TEXT NOT NULL,           -- obs | forecast
    state      TEXT NOT NULL,           -- arrived | missing
    checked_at TEXT NOT NULL,
    PRIMARY KEY (valid_time, dataset)
);
CREATE INDEX IF NOT EXISTS ix_arrival_day ON arrival(dataset, valid_time, state);
