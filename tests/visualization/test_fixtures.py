"""VIS-003 synthetic fixture tests: contract, reproducibility, position oracle.

Behavior groups (see
doc_log/plans/visualization-mvp-tasks/2026-09-21-vis-003-synthetic-fixtures.md):

- S1 valid fixture contract: both grids at default 12 leads plus a singleton
  ``n_leads=1`` field, contract metadata, grid coordinates, domain containment
  and clean ``ValueError`` for unknown grids / non-positive or non-integer leads
- S2 reproducibility: identical fields from repeated identical calls, returned as
  fully isolated objects (no shared identity/memory; mutating the first never
  leaks into the second) — VIS-003 review F-002 closure
- S3 independent analytical-position oracle: the max-value cell of first/mid/last
  frames tracks the moving Gaussian center computed from the fixed formula, and
  non-peak cell values match the independently restated Gaussian formula —
  VIS-003 review F-001 closure

Every expected center, lead sequence and grid geometry is recomputed in this
file from the fixed task-card definitions; nothing is derived from the
generator's own helpers, from an argmax round-trip or from renderer output.
The projected-grid oracle builds its own ``always_xy=True`` transformer here.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from pyproj import CRS, Transformer

from nib_visualization import make_fixture
from nib_visualization.field import validate_field

# Fixed fixture contract, restated independently of the implementation.
START_LON, START_LAT = 8.5, 50.0
END_LON, END_LAT = 11.5, 52.0
SIGMA_LON, SIGMA_LAT = 0.45, 0.35
INIT_TIME = np.datetime64("2026-09-21T00:00:00")
LEAD_STEP = np.timedelta64(5, "m")
DEFAULT_N_LEADS = 12
N_Y, N_X = 96, 128
LATLON_X_ENDPOINTS = (7.0, 13.0)
LATLON_Y_ENDPOINTS = (49.0, 53.0)


def expected_center(lead_index: int, n_leads: int) -> tuple[float, float]:
    """Analytical moving-Gaussian center ``(lon, lat)`` from the fixed formula."""
    fraction = lead_index / (n_leads - 1) if n_leads > 1 else 0.0
    return (
        START_LON + (END_LON - START_LON) * fraction,
        START_LAT + (END_LAT - START_LAT) * fraction,
    )


def expected_leads(n_leads: int) -> np.ndarray:
    """Lead times ``5, 10, ..., 5*n_leads`` minutes."""
    return np.arange(1, n_leads + 1) * LEAD_STEP


def peak_cell(values: xr.DataArray, lead_index: int) -> tuple[float, float, int, int]:
    """Coordinate ``(y, x)`` and index ``(iy, ix)`` of a frame's max-value cell."""
    frame = values.isel(lead_time=lead_index)
    iy, ix = np.unravel_index(np.argmax(frame.values), frame.values.shape)
    return float(frame.coords["y"][iy]), float(frame.coords["x"][ix]), iy, ix


def cell_diagonal(values: xr.DataArray) -> float:
    """Length of one grid-cell diagonal from the regular x/y cell centres."""
    x = values.coords["x"].values
    y = values.coords["y"].values
    dx = (x[-1] - x[0]) / (x.size - 1)
    dy = (y[-1] - y[0]) / (y.size - 1)
    return float(np.hypot(abs(dx), abs(dy)))


def expected_position(grid: str, lon: float, lat: float) -> tuple[float, float]:
    """Expected center expressed in the fixture grid's own ``(x, y)`` axes."""
    if grid == "latlon":
        return lon, lat
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True)
    easting, northing = to_utm.transform(lon, lat)
    return easting, northing


# ---------------------------------------------------------------------------
# S1 — valid fixture contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grid, epsg", [("latlon", 4326), ("projected", 32632)])
def test_s1_default_fixture_satisfies_field_contract(grid, epsg):
    field = make_fixture(grid)
    revalidated = validate_field(field)  # the returned field re-validates cleanly

    values = field.values
    assert values.dims == ("lead_time", "y", "x")
    assert values.shape == (DEFAULT_N_LEADS, N_Y, N_X)
    assert values.name == "synthetic_scalar"
    assert values.attrs["units"] == "1"
    assert set(values.coords) == {"lead_time", "y", "x"}  # no duplicate 2D lon/lat coords
    assert field.crs == CRS.from_epsg(epsg)
    assert field.init_time == INIT_TIME
    assert field.temporal_statistic == "instantaneous"
    assert field.interval_start is None
    assert field.interval_end is None
    np.testing.assert_array_equal(
        values.coords["lead_time"].values, expected_leads(DEFAULT_N_LEADS)
    )
    np.testing.assert_array_equal(field.valid_time, INIT_TIME + expected_leads(DEFAULT_N_LEADS))
    assert field.provenance == {
        "synthetic": True,
        "generator": "moving_gaussian",
        "grid": grid,
        "fixture_version": 1,
    }
    assert np.isfinite(values.values).all()
    assert revalidated.valid_time.shape == (DEFAULT_N_LEADS,)


