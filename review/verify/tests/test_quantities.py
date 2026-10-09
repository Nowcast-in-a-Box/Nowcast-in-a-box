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
    # An edge window holds 6 of its 9 cells, which is over the three-fifths floor,
    # so it counts; only the four corners, at 4 of 9, do not.
    assert got["n_valid"] == 96.0


def test_disjoint_fields_give_worst_fss():
    truth = np.zeros((10, 10))
    truth[1:4, 1:4] = 10.0
    pred = np.zeros((10, 10))
    pred[6:9, 6:9] = 10.0
    got = spatial(truth, pred, 5.0, 1, np.ones((10, 10), dtype=bool))
    assert got["pfo"] == pytest.approx(got["pob"])
    assert got["fbs"] == pytest.approx(got["pob"] + got["pfo"])
    assert 1.0 - got["fbs"] / (got["pob"] + got["pfo"]) == pytest.approx(0.0)


def test_spatial_uses_a_neighbourhood_that_is_mostly_valid():
    """Not one that is *entirely* valid, which is what it used to ask for.

    A single scattered gap anywhere in a 21x21 window used to kill the window,
    and a radar composite is 60-70% valid once the model's own footprint is
    intersected with it -- so FSS came back as a gap at half a window and nowhere
    at all beyond. The floor is three fifths, declared in :data:`MIN_COVERAGE`.
    """
    truth = np.zeros((6, 6))
    valid = np.ones((6, 6), dtype=bool)
    valid[5, 5] = False
    got = spatial(truth, truth, 1.0, 1, valid)
    # every window whose 3x3 touches at most one dead cell, which is all of them
    # except the one that would be entirely the corner
    assert got["n_valid"] == 30.0


def test_a_window_that_is_mostly_hole_is_not_used():
    """The floor has to bite somewhere, or it is not a floor."""
    truth = np.zeros((10, 10))
    valid = np.ones((10, 10), dtype=bool)
    valid[:5, :] = False          # half the grid is gone; 9 of 25 cells left below
    got = spatial(truth, truth, 1.0, 2, valid)
    assert got["n_valid"] > 0, "the deep half still has whole neighbourhoods"
    assert got["n_valid"] < 100, "and the boundary does not"


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
        families=("yes_or_no", "continuous", "moments", "spatial"),
        levels=(5.0,),
        half_windows=(1,),
    )
    assert set(out) == {"yes_or_no", "continuous", "moments", "spatial"}
    assert set(out["yes_or_no"]) == {5.0}
    assert set(out["spatial"]) == {(5.0, 1)}
    assert out["continuous"]["T"] == 64.0
    assert out["yes_or_no"][5.0]["H"] == 9.0


def test_accumulate_rejects_unknown_family():
    with pytest.raises(ValueError):
        accumulate(np.zeros((2, 2)), np.zeros((2, 2)), ALL, families=("nope",))
