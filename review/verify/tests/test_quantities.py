"""Additive quantities, checked against hand-worked answers."""

from __future__ import annotations

import numpy as np
import pytest

from nib_verify.core.quantities import (
    accumulate,
    box_sum,
    categorical,
    continuous,
    moments,
    spatial,
    window_mean,
)

ALL = np.ones((2, 2), dtype=bool)


# ----------------------------------------------------------------------
# categorical
# ----------------------------------------------------------------------
def test_categorical_known_contingency():
    truth = np.array([[1.0, 3.0], [3.0, 4.0]])
    pred = np.array([[2.0, 1.0], [4.0, 4.0]])
    got = categorical(truth, pred, 2.5, ALL)
    # truth >= 2.5 -> [[F, T], [T, T]]   pred >= 2.5 -> [[F, F], [T, T]]
    assert got == {"H": 2.0, "F": 0.0, "M": 1.0, "C": 1.0}


def test_categorical_ignores_invalid_points():
    truth = np.array([[9.0, 9.0], [9.0, 9.0]])
    pred = np.array([[9.0, 9.0], [9.0, 9.0]])
    valid = np.array([[True, False], [False, False]])
    got = categorical(truth, pred, 1.0, valid)
    assert got == {"H": 1.0, "F": 0.0, "M": 0.0, "C": 0.0}


def test_categorical_threshold_above_everything_is_all_correct_negatives():
    truth = np.array([[1.0, 2.0], [3.0, 4.0]])
    got = categorical(truth, truth, 99.0, ALL)
    assert got == {"H": 0.0, "F": 0.0, "M": 0.0, "C": 4.0}


# ----------------------------------------------------------------------
# continuous / moments
# ----------------------------------------------------------------------
def test_continuous_known_sums():
    truth = np.array([[1.0, 2.0], [3.0, 4.0]])
    pred = np.array([[2.0, 2.0], [5.0, 4.0]])
    got = continuous(truth, pred, ALL)
    # differences: +1, 0, +2, 0
    assert got["T"] == 4.0
    assert got["E"] == pytest.approx(3.0)
    assert got["A"] == pytest.approx(3.0)
    assert got["S"] == pytest.approx(5.0)


def test_moments_known_sums():
    truth = np.array([[1.0, 2.0], [3.0, 4.0]])
    pred = np.array([[2.0, 4.0], [6.0, 8.0]])
    got = moments(truth, pred, ALL)
    assert got["T"] == 4.0
    assert got["MX"] == pytest.approx(10.0)
    assert got["MY"] == pytest.approx(20.0)
    assert got["SX"] == pytest.approx(30.0)
    assert got["SY"] == pytest.approx(120.0)
    assert got["SXY"] == pytest.approx(60.0)


def test_empty_selection_is_zeroed_not_nan():
    truth = np.zeros((2, 2))
    none = np.zeros((2, 2), dtype=bool)
    assert continuous(truth, truth, none) == {"T": 0.0, "E": 0.0, "A": 0.0, "S": 0.0}
    assert moments(truth, truth, none)["T"] == 0.0


# ----------------------------------------------------------------------
# neighbourhood sums
# ----------------------------------------------------------------------
def test_box_sum_matches_a_naive_loop():
    rng = np.random.default_rng(7)
    a = rng.normal(size=(11, 13))
    for half in (0, 1, 2, 3):
        got = box_sum(a, half)
        assert got.shape == a.shape
        k = 2 * half + 1
        for i in range(a.shape[0]):
            for j in range(a.shape[1]):
                lo_i, hi_i = max(0, i - half), min(a.shape[0], i + half + 1)
                lo_j, hi_j = max(0, j - half), min(a.shape[1], j + half + 1)
                assert got[i, j] == pytest.approx(a[lo_i:hi_i, lo_j:hi_j].sum())


def test_window_mean_of_ones_is_one():
    a = np.ones((6, 6))
    mean, fully = window_mean(a, 1)
    assert np.allclose(mean, 1.0)
    # a full 3x3 window fits only from (1,1) to (4,4)
    assert fully.sum() == 16
    assert not fully[0, 0]
    assert not fully[5, 5]


# ----------------------------------------------------------------------
# spatial / FSS blocks
# ----------------------------------------------------------------------
def test_identical_fields_give_perfect_fss():
    truth = np.zeros((10, 10))
    truth[2:6, 2:6] = 10.0
    got = spatial(truth, truth.copy(), 5.0, 1, np.ones((10, 10), dtype=bool))
    assert got["fbs"] == pytest.approx(0.0)
    assert got["pob"] == pytest.approx(got["pfo"])
    assert got["pob"] > 0
    assert got["n_valid"] == 64.0  # 8x8 fully-interior windows


def test_disjoint_fields_give_worst_fss():
    truth = np.zeros((10, 10))
    truth[1:4, 1:4] = 10.0
    pred = np.zeros((10, 10))
    pred[6:9, 6:9] = 10.0
    got = spatial(truth, pred, 5.0, 1, np.ones((10, 10), dtype=bool))
    assert got["pfo"] == pytest.approx(got["pob"])
    assert got["fbs"] == pytest.approx(got["pob"] + got["pfo"])
    assert 1.0 - got["fbs"] / (got["pob"] + got["pfo"]) == pytest.approx(0.0)


def test_spatial_needs_fully_valid_windows():
    truth = np.zeros((6, 6))
    valid = np.ones((6, 6), dtype=bool)
    valid[5, 5] = False
    got = spatial(truth, truth, 1.0, 1, valid)
    # 4x4 fully-interior windows, minus the one at (4,4) that touches the hole
    assert got["n_valid"] == 15.0


# ----------------------------------------------------------------------
# the batched entry point
# ----------------------------------------------------------------------
def test_accumulate_dispatches_by_family():
    truth = np.zeros((8, 8))
    truth[2:5, 2:5] = 10.0
    pred = truth.copy()
    valid = np.ones((8, 8), dtype=bool)
    out = accumulate(
        truth, pred, valid,
        families=("cat", "cont", "mom", "spatial"),
        levels=(5.0,),
        half_windows=(1,),
    )
    assert set(out) == {"cat", "cont", "mom", "spatial"}
    assert set(out["cat"]) == {5.0}
    assert set(out["spatial"]) == {(5.0, 1)}
    assert out["cont"]["T"] == 64.0
    assert out["cat"][5.0]["H"] == 9.0


def test_accumulate_rejects_unknown_family():
    with pytest.raises(ValueError):
        accumulate(np.zeros((2, 2)), np.zeros((2, 2)), ALL, families=("nope",))