def test_s1_latlon_grid_coordinates():
    values = make_fixture("latlon").values
    np.testing.assert_allclose(
        values.coords["x"].values, np.linspace(*LATLON_X_ENDPOINTS, N_X), rtol=0, atol=1e-12
    )
    np.testing.assert_allclose(
        values.coords["y"].values, np.linspace(*LATLON_Y_ENDPOINTS, N_Y), rtol=0, atol=1e-12
    )
    assert values.coords["x"].values[0] == LATLON_X_ENDPOINTS[0]  # endpoints included
    assert values.coords["x"].values[-1] == LATLON_X_ENDPOINTS[1]
    assert values.coords["y"].values[0] == LATLON_Y_ENDPOINTS[0]
    assert values.coords["y"].values[-1] == LATLON_Y_ENDPOINTS[1]


def test_s1_projected_grid_coordinates():
    values = make_fixture("projected").values
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True)
    x_start, _ = to_utm.transform(7.0, 51.0)
    x_end, _ = to_utm.transform(13.0, 51.0)
    _, y_start = to_utm.transform(10.0, 49.0)
    _, y_end = to_utm.transform(10.0, 53.0)
    x = values.coords["x"].values
    y = values.coords["y"].values
    assert x.size == N_X
    assert y.size == N_Y
    np.testing.assert_allclose([x[0], x[-1]], [x_start, x_end], rtol=0, atol=1e-6)
    np.testing.assert_allclose([y[0], y[-1]], [y_start, y_end], rtol=0, atol=1e-6)
    assert np.all(np.diff(x) > 0)  # ascending regular cell centres
    assert np.all(np.diff(y) > 0)


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_s1_center_path_stays_inside_domain(grid):
    values = make_fixture(grid).values
    x = values.coords["x"].values
    y = values.coords["y"].values
    for lead_index in (0, DEFAULT_N_LEADS - 1):
        lon, lat = expected_center(lead_index, DEFAULT_N_LEADS)
        cx, cy = expected_position(grid, lon, lat)
        assert x[0] < cx < x[-1], (grid, lead_index)
        assert y[0] < cy < y[-1], (grid, lead_index)


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_s1_singleton_keeps_lead_dimension_and_uses_start_center(grid):
    field = make_fixture(grid, n_leads=1)
    values = field.values
    assert values.dims == ("lead_time", "y", "x")
    assert values.shape == (1, N_Y, N_X)
    np.testing.assert_array_equal(values.coords["lead_time"].values, expected_leads(1))
    np.testing.assert_array_equal(field.valid_time, INIT_TIME + expected_leads(1))
    lon, lat = expected_center(0, 1)  # fraction 0.0: the singleton uses the start
    ex, ey = expected_position(grid, lon, lat)
    cy, cx, _, _ = peak_cell(values, 0)
    assert np.hypot(cx - ex, cy - ey) <= cell_diagonal(values)


@pytest.mark.parametrize(
    ("grid", "n_leads"),
    [
        ("mercator", 12),  # unknown grid name
        ("LATLON", 12),  # exact strings only
        ("", 12),
        ("latlon", 0),  # non-positive lead count
        ("latlon", -3),
        ("latlon", 2.5),  # non-integer
        ("latlon", "12"),
        ("latlon", True),  # bool is not a count
    ],
)
def test_s1_invalid_arguments_raise_value_error(grid, n_leads):
    with pytest.raises(ValueError):
        make_fixture(grid, n_leads=n_leads)


