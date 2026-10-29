"""Inspect a NiB MLCast DataBundle without altering the original dispatcher.

This is a source-specific *optional QA tool*, not a second data adapter.
Data is loaded through adapters.__main__.run -> adapter_for -> ADAPTER.
Metadata from older MLCast loader versions may not include QA statistics;
we recompute them from the returned DataArray instead of printing None.

Example:
    python -m scripts.mlcast_inspect --config tests/adapters/requests/mlcast_it_dpc_sri_5min/example_success.yaml --data-root ./demo_data
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from adapters.__main__ import data_root_from, run
from adapters.mlcast_common import native_grid_spacing


def _sample_bounds(store_path, variable):
    """Optional fallback for old MLCast bundles without sample_bbox metadata.

    Uses ONLY the local Zarr store. Never downloads anything.
    """
    if not store_path or not Path(store_path).is_dir():
        return None
    import xarray as xr
    from adapters.mlcast_common import _latlon

    with xr.open_zarr(store_path, consolidated=True) as ds:
        frame = ds[variable].isel(time=0, drop=True)
        lat, lon = _latlon(frame, ds)
        mask = np.isfinite(lat) & np.isfinite(lon)
        if not mask.any():
            return None
        return [float(np.min(lon[mask])), float(np.min(lat[mask])),
                float(np.max(lon[mask])), float(np.max(lat[mask]))]


def print_mlcast_qa(bundle):
    """Inspect the real DataBundle instead of relying on optional metadata keys."""
    source = bundle.get("source", "")
    if not source.startswith("mlcast_"):
        raise ValueError("Expected a mlcast_* DataBundle, got {!r}".format(source))
    data = bundle.get("data", {})
    if not data:
        raise ValueError("MLCast DataBundle has no data frames")
    request = bundle.get("request", {})
    metadata = bundle.get("metadata", {})
    files = bundle.get("source_files", {})
    times = sorted(data)
    channels = list(data[times[0]])
    frame = data[times[0]][channels[0]]
    variable = channels[0]
    source_paths = files.get(times[0], {}).get(variable, [])
    sample_path = source_paths[0] if source_paths else metadata.get("local_store")

    requested_bbox = request.get("region", {}).get("bbox")
    if requested_bbox is None:
        requested_bbox = metadata.get("requested_bbox")
    bounds = metadata.get("sample_bbox")
    if bounds is None:
        try:
            bounds = _sample_bounds(sample_path, variable)
        except (OSError, ValueError, KeyError, ImportError) as err:
            print("Note: unable to read original sample bounds: {}".format(err))
    
    print("\n=== MLCast DataBundle QA ===")
    print("source / fields : {} / {}".format(source, channels))
    print("DataBundle keys : {}".format(list(bundle)))
    print("data_dir        : {}".format(request.get("data_dir", "unknown")))
    print("local Zarr      : {}".format(sample_path or "not recorded"))
    print("variable / units: {} / {}".format(variable, frame.attrs.get("units", metadata.get("units", "unknown"))))
    print("loaded times    : {}".format(len(times)))
    print("frame shape     : {}".format(tuple(frame.shape)))
    print("coords          : {}".format(list(frame.coords)))
    # Current loaders record native spacing in metadata. Older bundles can
    # still be inspected using the x/y coordinate arrays carried by DataArray.
    spacing = metadata.get("grid_spacing")
    resolution_m = metadata.get("spatial_resolution_m")
    if spacing is None or resolution_m is None:
        inferred_spacing, inferred_resolution = native_grid_spacing(frame)
        if spacing is None:
            spacing = inferred_spacing
        if resolution_m is None:
            resolution_m = inferred_resolution
    if spacing:
        x_step, y_step = spacing["x"], spacing["y"]
        print("native x/y step : dx={:g} {}, dy={:g} {}".format(
            x_step["step"], x_step["units"], y_step["step"], y_step["units"]))
        if any(spacing[axis].get("unit_source") == "projected_crs" for axis in ("x", "y")):
            print("grid units source: projected CRS (spatial_ref/CF grid mapping)")
    else:
        print("native x/y step : not determined (missing or nonuniform coordinates)")
    if resolution_m:
        dx, dy = resolution_m["x"], resolution_m["y"]
        if dx >= 1000 and dy >= 1000:
            print("spatial resolution: {:g} km x {:g} km (native grid spacing)".format(dx / 1000, dy / 1000))
        else:
            print("spatial resolution: {:g} m x {:g} m (native grid spacing)".format(dx, dy))
    else:
        print("spatial resolution: not verified in metres (check x/y units)")
    print("requested bbox  : {}".format(requested_bbox if requested_bbox is not None else "not recorded"))
    print("sample bounds   : {}".format(bounds if bounds is not None else "not available"))
    print("frame QA (UTC, valid %, min, max):")
    # Compute values from the actual returned frames. Older loader versions may
    # omit frame_stats in metadata, but that must not produce empty QA output.
    for stamp in times:
        for channel, array in data[stamp].items():
            vals = np.asarray(array.values)
            valid = np.isfinite(vals)
            fraction = float(valid.mean()) * 100 if vals.size else 0.0
            if valid.any():
                minimum = float(np.min(vals[valid]))
                maximum = float(np.max(vals[valid]))
                print("  {}  {}  {:.1f}%  {:.6g}  {:.6g}".format(
                    stamp, channel, fraction, minimum, maximum))
            else:
                print("  {}  {}  0.0%  N/A  N/A [INVALID]".format(stamp, channel))



def print_mlcast_all_attrs(bundle):
    """Dump all bundle metadata and xarray attrs without printing huge pixel arrays.

    "All attrs" includes the shared request, source_files, metadata, each
    returned DataArray's attrs/encoding, and each coordinate's attrs/encoding.
    Actual grids remain accessible through bundle["data"][time][channel].
    """
    from pprint import pprint

    print("\n=== MLCast DataBundle ALL ATTRIBUTES (pixel values omitted) ===")
    print("source:")
    pprint(bundle.get("source"), sort_dicts=False)
    print("request:")
    pprint(bundle.get("request"), sort_dicts=False)
    print("source_files:")
    pprint(bundle.get("source_files"), sort_dicts=False)
    print("metadata:")
    pprint(bundle.get("metadata"), sort_dicts=False)
    for time, channels in sorted(bundle.get("data", {}).items()):
        for channel, arr in channels.items():
            print("\n[frame] UTC={} channel={}".format(time, channel))
            print("dims={} shape={} dtype={}".format(arr.dims, arr.shape, arr.dtype))
            print("data attrs:")
            pprint(dict(arr.attrs), sort_dicts=False)
            print("data encoding:")
            pprint(dict(arr.encoding), sort_dicts=False)
            for name, coord in arr.coords.items():
                print("coordinate {}: dims={} shape={} dtype={}".format(
                    name, coord.dims, coord.shape, coord.dtype))
                print("  attrs:")
                pprint(dict(coord.attrs), sort_dicts=False)
                print("  encoding:")
                pprint(dict(coord.encoding), sort_dicts=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Standard NiB request YAML")
    parser.add_argument("--data-root", default=None, help="Local NiB data root")
    parser.add_argument("--all-attrs", action="store_true",
                        help="Print full DataBundle metadata and all xarray attributes (not pixel arrays)")
    args = parser.parse_args()
    bundle = run(args.config, data_root_from(args.data_root))
    print_mlcast_qa(bundle)
    if args.all_attrs:
        print_mlcast_all_attrs(bundle)


if __name__ == "__main__":
    main()
