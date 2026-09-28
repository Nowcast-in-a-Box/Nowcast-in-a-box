"""Metric formulas, checked against hand-worked numbers."""

from __future__ import annotations

import math

import pytest

from nib_verify.core.metrics import (
    REGISTRY,
    bias,
    corr,
    csi,
    ets,
    far,
    fss,
    list_metrics,
    mae,
    me,
    pod,
    rmse,
    spec_for,
)

H, F, M, C = 5.0, 3.0, 2.0, 10.0


def test_csi_is_hits_over_forecast_and_observed():
    assert csi(H, F, M, C) == pytest.approx(5.0 / 10.0)


def test_csi_is_nan_when_there_is_nothing_to_score():
    assert math.isnan(csi(0.0, 0.0, 0.0, 100.0))


def test_bias_and_frequency_bias_agree():
    assert bias(H, F, M, C) == pytest.approx(8.0 / 7.0)


def test_pod_and_far():
    assert pod(H, F, M, C) == pytest.approx(5.0 / 7.0)
    assert far(H, F, M, C) == pytest.approx(3.0 / 8.0)


def test_ets_subtracts_the_random_hit_rate():
    n = H + F + M + C
    hits_random = (H + F) * (H + M) / n
    expected = (H - hits_random) / (H + F + M - hits_random)
    assert ets(H, F, M, C) == pytest.approx(expected)
    assert ets(H, F, M, C) < csi(H, F, M, C)


def test_ets_of_a_perfect_forecast_is_one():
    assert ets(10.0, 0.0, 0.0, 90.0) == pytest.approx(1.0)


def test_continuous_family():
    assert me(4.0, 8.0, 12.0, 40.0) == pytest.approx(2.0)
    assert mae(4.0, 8.0, 12.0, 40.0) == pytest.approx(3.0)
    assert rmse(4.0, 8.0, 40.0, 40.0) == pytest.approx(math.sqrt(10.0))


def test_continuous_family_is_nan_without_samples():
    for fn in (me, mae, rmse):
        assert math.isnan(fn(0.0, 0.0, 0.0, 0.0))


def test_corr_of_a_perfect_linear_relation_is_one():
    # x = [1, 2], y = [2, 4]
    assert corr(2.0, 3.0, 6.0, 5.0, 20.0, 10.0) == pytest.approx(1.0)


def test_corr_is_nan_when_a_series_is_constant():
    # x = [1, 1], y = [2, 4]
    assert math.isnan(corr(2.0, 2.0, 6.0, 2.0, 20.0, 6.0))


def test_fss_endpoints():
    assert fss(1.0, 1.0, 0.0) == pytest.approx(1.0)
    assert fss(1.0, 0.0, 1.0) == pytest.approx(0.0)
    assert math.isnan(fss(0.0, 0.0, 0.0))


# ----------------------------------------------------------------------
# the registry
# ----------------------------------------------------------------------
def test_every_metric_declares_the_columns_it_needs():
    allowed = {"H", "F", "M", "C", "T", "E", "A", "S",
               "MX", "MY", "SX", "SY", "SXY", "pob", "pfo", "fbs", "n_valid"}
    for name, spec in REGISTRY.items():
        assert spec.columns, f"{name} declares no columns"
        assert set(spec.columns) <= allowed, f"{name} wants unknown columns"
        assert spec.family in {"cat", "cont", "mom", "spatial"}


def test_compute_reads_a_row_dict():
    spec = spec_for("ts")
    assert spec.compute({"H": 5, "F": 3, "M": 2, "C": 10}) == pytest.approx(0.5)


def test_compute_tolerates_missing_columns():
    assert spec_for("ts").compute({"H": 1, "F": 0, "M": 0}) == pytest.approx(1.0)


def test_unknown_metric_names_the_alternatives():
    with pytest.raises(KeyError) as excinfo:
        spec_for("nope")
    assert "ts" in str(excinfo.value)


def test_list_metrics_reports_required_parameters():
    by_name = {m["method"]: m for m in list_metrics()}
    assert by_name["ts"]["params"] == ["level"]
    assert by_name["fss"]["params"] == ["level", "half_window"]
    assert by_name["rmse"]["params"] == []


def test_alias_ts_and_csi_are_the_same_formula():
    assert spec_for("ts").fn is spec_for("csi").fn
