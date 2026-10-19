from datetime import timedelta

import numpy as np
import pytest

from utils.stored_fields import add_forecast
from test_stored_fields import fixture_window, START
from test_stored_fields import run as run  # noqa: F811 — pytest fixture shared by these tests


def test_prediction_gap_does_not_mark_observation_gap(run):
    from nib_verify.access.stored import RunAccess
    from nib_verify.config.schema import Ruler, Member, MetricGroup, VerificationConfig
    from nib_verify.runner import run_tasks, RunRef
    from nib_verify.store import Store

    init = START + timedelta(minutes=25)
    config = VerificationConfig(
        "sample",
        "sample",
        Ruler("sevir", "raw", 5, (0.0, 0.0, 9000.0, 9000.0), 3000.0, "EPSG:3857"),
        (Member("wadepre", "vil", "raw", (5,)),),
        (MetricGroup("continuous", ("mae",)),),
    )
    stats = run_tasks(
        RunAccess(run), Store(":memory:"), config, [RunRef("wadepre", init)]
    )
    assert stats.unavailable == 1
    assert stats.obs_missing == 0


def test_three_models_are_all_scored_in_one_verification(run):
    import json
    from diagnostics.verification_run import verification

    models = ("nowcastnet", "exprecast", "wadepre")
    for index, model in enumerate(models):
        add_forecast(
            run,
            model,
            "0.1.0",
            "a" * 64,
            np.full((20, 4, 4), index + 2, np.float32),
            list(range(5, 101, 5)),
        )
    result = verification(
        run,
        [{"family": "continuous", "items": ["mae", "rmse"]}],
        models=list(models),
    )
    summary = json.loads((run / result["path"] / "summary.json").read_text())
    assert result["status"] == "succeeded"
    assert summary["ran"] == 60
    for method in ("mae", "rmse"):
        records = summary["scores"][method]["records"]
        assert len(records) == 60
        assert {record["member"] for record in records} == set(models)


def test_verification_attempts_preserve_previous_reports(run):
    import json
    from diagnostics.verification_run import verification

    add_forecast(
        run,
        "wadepre",
        "0.1.0",
        "a" * 64,
        np.ones((20, 4, 4), np.float32),
        list(range(5, 101, 5)),
    )
    first = verification(run, [{"family": "continuous", "items": ["mae", "rmse"]}])
    assert first["status"] == "succeeded"
    report = (run / first["entry"]).read_bytes()
    second = verification(
        run, [{"family": "yes_or_no", "items": ["ts"], "levels": [1, 2]}]
    )
    assert first["id"] != second["id"]
    assert (run / first["entry"]).read_bytes() == report
    summary = json.loads((run / second["path"] / "summary.json").read_text())
    assert summary["ran"] == 20
    assert len(summary["scores"]["ts"]["records"]) == 40


def test_added_threshold_is_computed_from_saved_fields(run):
    from dataclasses import replace
    from nib_verify.access.stored import RunAccess
    from nib_verify.config.schema import Ruler, Member, MetricGroup, VerificationConfig
    from nib_verify.runner import run_tasks, RunRef
    from nib_verify.store import Store

    add_forecast(
        run,
        "wadepre",
        "0.1.0",
        "a" * 64,
        np.ones((20, 4, 4), np.float32),
        list(range(5, 101, 5)),
    )
    access = RunAccess(run)
    init = START + timedelta(minutes=25)
    config = VerificationConfig(
        "sample",
        "sample",
        Ruler("sevir", "raw", 5, (0.0, 0.0, 9000.0, 9000.0), 3000.0, "EPSG:3857"),
        (Member("wadepre", "vil", "raw", (5,)),),
        (MetricGroup("yes_or_no", ("ts",), (1.0,)),),
    )
    store = Store(":memory:")
    refs = [RunRef("wadepre", init)]
    assert run_tasks(access, store, config, refs).ran == 1
    expanded = replace(config, metrics=(MetricGroup("yes_or_no", ("ts",), (1.0, 2.0)),))
    assert run_tasks(access, store, expanded, refs).ran == 1
    assert store.count_rows("yes_or_no", filters={"level": 2.0}) == 1
    assert run_tasks(access, store, expanded, refs).skipped == 1
    assert access.fields.file_reads == 2


def test_stored_provider_and_visualization_share_scientific_identity(run):
    from nib_verify.access.stored import RunAccess
    from nib_verify.config.schema import Ruler, Member, MetricGroup, VerificationConfig
    from nib_verify.runner import run_tasks, RunRef
    from nib_verify.store import Store
    from nib_verify.query import query_scores
    from diagnostics.fields import visualization_field

    values = np.ones((20, 4, 4), np.float32) * 2
    add_forecast(run, "wadepre", "0.1.0", "a" * 64, values, list(range(5, 101, 5)))
    source = RunAccess(run)
    init = START + timedelta(minutes=25)
    truth = source.get_observation("sevir", init + timedelta(minutes=5))
    config = VerificationConfig(
        "sample",
        "sample",
        Ruler("sevir", "raw", 5, truth.coords.extent, 3000.0, truth.coords.crs),
        (Member("wadepre", "vil", "raw", (5, 10, 100)),),
        (MetricGroup("continuous", ("mae", "rmse")),),
    )
    store = Store(":memory:")
    stats = run_tasks(source, store, config, [RunRef("wadepre", init)])
    assert stats.ran == 3 and stats.failed == 0
    scores = query_scores(store, config, method="mae", g=["lead_minutes"])
    assert all(record["value"] == pytest.approx(1) for record in scores["records"])
    assert source.fields.file_reads == 2  # one target and one Forecast for all tasks
    field = visualization_field(run, "wadepre")
    assert field.units == "raw"
    assert field.crs.to_epsg() == 3857
    np.testing.assert_array_equal(field.values.values, values)
    assert field.provenance["sample_id"] == fixture_window()["sample_id"]