# ---------------------------------------------------------------------------
# S2 — reproducibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_s2_repeated_generation_is_identical(grid):
    first = make_fixture(grid)
    second = make_fixture(grid)
    xr.testing.assert_identical(first.values, second.values)  # values/dims/coords/attrs/name
    assert first.crs == second.crs
    assert first.init_time == second.init_time
    assert first.temporal_statistic == second.temporal_statistic
    np.testing.assert_array_equal(first.valid_time, second.valid_time)
    assert first.interval_start is None and second.interval_start is None
    assert first.interval_end is None and second.interval_end is None
    assert first.provenance == second.provenance


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_s2_repeated_generation_returns_isolated_objects(grid):
    """Two equal-argument calls yield independent objects, not a shared one.

    VIS-003 independent review F-002: identity/equality alone would stay green
    if ``make_fixture`` cached and returned the same ``VisualizationField``.
    """
    first = make_fixture(grid)
    second = make_fixture(grid)

    assert first is not second
    assert first.values is not second.values
    assert not np.shares_memory(first.values.values, second.values.values)
    for coord in ("lead_time", "y", "x"):
        assert not np.shares_memory(
            first.values.coords[coord].values, second.values.coords[coord].values
        )
    assert not np.shares_memory(first.valid_time, second.valid_time)
    assert first.values.attrs is not second.values.attrs
    assert first.provenance is not second.provenance

    second_values = second.values.values.copy()
    second_x = second.values.coords["x"].values.copy()
    second_valid_time = second.valid_time.copy()

    # coord arrays are deliberately read-only views in xarray, so the mutable
    # surfaces are the data array, the attrs mapping and the provenance mapping
    first.values.values[0, 0, 0] = -999.0
    first.values.attrs["units"] = "tampered"
    first.provenance["synthetic"] = False

    np.testing.assert_array_equal(second.values.values, second_values)
    np.testing.assert_array_equal(second.values.coords["x"].values, second_x)
    np.testing.assert_array_equal(second.valid_time, second_valid_time)
    assert second.values.attrs["units"] == "1"
    assert second.provenance["synthetic"] is True


# ---------------------------------------------------------------------------
# S3 — independent analytical-position oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grid", ["latlon", "projected"])
@pytest.mark.parametrize("lead_index", [0, 6, DEFAULT_N_LEADS - 1])
def test_s3_peak_cell_tracks_analytical_center(grid, lead_index):
    values = make_fixture(grid).values
    lon, lat = expected_center(lead_index, DEFAULT_N_LEADS)
    ex, ey = expected_position(grid, lon, lat)
    cy, cx, _, _ = peak_cell(values, lead_index)
    assert np.hypot(cx - ex, cy - ey) <= cell_diagonal(values)


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_s3_peak_moves_with_lead_not_one_repeated_frame(grid):
    values = make_fixture(grid).values
    cells = [peak_cell(values, i)[2:] for i in (0, 6, DEFAULT_N_LEADS - 1)]
    assert len(set(cells)) == 3  # first/mid/last peaks are distinct cells
    assert cells[0][1] < cells[1][1] < cells[2][1]  # x index grows with the center
    assert cells[0][0] < cells[2][0]  # y index grows with the center


def expected_gaussian(lon: float, lat: float, lead_index: int, n_leads: int) -> float:
    """Analytical field value from the restated formula, sigma and centers."""
    center_lon, center_lat = expected_center(lead_index, n_leads)
    z = ((lon - center_lon) / SIGMA_LON) ** 2 + ((lat - center_lat) / SIGMA_LAT) ** 2
    return float(np.exp(-0.5 * z))


@pytest.mark.parametrize("grid", ["latlon", "projected"])
@pytest.mark.parametrize("lead_index", [0, 6, DEFAULT_N_LEADS - 1])
def test_s3_non_peak_cells_match_analytical_gaussian(grid, lead_index):
    """VIS-003 independent review F-001: constrain non-peak values too.

    Peak position alone stays green under a wrong sigma or any other
    same-centre hill; corner and mid-domain cells pin the full formula with
    the sigma restated here, never imported from the generator.
    """
    values = make_fixture(grid).values
    x = values.coords["x"].values
    y = values.coords["y"].values
    to_lonlat = Transformer.from_crs("EPSG:32632", "EPSG:4326", always_xy=True)

    samples = [
        (0, 0),  # four corners: true non-peak cells far from the moving centre
        (0, N_X - 1),
        (N_Y - 1, 0),
        (N_Y - 1, N_X - 1),
        (N_Y // 2, N_X // 2),  # fixed mid-domain cell, never the peak
    ]
    for iy, ix in samples:
        if grid == "latlon":
            lon, lat = float(x[ix]), float(y[iy])
        else:
            lon, lat = to_lonlat.transform(float(x[ix]), float(y[iy]))
        expected = expected_gaussian(lon, lat, lead_index, DEFAULT_N_LEADS)
        actual = float(values.isel(lead_time=lead_index, y=iy, x=ix))
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-12), (grid, lead_index, iy, ix)
        assert actual < 1.0  # sampled cells are genuinely off-peak
