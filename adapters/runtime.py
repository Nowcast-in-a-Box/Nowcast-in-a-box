"""Run a local data adapter and persist selected observations for a model."""

import argparse
import json
from pathlib import Path

from adapters.__main__ import adapter_for
from interfaces.data import RuntimeDataAdapter
from utils.stored_fields import create_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--event")
    parser.add_argument("--start")
    parser.add_argument("--center-lon", type=float)
    parser.add_argument("--center-lat", type=float)
    parser.add_argument("--runs", type=Path)
    parser.add_argument("--id")
    parser.add_argument("--atlas-revision")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list-samples", action="store_true")
    mode.add_argument("--check-window", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--day")
    args = parser.parse_args()

    adapter = adapter_for(args.dataset)
    if not isinstance(adapter, RuntimeDataAdapter):
        parser.error(f"{args.dataset} does not provide runtime windows")
    if args.list_samples:
        result = adapter.list_samples(
            args.data,
            catalog=args.catalog,
            offset=args.offset,
            limit=args.limit,
            day=args.day,
        )
    else:
        if not args.event or not args.start:
            parser.error("selected windows require --event and --start")
        if args.check_window:
            result = adapter.check_window(
                args.data, args.event, args.start, catalog=args.catalog
            )
        else:
            if not all((args.runs, args.id, args.atlas_revision)):
                parser.error(
                    "run preparation requires --runs, --id and --atlas-revision"
                )
            if not args.runs.is_dir():
                raise FileNotFoundError("configured runs root does not exist")
            window, grid = adapter.read_window(
                args.data,
                args.event,
                args.start,
                catalog=args.catalog,
                center_lon=args.center_lon,
                center_lat=args.center_lat,
            )
            directory = create_run(
                args.runs, args.id, window, grid, atlas_revision=args.atlas_revision
            )
            result = {
                "run_id": args.id,
                "directory": str(directory),
                "sample_id": window["sample_id"],
            }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
