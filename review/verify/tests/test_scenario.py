"""The named scenarios, and the structure they turn on.

Two jobs here.

The first is that the scenarios are usable: every one of them has to produce data
the config that ships with this module can actually be verified against, or it is
a preset that gives you a unit error.

The second is that the structure is real. A generator whose forecast equals the
truth is what the acceptance tests need and the worst thing to develop an
interface against — every CSI comes back 1.0 and a broken display looks exactly
like a working one. So the tests below assert the *properties* the extra knobs
are for: a lagged forecast is displaced rather than blurred, coverage gaps are
contiguous rather than speckled, and the second model is genuinely worse than the
first.

And throughout: the defaults have not moved. Everything here is additive.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from nib_verify.access import FieldUnavailable, scenario
from nib_verify.access.factory import build_access
from nib_verify.access.synthetic import MISSING_KINDS, SyntheticAccess

from .conftest import T0, make_access

SCENARIO_NAMES = scenario.names()


def provider(name: str, **over) -> SyntheticAccess:
    """A scenario on **one** grid, so a prediction can be compared with the
    truth directly. The generator puts observations and models on different
    native grids by design; that is what the resampler is for and not what any
    of these tests are about.
    """
    settings = {**scenario.settings(name)}
    settings.setdefault("model_shape", settings.get("obs_shape", (48, 64)))
    return SyntheticAccess(**{**settings, **over})


def error_against_truth(access, lead, model, when=T0) -> float:
    """RMSE of one prediction against the observation at the same instant."""
    predicted = access.get_prediction(model, when, lead)
    observed = access.get_observation("radar-composite", when + timedelta(minutes=lead))
    difference = (predicted.values() - observed.values()) ** 2
    with np.errstate(invalid="ignore"):
        return float(np.sqrt(np.nanmean(difference)))


def interior_points(mask: np.ndarray) -> int:
    """Missing points whose four neighbours are missing too.

    A contiguous blob is full of them and scattered speckle has none, which is
    the difference between the two kinds without needing a connected-component
    labelling.
    """
    core = (
        mask[1:-1, 1:-1]
        & mask[:-2, 1:-1] & mask[2:, 1:-1]
        & mask[1:-1, :-2] & mask[1:-1, 2:]
    )
    return int(core.sum())


# ======================================================================
# the defaults have not moved
# ======================================================================
def test_the_bare_generator_is_still_one_blob():
    access = SyntheticAccess()
    assert (access.cells, access.drift, access.background) == (1, 0.0, 0.0)
    assert access.forecast_lag_minutes == 0.0
    assert access.missing_kind == "random"


def test_a_perfect_forecast_is_still_exactly_perfect():
    """The property a great many other tests rest on."""
    access = make_access()
    field = access.get_observation("radar-composite", T0)
    prediction = access.get_prediction("mlcast-det", T0, 30)
    assert np.array_equal(access.blob(access.obs_coords, T0), field.data)
    assert prediction.data.shape == access.model_coords.shape


def test_cell_zero_is_untouched_by_the_new_parameters():
    """``cells=1, drift=0`` has to be the same field, or every expectation that
    a noiseless forecast equals the truth needs rewriting."""
    plain = SyntheticAccess()
    decorated = SyntheticAccess(cells=1, background=0.0,
                                forecast_lag_minutes=30.0, skill_spread=2.0,
                                missing_kind="none")
    coords = plain.obs_coords
    assert np.array_equal(plain.blob(coords, T0), decorated.blob(coords, T0))


# ======================================================================
# every scenario is usable against the shipped config
# ======================================================================
@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_a_scenario_matches_the_ruler_of_the_shipped_config(name):
    """The config says vil in kg m-2. A scenario that says dBZ is a unit error
    the moment somebody tries it."""
    access = provider(name)
    caps = access.capabilities()
    assert caps.unit_of("radar-composite") == "kg m-2"
    field = access.get_observation("radar-composite", T0)
    assert field.unit == "kg m-2"
    assert field.variable == "vil"


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_a_scenario_produces_a_field_with_something_in_it(name):
    """All-zero or all-clipped data would make every threshold degenerate and
    every metric trivially right."""
    access = provider(name)
    values = access.get_observation("radar-composite", T0).values()
    assert np.nanmax(values) > 100.0
    assert np.nanmin(values) >= 0.0
    assert np.nanstd(values) > 1.0


def test_every_scenario_is_named_and_described():
    assert SCENARIO_NAMES[0] == "simple"
    for name in SCENARIO_NAMES:
        assert scenario.DESCRIPTIONS[name]
        assert name in scenario.catalogue()
        assert name in scenario.catalogue(sep="; ")


def test_an_unknown_scenario_lists_the_real_ones():
    with pytest.raises(ValueError, match="simple.*convective.*widespread.*patchy"):
        scenario.settings("convectiv")          # a typo, which is the point


def test_settings_can_be_overridden_by_the_caller():
    assert provider("convective", peak=999.0).peak == 999.0


def test_the_factory_understands_a_scenario():
    access = build_access("mock", scenario="convective")
    assert access.model_ids == ("mlcast-det", "mlcast-alt")
    assert access.cells == 4
    # an explicit setting still wins over the bundle
    assert build_access("mock", scenario="convective", peak=1.0).peak == 1.0


# ======================================================================
# the structure is real
# ======================================================================
def test_an_unknown_missing_kind_is_refused():
    with pytest.raises(ValueError, match="unknown missing_kind"):
        SyntheticAccess(missing_kind="sometimes")
    assert "random" in MISSING_KINDS and "range" in MISSING_KINDS


def test_more_cells_means_more_weather():
    coords = SyntheticAccess().obs_coords
    one = SyntheticAccess()
    many = SyntheticAccess(cells=5, drift=0.05)
    assert not np.array_equal(one.blob(coords, T0), many.blob(coords, T0))
    assert np.count_nonzero(many.blob(coords, T0) > 50) > np.count_nonzero(
        one.blob(coords, T0) > 50
    )


def test_a_background_raises_the_floor_over_the_whole_domain():
    coords = SyntheticAccess().obs_coords
    dry = SyntheticAccess()
    wet = SyntheticAccess(background=30.0)
    dry_values, wet_values = dry.blob(coords, T0), wet.blob(coords, T0)
    # the corner is where a single cell contributes essentially nothing, so it is
    # the corner that shows the background is broad rather than another blob
    assert dry_values[0, 0] < 0.01 * dry.peak
    assert wet_values[0, 0] > 5.0
    assert np.nanmin(wet_values) > np.nanmin(dry_values)


def test_a_lagging_forecast_is_the_field_from_earlier_not_a_blurred_one():
    """The displacement is the point. A blurred field would score the same at
    every threshold; a displaced one loses the cores and keeps the edges."""
    access = SyntheticAccess(cells=3, drift=0.08, forecast_lag_minutes=30.0,
                             forecast_noise=0.0, truth_noise=0.0)
    lead = 30
    predicted = access.get_prediction("mlcast-det", T0, lead)
    observed = access.get_observation("radar-composite", T0 + timedelta(minutes=lead))
    expected = access.blob(access.model_coords, T0)      # i.e. (T0+30min) - 30min
    assert np.allclose(predicted.data, expected)
    assert not np.array_equal(predicted.data, observed.data)


def test_the_second_model_is_worse_than_the_first():
    """Why the ordering of model_ids is the whole content of skill_spread: a
    comparison needs two models that differ, and a visible winner."""
    access = provider("convective", forecast_noise=4.0)
    good = error_against_truth(access, 30, "mlcast-det")
    worse = error_against_truth(access, 30, "mlcast-alt")
    assert worse > good


def test_skill_spread_of_zero_makes_the_models_identical():
    access = SyntheticAccess(model_ids=("a", "b"), forecast_lag_minutes=10.0,
                             skill_spread=0.0)
    assert np.array_equal(
        access.get_prediction("a", T0, 30).data,
        access.get_prediction("b", T0, 30).data,
    )


def test_a_coverage_gap_is_contiguous_and_speckle_is_not():
    """Two different failure modes for whatever resamples onto the config grid:
    a large hole catches a step that fills, scattered points catch one that
    smears."""
    coords = SyntheticAccess().obs_coords
    gap = SyntheticAccess(missing_kind="range")._missing_mask(coords, T0)
    speckle = SyntheticAccess(missing_kind="random", missing_points=50)._missing_mask(
        coords, T0
    )
    assert gap is not None and speckle is not None
    assert gap.mean() > 0.05
    assert interior_points(gap) > 20
    assert interior_points(speckle) == 0


def test_missing_kind_none_means_none():
    access = SyntheticAccess(missing_kind="none", missing_points=500)
    assert access.get_observation("radar-composite", T0).mask is None


def test_a_dropped_lead_is_listed_by_neither_method():
    access = SyntheticAccess(horizon_minutes=60, drop_leads=(20,))
    leads = access.list_predictions("mlcast-det", T0)
    assert 20 not in leads and 30 in leads
    with pytest.raises(FieldUnavailable, match="lead 20"):
        access.get_prediction("mlcast-det", T0, 20)


def test_a_missing_run_spares_the_first_one():
    """Counting from one. ``index % n == 0`` would blank the run at t_start,
    which is the run every first look begins with."""
    access = SyntheticAccess(cadence_minutes=10, missing_runs_every=3,
                             horizon_minutes=60)
    assert access.list_predictions("mlcast-det", T0)          # +0 min: present
    assert access.list_predictions("mlcast-det", T0 + timedelta(minutes=10))
    assert not access.list_predictions("mlcast-det", T0 + timedelta(minutes=20))
    assert access.list_predictions("mlcast-det", T0 + timedelta(minutes=30))
    assert not access.list_predictions("mlcast-det", T0 + timedelta(minutes=50))
    with pytest.raises(FieldUnavailable):
        access.get_prediction("mlcast-det", T0 + timedelta(minutes=20), 10)


def test_the_generator_is_still_deterministic():
    """Two runs of the same scenario produce the same weather, or nothing else
    here means anything."""
    for name in SCENARIO_NAMES:
        a, b = provider(name), provider(name)
        assert np.array_equal(
            a.get_observation("radar-composite", T0).data,
            b.get_observation("radar-composite", T0).data,
        ), name


def test_a_different_seed_is_different_weather():
    a = SyntheticAccess(cells=3, drift=0.05)
    b = SyntheticAccess(cells=3, drift=0.05, seed=1)
    coords = a.obs_coords
    assert not np.array_equal(a.blob(coords, T0), b.blob(coords, T0))


# ======================================================================
# the reading is no longer trivial
# ======================================================================
@pytest.mark.parametrize("name", ["convective", "widespread", "patchy"])
def test_a_realistic_scenario_does_not_score_a_perfect_one(name):
    """The whole reason the scenarios exist. With the bare defaults every CSI is
    1.0, so a display that always prints 1.0 looks correct."""
    access = provider(name)
    predicted = access.get_prediction("mlcast-det", T0, 10)
    observed = access.get_observation("radar-composite", T0 + timedelta(minutes=10))
    assert not np.allclose(predicted.values(), observed.values(), equal_nan=True)


def test_simple_really_is_a_perfect_forecast():
    access = provider("simple")
    predicted = access.get_prediction("mlcast-det", T0, 30)
    observed = access.get_observation("radar-composite", T0 + timedelta(minutes=30))
    assert np.array_equal(predicted.data, observed.data)


def test_simple_declares_only_the_default_names():
    """Not an accident, and worth documenting rather than hiding: a config that
    rosters a second model gets ``unavailable`` from ``simple``, because the
    provider really does not have it."""
    access = provider("simple")
    assert access.model_ids == ("mlcast-det",)
    assert access.observation_names == ("radar-composite",)


# ======================================================================
# several observation products from one provider
# ======================================================================
def _fleet(**overrides):
    return SyntheticAccess(
        t_start=T0,
        observation_names=("radar-composite", "radar-dbz-hires", "satellite-vil"),
        model_ids=("mlcast-det", "mlcast-alt"),
        sources={
            "radar-composite": {"variable": "vil", "unit": "kg m-2", "scale": 1.0,
                                "missing_kind": "range", "missing_points": 60},
            "radar-dbz-hires": {"variable": "dbz", "unit": "dBZ", "scale": 0.25,
                                "vmin": -10.0, "vmax": 70.0,
                                "missing_kind": "none", "missing_points": 0},
            "satellite-vil": {"variable": "vil", "unit": "kg m-2", "scale": 0.9,
                              "noise": 9.0, "missing_kind": "random",
                              "missing_points": 120},
        },
        model_sources={"mlcast-det": "radar-composite",
                       "mlcast-alt": "radar-dbz-hires"},
        **overrides,
    )


def test_each_product_reports_its_own_unit():
    caps = _fleet().capabilities()
    assert caps.unit_of("radar-composite") == "kg m-2"
    assert caps.unit_of("radar-dbz-hires") == "dBZ"
    assert caps.unit_of("satellite-vil") == "kg m-2"


def test_a_model_reports_the_product_it_copies():
    """A model that nowcasts reflectivity produces reflectivity, and saying
    otherwise would put it in a config whose ruler is in another unit."""
    fleet = _fleet()
    field = fleet.get_prediction("mlcast-alt", T0, 10)
    assert (field.variable, field.unit) == ("dbz", "dBZ")
    other = fleet.get_prediction("mlcast-det", T0, 10)
    assert (other.variable, other.unit) == ("vil", "kg m-2")


def test_products_of_one_storm_are_not_the_same_field():
    """Otherwise the second source is the first wearing another name."""
    fleet = _fleet(truth_noise=0.0)
    vil = fleet.get_observation("radar-composite", T0).data
    dbz = fleet.get_observation("radar-dbz-hires", T0).data
    sat = fleet.get_observation("satellite-vil", T0).data
    assert np.allclose(dbz, vil * 0.25, atol=1e-9), "same storm, another scale"
    assert not np.allclose(sat, vil), "the second VIL product is not the first renamed"
    # what separates it is its own instrument noise, on top of its own scale
    assert float(np.abs(sat - vil * 0.9).mean()) > 1.0


def test_a_product_keeps_its_own_coverage():
    fleet = _fleet(truth_noise=0.0)
    assert fleet.get_observation("radar-dbz-hires", T0).mask is None
    for name in ("radar-composite", "satellite-vil"):
        mask = fleet.get_observation(name, T0).mask
        assert mask is not None and mask.any(), name


def test_a_product_keeps_its_own_clipping():
    fleet = _fleet()
    assert float(fleet.get_observation("radar-dbz-hires", T0).data.max()) <= 70.0


def test_saying_nothing_about_sources_changes_nothing():
    """The default has to stay byte for byte what it was, or every expectation
    written before products existed moves."""
    plain = SyntheticAccess(t_start=T0, observation_names=("radar-composite",),
                            model_ids=("mlcast-det",))
    bare = SyntheticAccess(t_start=T0)
    for method in ("get_observation", "get_prediction"):
        args = ("radar-composite", T0) if method == "get_observation" else ("mlcast-det", T0, 10)
        a, b = getattr(plain, method)(*args), getattr(bare, method)(*args)
        assert np.array_equal(a.data, b.data)
        assert (a.unit, a.variable) == (b.unit, b.variable)


@pytest.mark.parametrize("given, mentions", [
    ({"not-a-source": {}}, "not observation names"),
    ({"radar-composite": {"uni": "dBZ"}}, "do not exist"),
])
def test_a_source_setting_that_is_wrong_is_refused_at_construction(given, mentions):
    """Not when a field is asked for and quietly comes back as the first one's."""
    with pytest.raises(ValueError, match=mentions):
        SyntheticAccess(t_start=T0, observation_names=("radar-composite",), sources=given)


