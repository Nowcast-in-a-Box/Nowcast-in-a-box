"""Download a SMALL MLCast radar Zarr excerpt outside the NiB adapter.

Usage (online machine):
    pip install -r scripts/mlcast_py312_requirements.txt
    python scripts/download_mlcast_sample.py --list
    python scripts/download_mlcast_sample.py --dataset it_dpc_sri_5min \
        --output-root ./demo_data --history-steps 5 --size 256

The NiB Data Adapter does NOT import or execute this script and makes no network requests.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import shutil
import uuid
from pathlib import Path

import numpy as np
import xarray as xr
import yaml

ENDPOINT = "https://object-store.os-api.cci2.ecmwf.int"
# Both ``python scripts/download_mlcast_sample.py`` and ``python -m scripts.download_mlcast_sample``
# must work when invoked from the NiB repository root.  A directly executed script
# normally has only scripts/ on sys.path, not its parent directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SOURCES_PATH = PROJECT_ROOT / "adapters" / "mlcast_sources.json"


def parse_utc(s):
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


def get_source_catalog():
    return json.loads(SOURCES_PATH.read_text(encoding="utf-8"))


def select_contiguous_times(available, cadence_minutes, count, preferred):
    """Find a real, gap-free UTC window nearest the preferred end time.

    This reads only the Zarr time coordinate, not the full radar archive.
    Explicit --initial-time stays strict; --auto-time uses this helper.
    """
    timestamps = np.asarray(available).astype("datetime64[ns]")
    if timestamps.ndim != 1 or len(timestamps) < count:
        raise ValueError("No {} observations available in the remote time coordinate".format(count))
    if not np.all(timestamps[1:] > timestamps[:-1]):
        raise ValueError("Remote time coordinate is not strictly increasing")
    desired_step = np.timedelta64(cadence_minutes, "m")
    breaks = np.flatnonzero(np.diff(timestamps) != desired_step) + 1
    starts = np.r_[0, breaks]
    stops = np.r_[breaks, len(timestamps)]
    candidate_ends = []
    preferred_ns = np.datetime64(preferred.replace(tzinfo=None), "ns")
    for start, stop in zip(starts, stops):
        if stop - start < count:
            continue
        # Search within this continuous run, allowing the preferred time
        # to be missing or outside the available interval.
        i = int(np.searchsorted(timestamps[start + count - 1:stop], preferred_ns))
        for local in (i - 1, i):
            idx = start + count - 1 + local
            if start + count - 1 <= idx < stop:
                candidate_ends.append(idx)
    if not candidate_ends:
        raise ValueError("No {} consecutive frames at {}-minute spacing in upstream data".format(
            count, cadence_minutes))
    idx = min(candidate_ends, key=lambda i: abs(int((timestamps[i] - preferred_ns) / np.timedelta64(1, "ns"))))
    end = timestamps[idx]
    result = timestamps[idx-count+1:idx+1]
    if len(result) != count or (len(result) > 1 and
            not np.all(np.diff(result) == desired_step)):
        raise RuntimeError("Internal contiguous time-selection failure")
    return result



def _prepare_temporal_attrs_for_zarr_v2(dataset):
    """Prevent CF time-encoding clashes when writing a decoded remote dataset.

    Xarray serializes datetime64 / timedelta64 coordinates by generating a CF
    ``units`` attribute (and ``calendar`` for datetimes).  Some upstream Zarr
    stores also expose those values in ``attrs`` after decoding.  In that case
    Xarray refuses to overwrite the duplicate key at ``to_zarr()`` time.

    Preserve the original temporal attrs in dataset provenance, remove only
    conflicting keys from decoded time-like variables, and leave physical
    units on rainfall/reflectivity variables entirely untouched.
    """
    original = {}
    for name, variable in dataset.variables.items():
        kind = np.dtype(variable.dtype).kind
        if kind not in ("M", "m"):
            continue
        keys = ("units", "calendar") if kind == "M" else ("units",)
        removed = {}
        for key in keys:
            if key in variable.attrs:
                removed[key] = str(variable.attrs.pop(key))
        if removed:
            original[str(name)] = removed
    if original:
        dataset.attrs["nib_source_temporal_attrs"] = json.dumps(
            original, ensure_ascii=False, sort_keys=True
        )
    return dataset

def main(argv=None):
    if not ((3, 8) <= sys.version_info[:2] <= (3, 12)):
        raise SystemExit("MLCast compatibility requirements currently target Python 3.8-3.12; "
                         "check Python version and dependencies before running.")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true", help="List all seven catalog entries")
    p.add_argument("--dataset", choices=list(get_source_catalog()))
    p.add_argument("--output-root", default="demo_data")
    p.add_argument("--initial-time", default=None, help="UTC ISO-8601 last observed frame (strict when set)")
    p.add_argument("--auto-time", action="store_true",
                   help="Choose a gap-free window near the catalog example time if it is missing; "
                        "also works with --initial-time as the preferred approximate time")
    p.add_argument("--history-steps", type=int, default=5,
                   help="Number of frames requested by the generated NiB YAML (default 5)")
    p.add_argument("--download-steps", type=int, default=25,
                   help="Number of consecutive frames saved locally (default 25, about 2 hours at 5 min cadence)")
    p.add_argument("--size", type=int, default=256, help="Spatial crop width/height in pixels")
    p.add_argument("--x-start", type=int, default=None, help="First x index; default centered")
    p.add_argument("--y-start", type=int, default=None, help="First y index; default centered")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args(argv)
    catalog = get_source_catalog()
    if args.list:
        for item_id, info in catalog.items():
            print(f"{item_id:27} {info['variable']:16} {info['cadence_minutes']:>2} min")
        return
    if not args.dataset:
        p.error("Specify --dataset or --list")
    if args.history_steps < 1 or args.download_steps < args.history_steps or args.size < 1:
        p.error("Require --download-steps >= --history-steps >= 1 and --size >= 1")

    try:
        import s3fs
    except ImportError as exc:
        raise SystemExit("Install downloader dependencies first: pip install -r scripts/mlcast_py312_requirements.txt") from exc
    entry = catalog[args.dataset]
    outroot = Path(args.output_root).resolve()
    folder = outroot / "mlcast" / args.dataset
    store = folder / "sample.zarr"
    req_path = folder / "request.yaml"
    # Do not download chunks if they would only overwrite an existing sample.
    if store.exists() and not args.overwrite:
        raise SystemExit(f"Already downloaded: {store}. "
                         "Use --overwrite to replace, or test it with the existing request.yaml.")
    preferred = parse_utc(args.initial_time or entry["example_initial_time"])
    cadence = entry["cadence_minutes"]

    fs = s3fs.S3FileSystem(anon=True, client_kwargs={"endpoint_url": ENDPOINT})
    print(f"Opening catalog Zarr metadata: {entry['s3_path']}")
    # None accepts consolidated or non-consolidated V2/V3 metadata as available.
    ds = xr.open_zarr(fs.get_mapper(entry["s3_path"]), consolidated=None)
    try:
        var = entry["variable"]
        if var not in ds:
            raise ValueError(f"Variable {var!r} not found; available: {list(ds.data_vars)}")
        actual_times = np.asarray(ds.time.values).astype("datetime64[ns]")
        if args.auto_time:
            required = select_contiguous_times(actual_times, cadence, args.download_steps, preferred)
            print("Selected a continuous {}-frame window: {} -> {}".format(
                len(required), str(required[0]), str(required[-1])))
        else:
            times = [preferred - dt.timedelta(minutes=i * cadence)
                     for i in range(args.download_steps - 1, -1, -1)]
            required = np.array([np.datetime64(t.replace(tzinfo=None), "ns") for t in times])
            absent = required[~np.isin(required, actual_times)]
            if len(absent):
                raise ValueError(f"Requested timestamps missing upstream: {absent.tolist()}. "
                                 "Try --auto-time to choose a complete historic window.")
        initial = dt.datetime.fromtimestamp(
            int(required[-1].astype("datetime64[s]").astype("int64")), tz=dt.timezone.utc)
        field = ds[var]
        spatial = [dim for dim in field.dims if dim != "time"]
        if len(spatial) != 2:
            raise ValueError(f"Expected two spatial dimensions: {field.dims}")
        yd, xd = spatial
        h, w = int(field.sizes[yd]), int(field.sizes[xd])
        if args.size > min(h, w):
            raise ValueError(f"Requested crop size {args.size} exceeds source dimensions {(h, w)}")
        y0 = (h - args.size) // 2 if args.y_start is None else args.y_start
        x0 = (w - args.size) // 2 if args.x_start is None else args.x_start
        if not (0 <= y0 <= h - args.size and 0 <= x0 <= w - args.size):
            raise ValueError(f"Crop origin ({y0}, {x0}) is outside source grid {(h, w)}")
        sel = {yd: slice(y0, y0 + args.size), xd: slice(x0, x0 + args.size)}
        # Explicit times prevent accidentally including gaps or far larger data volumes.
        sample_field = field.sel(time=required).isel(sel)
        sample_ds = sample_field.to_dataset(name=var)
        # Preserve CF geographic coordinates even when they are data variables, not coordinates.
        for geo in ("lat", "lon", "latitude", "longitude"):
            if geo in ds and geo not in sample_ds:
                component = ds[geo]
                component_sel = {d: sl for d, sl in sel.items() if d in component.dims}
                if component_sel:
                    sample_ds[geo] = component.isel(component_sel)
        mapping = field.attrs.get("grid_mapping") or field.encoding.get("grid_mapping")
        if mapping and mapping in ds:
            sample_ds[mapping] = ds[mapping]
        sample_ds.attrs.update(ds.attrs)
        sample_ds.attrs.update({
            "nib_sample_source": args.dataset,
            "nib_sample_spatial_origin": [int(y0), int(x0)],
            "nib_sample_source_url": f"s3://{entry['s3_path']}",
            "nib_download_steps": int(args.download_steps),
        })
        sample_ds = sample_ds.compute()  # materialize only the small requested window
        values = np.asarray(sample_ds[var].values)
        valid_fraction = float(np.isfinite(values).mean())
        if valid_fraction <= 0:
            raise ValueError("Downloaded patch contains only NaNs. Change --initial-time / --x-start / --y-start")

        # Derive and verify geolocation BEFORE writing local files.  Otherwise a
        # failed import/CRS check can leave sample.zarr without request.yaml.
        from adapters.mlcast_common import _latlon
        frame = sample_ds[var].isel(time=0, drop=True)
        lat, lon = _latlon(frame, sample_ds)
        good = np.isfinite(lat) & np.isfinite(lon)
        if not good.any():
            raise ValueError("No finite geolocation; cannot generate valid region.bbox")
        bbox = [float(np.nanmin(lon[good])), float(np.nanmin(lat[good])),
                float(np.nanmax(lon[good])), float(np.nanmax(lat[good]))]
        request = {
            "source": entry["source"],
            "data_dir": f"mlcast/{args.dataset}",
            "initial_time": initial.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "history_steps": args.history_steps,
            "interval_minutes": cadence,
            "channels": [var],
            "region": {"bbox": bbox},
        }
        folder.mkdir(parents=True, exist_ok=True)
        # Write into a temporary Zarr directory first, then publish it.  An
        # interrupted download must not look like a complete local sample.
        suffix = uuid.uuid4().hex
        tmp_store = folder / (".sample-" + suffix + ".zarr")
        tmp_request = folder / (".request-" + suffix + ".yaml")
        try:
            # The NiB local interface consumes a stable consolidated Zarr V2 sample.
            # Do not copy V3-specific compressors, chunk layouts or store options.
            # Decoded values and variable attributes (units, coords, CRS) remain.
            if mapping and mapping in sample_ds:
                sample_ds[var].attrs.setdefault("grid_mapping", mapping)
            for variable in sample_ds.variables.values():
                variable.encoding = {}
            # French MF data can retain CF time "units" in attrs. After
            # decoding, Xarray must generate those units itself at write-time;
            # remove ONLY conflicting time-coordinate attrs (keep RR units).
            _prepare_temporal_attrs_for_zarr_v2(sample_ds)
            sample_ds.to_zarr(tmp_store, mode="w", consolidated=True, zarr_format=2)
            # Materialize the config before publishing the sample.
            tmp_request.write_text(
                yaml.safe_dump(request, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            if store.exists():
                if not args.overwrite:
                    raise FileExistsError(f"{store} exists; use --overwrite to replace")
                shutil.rmtree(store)
            tmp_store.replace(store)
            tmp_request.replace(req_path)
        finally:
            if tmp_store.exists():
                shutil.rmtree(tmp_store)
            if tmp_request.exists():
                tmp_request.unlink()
        print(f"Saved: {store}")
        print(f"Request: {req_path}")
        print(f"Downloaded frames: {args.download_steps}; default YAML history: {args.history_steps}")
        print(f"Frame shape: {sample_ds[var].shape}; valid pixel fraction: {valid_fraction:.4f}")
        print("Edit request.yaml to choose ANY time/bbox fully contained in this local sample.")
        print(f"Run from NiB root: python -m adapters --config '{req_path}' --data-root '{outroot}'")
    finally:
        ds.close()


if __name__ == "__main__":
    main()
