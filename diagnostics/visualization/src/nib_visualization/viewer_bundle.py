"""Optional numeric COG export, independent of Core's artifact manifest v1.

Rasterio is loaded only here; existing PNG/GIF consumers do not need it.
Pixel centres are explicit; axis reversal is lossless and never resamples.
"""

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from nib_visualization.artifacts import sha256_file
from nib_visualization.field import VisualizationField, validate_field


def write_numeric_cog(values, x, y, crs, path):
    """Write one regular grid as a north-up lossless tiled GeoTIFF."""
    x, y, data = np.asarray(x), np.asarray(y), np.asarray(values)
    if x[1] < x[0]:
        x, data = x[::-1], data[:, ::-1]
    if y[1] > y[0]:
        y, data = y[::-1], data[::-1]
    dx, dy = float(x[1] - x[0]), float(y[0] - y[1])
    transform = from_origin(float(x[0] - dx / 2), float(y[0] + dy / 2), dx, dy)
    # Float32/64 retain scientific values and support a NaN NoData marker.
    dtype = data.dtype if data.dtype.kind == "f" else np.dtype("float64")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="COG",
        width=x.size,
        height=y.size,
        count=1,
        dtype=dtype,
        crs=crs,
        transform=transform,
        nodata=np.nan,
        compress="DEFLATE",
        blocksize=256,
        overview_resampling="NEAREST",
    ) as dst:
        dst.write(data.astype(dtype, copy=False), 1)
    with rasterio.open(path) as src:
        return {
            "bounds": list(src.bounds),
            "crs": src.crs.to_string(),
            "shape": [src.height, src.width],
            "dtype": src.dtypes[0],
            "sha256": sha256_file(path),
            "overview_factors": src.overviews(1),
        }


def utc(value):
    return np.datetime_as_string(value, unit="s") + "Z"


def export_terrain_cog(view, path):
    """Preserve a prepared terrain view's pixel edges, independently of weather."""
    west, east, south, north = view.extent_wgs84
    rows, cols = view.values.shape
    x = west + (np.arange(cols) + 0.5) * (east - west) / cols
    y = south + (np.arange(rows) + 0.5) * (north - south) / rows
    return write_numeric_cog(view.values, x, y, "EPSG:4326", path)


def export_field_cogs(field: VisualizationField, output: Path | str) -> dict:
    """Export every actual input time; return viewer metadata, not Core manifest."""
    field = validate_field(field)
    output = Path(output)
    frames = []
    info = None
    for index in range(field.values.sizes["lead_time"]):
        filename = f"weather/frame-{index:04d}.tif"
        info = write_numeric_cog(
            field.values.isel(lead_time=index).values,
            field.values.x.values,
            field.values.y.values,
            field.crs,
            output / filename,
        )
        frame = {
            "url": filename,
            "sha256": info["sha256"],
            "lead_seconds": float(
                field.values.lead_time.values[index] / np.timedelta64(1, "s")
            ),
            "valid_time": utc(field.valid_time[index]),
        }
        if field.interval_start is not None:
            frame.update(
                interval_start=utc(field.interval_start[index]),
                interval_end=utc(field.interval_end[index]),
            )
        frames.append(frame)
    return {
        "viewer_bundle_version": 1,
        "variable": field.name,
        "units": field.units,
        "init_time": utc(field.init_time),
        "temporal_statistic": field.temporal_statistic,
        "crs": info["crs"],
        "shape": info["shape"],
        "bounds": info["bounds"],
        "extent": list(transform_bounds(info["crs"], "EPSG:4326", *info["bounds"])),
        "frames": frames,
        "overview_resampling": "nearest (display only)",
    }