def test_a_model_pointed_at_a_source_that_does_not_exist_is_refused():
    with pytest.raises(ValueError, match="not an observation"):
        SyntheticAccess(t_start=T0, observation_names=("radar-composite",),
                        model_ids=("mlcast-det",),
                        model_sources={"mlcast-det": "nope"})


def test_the_same_storm_sampled_on_two_extents_is_the_same_storm():
    """Not two storms of different sizes.

    ``sigma`` and ``drift`` are fractions of the reference domain, not of whatever
    the caller asks for. Measured against the caller's own extent, a product on a
    wider footprint would get a bigger, faster storm, and two products of one
    storm would stop agreeing -- which shows up as skill collapsing for no reason
    in the weather, the worst kind of wrong answer to go looking for.
    """
    wide = SyntheticAccess(t_start=T0, vmin=None, vmax=None,
                           obs_extent=(104.0, 24.0, 126.0, 48.0), obs_shape=(56, 72))
    narrow = SyntheticAccess(t_start=T0, vmin=None, vmax=None)

    inner = wide.get_observation("radar-composite", T0)
    outer = narrow.get_observation("radar-composite", T0)
    # the narrow grid's points all exist in the wide one
    lon_index = [int(np.argmin(np.abs(wide.obs_coords.lon - x))) for x in outer.coords.lon]
    lat_index = [int(np.argmin(np.abs(wide.obs_coords.lat - y))) for y in outer.coords.lat]
    sampled = inner.data[np.ix_(lat_index, lon_index)]
    # The two grids sample different points, and the wide one's cells are twice as
    # far apart, so half a coarse cell of gradient is the whole of the tolerance.
    # A storm that changed size or speed with the footprint would be out by 100%.
    worst = float(np.max(np.abs(sampled - outer.data)))
    assert worst < 0.12 * float(outer.data.max()), worst


def test_noise_and_bias_are_measured_in_the_truths_units():
    """A model that is 2% wrong about a 255-scale field is 2% wrong about a
    40-scale one. Added after the unit change it would be 5 of one and 5 of the
    other, which is 2% and 12%: the same number meaning two different forecasts.
    """
    access = SyntheticAccess(
        t_start=T0, vmin=None, vmax=None, truth_noise=0.0, forecast_noise=5.0,
        observation_names=("vil", "rain"), model_ids=("m",),
        sources={"rain": {"scale": 0.1, "noise": 0.0}},
        model_sources={"m": "rain"},
    )
    scaled = access.get_observation("rain", T0).data
    plain = access.get_observation("vil", T0).data
    assert np.allclose(plain * 0.1, scaled)

    # and the model's error is a fraction of the field it is wrong about, so the
    # same scenario produces the same relative spread on either scale
    here = access.get_prediction("m", T0, 10).data
    there = SyntheticAccess(t_start=T0, forecast_noise=5.0).get_prediction(
        "mlcast-det", T0, 10).data
    assert abs(float(np.std(here)) / float(np.std(there)) - 0.1) < 0.02
