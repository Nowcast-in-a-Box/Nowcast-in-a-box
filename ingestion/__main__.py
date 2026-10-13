"""Container entry for explicit SEVIR sample ingestion."""

import argparse
import json
from pathlib import Path

from ingestion.sevir import read_grid, read_window
from utils.stored_fields import create_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--event")
    parser.add_argument("--start")
    parser.add_argument("--runs")
    parser.add_argument("--id")
    parser.add_argument("--atlas-revision")
    parser.add_argument("--list-samples", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.list_samples:
        from ingestion.samples import list_samples

        print(
            json.dumps(
                list_samples(
                    args.data, args.catalog, offset=args.offset, limit=args.limit
                )
            )
        )
        return
    if not all((args.event, args.start, args.runs, args.id, args.atlas_revision)):
        parser.error(
            "ingestion requires --event, --start, --runs, --id and --atlas-revision"
        )
    window = read_window(args.data, args.start, args.event)
    grid = read_grid(args.catalog, window["event_id"])
    root = Path(args.runs)
    if not root.is_dir():
        raise FileNotFoundError("configured runs root does not exist")
    directory = create_run(
        root, args.id, window, grid, atlas_revision=args.atlas_revision
    )
    print(
        json.dumps(
            {
                "run_id": args.id,
                "directory": str(directory),
                "sample_id": window["sample_id"],
            }
        )
    )


if __name__ == "__main__":
    main()
