"""Deterministic synthetic forecast fixtures (VIS-003).

``make_fixture(grid, n_leads=12)`` builds an in-memory, fully deterministic
:class:`~nib_visualization.field.VisualizationField` that already satisfies the
VIS-002 contract (the field is returned through :func:`validate_field`) and
carries an independent analytical-position oracle for VIS-004/VIS-005: the
field is a moving Gaussian whose center is defined in closed form, never
derived back from rendered output.

Fixed definitions (single source of truth for every magic number below):

- Time: ``init_time`` 2026-09-21T00:00:00 (UTC per project contract), leads
  ``5, 10, ..., 5*n_leads`` minutes; default 12 leads.
- Grids: ``y=96`` x ``x=128`` cell centres on both grids. ``latlon`` samples
  longitudes 7.0–13.0 E and latitudes 49.0–53.0 N (EPSG:4326). ``projected``
  (EPSG:32632) linspace-transforms the forward-projected easting of
  (7.0, 51.0)/(13.0, 51.0) and northing of (10.0, 49.0)/(10.0, 53.0) into
  regular ascending UTM axes, then evaluates the same analytical field at each
  cell's inverse-transformed longitude/latitude. This projected domain is a
  fixture/test-domain construction, not a production projection-domain
  definition.
- Analytical field: dimensionless Gaussian ``exp(-0.5 * (((lon-center_lon) /
  SIGMA_LON)**2 + ((lat-center_lat) / SIGMA_LAT)**2))`` with sigma 0.45
  degrees in longitude and 0.35 degrees in latitude; the center moves linearly
  from (8.5 E, 50.0 N) to (11.5 E, 52.0 N), i.e. ``fraction = i/(n_leads-1)``
  (``fraction = 0`` when ``n_leads == 1``). Both grids sample the *same*
  geographical definition, only the sampling grid differs.
- Metadata: variable ``synthetic_scalar``, ``units = "1"``, instantaneous
  statistic (no interval arrays), provenance marking the field as synthetic.

No NaN, noise or unit conversion is injected; downstream tests derive partial
NaN / descending-y / longitude-seam variants locally from a valid fixture.
"""

from __future__ import annotations

import numpy as np
import xarray as xr
from pyproj import Transformer

from nib_visualization.field import VisualizationField, validate_field

FIXTURE_VERSION = 1
GENERATOR_NAME = "moving_gaussian"

VARIABLE_NAME = "synthetic_scalar"
VARIABLE_UNITS = "1"
TEMPORAL_STATISTIC = "instantaneous"

#: single init time shared by every fixture; UTC semantics per project contract
INIT_TIME = np.datetime64("2026-09-21T00:00:00")
#: spacing between consecutive leads; lead i is (i+1) * LEAD_STEP
LEAD_STEP = np.timedelta64(5, "m")
DEFAULT_N_LEADS = 12

#: spatial shape (y, x) of both fixture grids, in cell centres
GRID_SHAPE = (96, 128)

LATLON_CRS = "EPSG:4326"
#: longitude/latitude cell-centre endpoints of the lat/lon grid, degrees
LATLON_X_ENDPOINTS = (7.0, 13.0)
LATLON_Y_ENDPOINTS = (49.0, 53.0)

PROJECTED_CRS = "EPSG:32632"
#: (lon, lat) anchors whose forward projection fixes the regular UTM axis endpoints
PROJECTED_X_ANCHORS = ((7.0, 51.0), (13.0, 51.0))
PROJECTED_Y_ANCHORS = ((10.0, 49.0), (10.0, 53.0))

#: moving-Gaussian center at fraction 0 and fraction 1, (lon, lat) degrees
START_CENTER = (8.5, 50.0)
END_CENTER = (11.5, 52.0)
#: Gaussian scale (sigma) in longitude/latitude, degrees
SIGMA_LON = 0.45
SIGMA_LAT = 0.35


