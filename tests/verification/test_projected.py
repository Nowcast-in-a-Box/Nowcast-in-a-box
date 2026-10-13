import numpy as np
import pytest

from nib_verify.access.base import Coords, Field, ProjectedCoords
from nib_verify.config.schema import Ruler
from nib_verify.core.regrid import regrid, target_coords


def test_projected_identical_grid_preserves_values_and_mask():
    coords = ProjectedCoords(np.arange(4) * 3000.0, np.arange(3) * 3000.0, "EPSG:3857")
    values = np.arange(12.0).reshape(3, 4)
    mask = values == 5
    field = Field(values, coords, None, "raw", "vil", "sevir", mask)
    target = target_coords(coords.extent, 3000, coords.crs)
    aligned = regrid(field, target)
    np.testing.assert_array_equal(aligned.data, values)
    np.testing.assert_array_equal(aligned.mask, mask)
    with pytest.raises(ValueError, match="cross-CRS"):
        regrid(field, Coords(np.arange(4), np.arange(3)))
    with pytest.raises(ValueError, match="identical native"):
        regrid(field, target_coords(coords.extent, 1500, coords.crs))


def test_geographic_fingerprint_stable_and_projected_distinct():
    ruler = Ruler("sevir", "raw", 5, (0.0, 0.0, 3.0, 3.0), 1.0)
    import hashlib, json

    payload = {
        "ground_truth": "sevir",
        "unit": "raw",
        "time_step_minutes": 5,
        "extent": [0.0, 0.0, 3.0, 3.0],
        "resolution": 1.0,
    }
    expected = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[
        :16
    ]
    assert ruler.fingerprint == expected
    assert (
        Ruler("sevir", "raw", 5, ruler.extent, 1.0, "EPSG:3857").fingerprint != expected
    )
