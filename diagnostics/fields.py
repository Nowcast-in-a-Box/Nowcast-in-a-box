"""Convert verified stored Forecasts to the visualization input contract."""

from dataclasses import replace

import numpy as np
import xarray as xr
from pyproj import Transformer

from utils.stored_fields import StoredFields
from nib_visualization.field import VisualizationField, validate_field


def display_grid(field):
    """Display-only nearest-neighbour projection; stored scoring arrays are unchanged."""
    if field.crs.is_geographic or field.crs.utm_zone:
        return field
    source_x, source_y = field.values.x.values, field.values.y.values
    xx, yy = np.meshgrid(source_x, source_y)
    forward = Transformer.from_crs(field.crs, "EPSG:4326", always_xy=True)
    lon, lat = forward.transform(xx, yy)
    if not np.isfinite(lon).all() or not np.isfinite(lat).all():
        raise ValueError("non-finite projected display coordinates")
    x = np.linspace(lon.min(), lon.max(), len(source_x))
    y = np.linspace(lat.min(), lat.max(), len(source_y))
    inverse = Transformer.from_crs("EPSG:4326", field.crs, always_xy=True)
    target_x, target_y = inverse.transform(*np.meshgrid(x, y))
    ix = np.rint((target_x - source_x[0]) / (source_x[1] - source_x[0])).astype(int)
    iy = np.rint((target_y - source_y[0]) / (source_y[1] - source_y[0])).astype(int)
    outside = (ix < 0) | (ix >= len(source_x)) | (iy < 0) | (iy >= len(source_y))
    values = field.values.values[
        :, np.clip(iy, 0, len(source_y) - 1), np.clip(ix, 0, len(source_x) - 1)
    ].copy()
    values[:, outside] = np.nan
    data = xr.DataArray(
        values,
        dims=field.values.dims,
        coords={"lead_time": field.values.lead_time, "x": x, "y": y},
        name=field.name,
        attrs={"units": field.units},
    )
    return validate_field(
        replace(
            field,
            values=data,
            crs="EPSG:4326",
            provenance={
                **field.provenance,
                "display_resampling": "nearest",
                "source_crs": field.crs.to_wkt(),
            },
        )
    )


def visualization_field(directory, model_id):
    source = StoredFields(directory)
    descriptor = source.manifest["forecasts"][model_id]
    cube = source.cube(descriptor)
    values = xr.DataArray(
        np.where(cube["mask"], np.nan, cube["values"]),
        dims=("lead_time", "y", "x"),
        coords={
            "lead_time": np.asarray(descriptor["lead_minutes"], dtype="timedelta64[m]"),
            "x": cube["x"],
            "y": cube["y"],
        },
        name=descriptor["variable"],
        attrs={"units": descriptor["unit"]},
    )
    return validate_field(
        VisualizationField(
            values=values,
            crs=descriptor["grid"]["crs"],
            init_time=np.datetime64(descriptor["init_time"].removesuffix("Z")),
            valid_time=np.asarray(
                [t.removesuffix("Z") for t in descriptor["times"]],
                dtype="datetime64[s]",
            ),
            temporal_statistic="instantaneous",
            provenance={
                "run_id": source.manifest["id"],
                "sample_id": source.manifest["sample"]["sample_id"],
                "forecast_sha256": descriptor["sha256"],
            },
        )
    )