def _moving_center(lead_index: int, n_leads: int) -> tuple[float, float]:
    """Analytical center ``(lon, lat)`` of the Gaussian at ``lead_index``."""
    fraction = lead_index / (n_leads - 1) if n_leads > 1 else 0.0
    start_lon, start_lat = START_CENTER
    end_lon, end_lat = END_CENTER
    return (
        start_lon + (end_lon - start_lon) * fraction,
        start_lat + (end_lat - start_lat) * fraction,
    )


def _sample_analytical(lon: np.ndarray, lat: np.ndarray, index: int, n_leads: int) -> np.ndarray:
    center_lon, center_lat = _moving_center(index, n_leads)
    z = ((lon - center_lon) / SIGMA_LON) ** 2 + ((lat - center_lat) / SIGMA_LAT) ** 2
    return np.exp(-0.5 * z)


def _latlon_grid() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Regular lat/lon cell centres plus their 2D longitude/latitude arrays."""
    x = np.linspace(*LATLON_X_ENDPOINTS, GRID_SHAPE[1])
    y = np.linspace(*LATLON_Y_ENDPOINTS, GRID_SHAPE[0])
    lon, lat = np.meshgrid(x, y)
    return x, y, lon, lat


def _projected_grid() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Regular UTM cell centres plus each cell's longitude/latitude."""
    to_projected = Transformer.from_crs(LATLON_CRS, PROJECTED_CRS, always_xy=True)
    x_start, _ = to_projected.transform(*PROJECTED_X_ANCHORS[0])
    x_end, _ = to_projected.transform(*PROJECTED_X_ANCHORS[1])
    _, y_start = to_projected.transform(*PROJECTED_Y_ANCHORS[0])
    _, y_end = to_projected.transform(*PROJECTED_Y_ANCHORS[1])
    x = np.linspace(x_start, x_end, GRID_SHAPE[1])
    y = np.linspace(y_start, y_end, GRID_SHAPE[0])
    to_lonlat = Transformer.from_crs(PROJECTED_CRS, LATLON_CRS, always_xy=True)
    xx, yy = np.meshgrid(x, y)
    lon, lat = to_lonlat.transform(xx, yy)
    return x, y, lon, lat


def make_fixture(grid: str, n_leads: int = DEFAULT_N_LEADS) -> VisualizationField:
    """Build a deterministic synthetic forecast fixture on ``grid``.

    ``grid`` is the exact string ``"latlon"`` (EPSG:4326) or ``"projected"``
    (EPSG:32632); any other value raises ``ValueError``. ``n_leads`` must be a
    positive integer (a single lead keeps the physical ``lead_time`` axis).
    The returned field has been through :func:`validate_field`, so it carries a
    resolved CRS and derived ``valid_time`` and re-validates cleanly; repeated
    calls with equal arguments return identical fields.
    """
    if grid not in ("latlon", "projected"):
        raise ValueError(f"grid must be 'latlon' or 'projected', got {grid!r}")
    if (
        isinstance(n_leads, bool)
        or not isinstance(n_leads, (int, np.integer))
        or n_leads < 1
    ):
        raise ValueError(f"n_leads must be a positive integer, got {n_leads!r}")

    if grid == "latlon":
        x, y, lon, lat = _latlon_grid()
        crs = LATLON_CRS
    else:
        x, y, lon, lat = _projected_grid()
        crs = PROJECTED_CRS

    lead_time = np.arange(1, n_leads + 1) * LEAD_STEP
    frames = np.stack(
        [_sample_analytical(lon, lat, index, n_leads) for index in range(n_leads)]
    )
    values = xr.DataArray(
        frames,
        dims=("lead_time", "y", "x"),
        coords={"lead_time": lead_time, "y": y, "x": x},
        name=VARIABLE_NAME,
        attrs={"units": VARIABLE_UNITS},
    )
    field = VisualizationField(
        values=values,
        crs=crs,
        init_time=INIT_TIME,
        temporal_statistic=TEMPORAL_STATISTIC,
        provenance={
            "synthetic": True,
            "generator": GENERATOR_NAME,
            "grid": grid,
            "fixture_version": FIXTURE_VERSION,
        },
    )
    return validate_field(field)
