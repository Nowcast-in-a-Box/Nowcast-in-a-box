"""The acceptance test — D1's ``done_when``, made executable.

D1's declared done condition is:

    Changing a CSI threshold does not start ONNX again.

Inference is already out of the picture here, so the test states the property in
the terms that survive: a threshold is a *query*, not a computation.  Three
things have to hold.

1. **Thresholds are independent.**  CSI at 181 computed from a run that also
   covered 16, 74 and 133 is bit-identical to CSI at 181 computed from a run
   that covered only 181 and 219.
2. **A query never reads a field.**  Re-asking at a different threshold set
   leaves the provider call counter untouched.
3. **Re-running a done task reads nothing.**  A second pass over the same
   period is skipped, and the provider is not called at all.

Plus the efficiency claim that makes the design worth it: the number of
prediction reads equals the number of tasks, no matter how many levels are
requested.  Five thresholds cost the same reads as two.
"""

from __future__ import annotations

from dataclasses import replace

from nib_verify.access import CountingAccess
from nib_verify.query import query_scores
from nib_verify.config.schema import MetricGroup
from nib_verify.runner import RunRef, run_tasks
from nib_verify.store import Store

from .conftest import LEVELS_FULL, LEVELS_SUB, T0, make_access, make_config


def _run(levels):
    """Fresh provider + fresh store + one pass, returning everything."""
    access = CountingAccess(make_access())
    store = Store(":memory:")
    cfg = make_config(levels)
    runs = [RunRef(member="mlcast-det", init_time=T0)]
    stats = run_tasks(access, store, cfg, runs)
    return access, store, cfg, stats


def _csi_records(store, cfg):
    return query_scores(
        store, cfg, method="csi",
        s={"level": list(LEVELS_SUB)},
        g=["lead_minutes", "level"],
    )


# ----------------------------------------------------------------------
# 1. thresholds are independent
# ----------------------------------------------------------------------
def test_thresholds_are_independent():
    _, store_full, cfg_full, _ = _run(LEVELS_FULL)
    _, store_sub, cfg_sub, _ = _run(LEVELS_SUB)

    full = _csi_records(store_full, cfg_full)
    sub = _csi_records(store_sub, cfg_sub)

    assert full["dims"] == ["lead_minutes", "level"]
    assert len(full["records"]) == len(sub["records"]) == 6, "3 leads x 2 levels"
    assert full["records"] == sub["records"], (
        "CSI at 181/219 must not depend on which other thresholds were computed"
    )
    for rec in full["records"]:
        assert rec["value"] is not None, "the synthetic field should make these definite"
        assert 0.0 <= rec["value"] <= 1.0


# ----------------------------------------------------------------------
# 2. a query never reads a field
# ----------------------------------------------------------------------
def test_query_does_not_touch_the_provider():
    access, store, cfg, _ = _run(LEVELS_FULL)
    before = access.field_reads
    assert before > 0

    _csi_records(store, cfg)
    query_scores(store, cfg, method="rmse", g=["lead_minutes"])
    query_scores(store, cfg, method="fss",
                 s={"level": list(LEVELS_SUB), "half_window": [1, 2]},
                 g=["lead_minutes", "level", "half_window"])

    assert access.field_reads == before, "the read path must not call the provider"


# ----------------------------------------------------------------------
# 3. re-running a done task reads nothing
# ----------------------------------------------------------------------
def test_second_run_is_a_noop():
    access = CountingAccess(make_access())
    store = Store(":memory:")
    runs = [RunRef(member="mlcast-det", init_time=T0)]

    cfg_full = make_config(LEVELS_FULL)
    first = run_tasks(access, store, cfg_full, runs)
    assert first.ran == 3 and first.skipped == 0
    reads_after_first = access.field_reads

    # same config again
    again = run_tasks(access, store, cfg_full, runs)
    assert again.ran == 0 and again.skipped == 3
    assert access.field_reads == reads_after_first

    # and a narrower level set: still nothing to do
    cfg_sub = make_config(LEVELS_SUB)
    narrower = run_tasks(access, store, cfg_sub, runs)
    assert narrower.ran == 0 and narrower.skipped == 3
    assert access.field_reads == reads_after_first


# ----------------------------------------------------------------------
# 4. levels do not multiply the reads
# ----------------------------------------------------------------------
def test_reads_scale_with_tasks_not_with_levels():
    runs = [RunRef(member="mlcast-det", init_time=T0)]

    acc_2, _, _, stats_2 = _run(LEVELS_SUB)
    acc_5, _, _, stats_5 = _run(LEVELS_FULL)

    assert stats_2.ran == stats_5.ran == 3
    assert acc_2.prediction_reads == acc_5.prediction_reads == 3
    assert acc_2.observation_reads == acc_5.observation_reads == 3


# ----------------------------------------------------------------------
# 5. force actually overwrites
# ----------------------------------------------------------------------
def test_force_recomputes():
    access = CountingAccess(make_access())
    store = Store(":memory:")
    cfg = make_config(LEVELS_SUB)
    runs = [RunRef(member="mlcast-det", init_time=T0)]

    run_tasks(access, store, cfg, runs)
    before = access.field_reads

    forced = run_tasks(access, store, cfg, runs, force=True)
    assert forced.ran == 3
    assert access.field_reads > before

    assert store.count_rows("cat", filters={"group_id": cfg.group_id}) == 6
    assert store.count_rows("cont", filters={"group_id": cfg.group_id}) == 3
    assert store.count_rows("mom", filters={"group_id": cfg.group_id}) == 3
    assert store.count_rows("spatial", filters={"group_id": cfg.group_id}) == 12


# ----------------------------------------------------------------------
# 6. a metric over a block that is already stored needs no rerun
# ----------------------------------------------------------------------
def test_a_new_metric_over_the_same_block_reads_nothing():
    """DTS is a formula over H, F, M and C — the same four numbers ts is a
    formula over. Adding it asks the store for a column that is already there,
    so it costs a query and nothing else.

    This is the whole reason to store the blocks rather than the scores: a
    threshold is not the only thing that should be changeable for free.
    """
    access = CountingAccess(make_access())
    store = Store(":memory:")
    runs = [RunRef(member="mlcast-det", init_time=T0)]

    before = make_config(LEVELS_SUB)
    assert "dts" not in before.metrics[0].items
    first = run_tasks(access, store, before, runs)
    assert first.ran == 3 and first.skipped == 0
    reads = access.field_reads

    # same ruler, same roster, same thresholds; the metric list is the difference
    after = replace(before, metrics=before.metrics + (MetricGroup(
        family="categorical", items=("dts",), levels=tuple(LEVELS_SUB)),))
    again = run_tasks(access, store, after, runs)

    assert again.ran == 0 and again.skipped == 3, "adding a metric must not re-run"
    assert access.field_reads == reads, "and must not read a field"

    records = query_scores(store, after, method="dts",
                           s={"level": list(LEVELS_SUB)},
                           g=["lead_minutes", "level"])["records"]
    assert records, "the new metric has nothing to read"
    for rec in records:
        assert rec["value"] is not None
        assert 0.0 <= rec["value"] <= 1.0
    assert access.field_reads == reads, "reading it must not either"
