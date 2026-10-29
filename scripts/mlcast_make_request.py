"""Generate an editable NiB YAML request from an EXISTING local MLCast sample.

No internet or downloads. For the already-downloaded Italian 5-frame sample:

  python -m scripts.mlcast_make_request --dataset it_dpc_sri_5min \
      --data-root ./demo_data --history-steps 3 --shift-steps 1 --crop-fraction 0.6

A standard example_success.yaml is also saved under tests/adapters/requests/
for the same NiB CLI workflow as CMA radar and FY-4B.
Edit either YAML freely within the local sample time/region.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import xarray as xr
import yaml

from adapters.mlcast_common import _latlon, locate_store


def make_request(data_root, dataset, history_steps=3, shift_steps=0,
                 crop_fraction=0.65, output=None, sync_example=False):
    catalog = json.loads((Path(__file__).resolve().parents[1] / "adapters" / "mlcast_sources.json").read_text(encoding="utf-8"))
    if dataset not in catalog:
        raise ValueError("Unknown MLCast dataset: {}".format(dataset))
    info = catalog[dataset]
    if history_steps < 1 or shift_steps < 0:
        raise ValueError("history_steps must be >=1, shift_steps must be >=0")
    if not (0 < crop_fraction <= 1):
        raise ValueError("crop_fraction must be in (0,1]")
    root = Path(data_root).resolve()
    folder = root / "mlcast" / dataset
    store = locate_store(folder)
    ds = xr.open_zarr(store, consolidated=True)
    try:
        variable = info["variable"]
        if variable not in ds:
            raise ValueError("Variable {} missing in {}".format(variable, store))
        timevals = np.asarray(ds.time.values).astype("datetime64[ns]")
        if len(timevals) < history_steps + shift_steps:
            raise ValueError("Only {} stored frames, but shift_steps + history_steps = {}".format(
                len(timevals), history_steps + shift_steps))
        target_index = len(timevals) - shift_steps - 1
        target = timevals[target_index]
        step = np.timedelta64(int(info["cadence_minutes"]), "m")
        expected = target - np.arange(history_steps - 1, -1, -1) * step
        if not np.isin(expected, timevals).all():
            raise ValueError("Chosen timestamps include missing frames; reduce shift or choose another time")
        frame = ds[variable].isel(time=0, drop=True)
        lat, lon = _latlon(frame, ds)
        finite = np.isfinite(lat) & np.isfinite(lon)
        if not finite.any():
            raise ValueError("No valid sample geolocation")
        west, east = float(lon[finite].min()), float(lon[finite].max())
        south, north = float(lat[finite].min()), float(lat[finite].max())
        scale = (1 - crop_fraction) / 2
        bbox = [west + (east-west)*scale, south+(north-south)*scale,
                east-(east-west)*scale, north-(north-south)*scale]
        # Always store strict valid interval, remove trailing fractional seconds.
        target_text = str(np.datetime_as_string(target, unit="s")) + "Z"
        req = {
            "source": info["source"], "data_dir": "mlcast/{}".format(dataset),
            "initial_time": target_text,
            "history_steps": history_steps,
            "interval_minutes": int(info["cadence_minutes"]),
            "channels": [variable], "region": {"bbox": bbox},
        }
        dest = Path(output).resolve() if output else folder / "request_custom.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = yaml.safe_dump(req, sort_keys=False, allow_unicode=True)
        dest.write_text(content, encoding="utf-8")
        print("Local YAML:", dest)
        if sync_example:
            example = (Path(__file__).resolve().parents[1] / "tests" / "adapters" /
                       "requests" / info["source"] / "example_success.yaml")
            example.parent.mkdir(parents=True, exist_ok=True)
            example.write_text(content, encoding="utf-8")
            print("NiB example YAML:", example)
            print('Example run: python -m adapters --config "{}" --data-root "{}"'.format(example, root))
        print("Time:", expected[0], "...", target, "; bbox:", [round(x, 5) for x in bbox])
        print("Run: python -m adapters --config \"{}\" --data-root \"{}\"".format(dest, root))
        return dest
    finally:
        ds.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", default="it_dpc_sri_5min")
    p.add_argument("--data-root", default="./demo_data")
    p.add_argument("--history-steps", type=int, default=3)
    p.add_argument("--shift-steps", type=int, default=1,
                   help="Frames to move back from newest stored time")
    p.add_argument("--crop-fraction", type=float, default=0.65)
    p.add_argument("--output", default=None)
    p.add_argument("--no-sync-example", action="store_true",
                   help="Do not also create tests/adapters/requests/<source>/example_success.yaml")
    a = p.parse_args()
    make_request(a.data_root, a.dataset, a.history_steps, a.shift_steps,
                 a.crop_fraction, a.output, sync_example=not a.no_sync_example)


if __name__ == "__main__":
    main()
