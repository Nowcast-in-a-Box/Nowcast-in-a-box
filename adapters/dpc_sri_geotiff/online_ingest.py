"""Create one NiB run from the public DPC API, without a polling model.

This is the online mode of the DPC Data Adapter.  It downloads exactly six
verified SRI scans into its own cache, then invokes the normal local adapter
to materialise ``context.npz`` plus the original GeoTIFF files under
``inputs/dpc``.  The model container never needs network access.

It must be run in a trusted *data* container with outbound access only to the
DPC public API.  Model, converter and diagnostics containers stay offline.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from irene_nowcast.entrypoint import geotiff
from irene_nowcast.source import DpcRadarApi
from nowcast_runtime.builtin.sources import RetryPolicy
from nowcast_runtime.ports import Observation

from adapters.dpc_sri_geotiff.ingest import CADENCE_MINUTES, CONTEXT_STEPS, create, stamp, utc


def download_window(
    cache: Path, run_time: datetime | None, *, count: int = CONTEXT_STEPS
) -> tuple[Path, datetime]:
    """Fetch one complete DPC history into the API-managed cache directory."""
    product = geotiff()
    source = DpcRadarApi(
        product_type="SRI",
        cache=cache,
        retry=RetryPolicy(
            timeout_seconds=60,
            attempts=3,
            retry_delay_seconds=5,
            max_retry_delay_seconds=30,
        ),
        filename=product.filename,
        verify=product.validate,
    )
    anchor = run_time or source.latest()
    if anchor is None:
        raise RuntimeError("DPC API returned no latest SRI timestamp")
    anchor = anchor.astimezone(run_time.tzinfo if run_time else anchor.tzinfo)
    moments = [anchor - timedelta(minutes=CADENCE_MINUTES * step) for step in reversed(range(count))]
    for moment in moments:
        name = product.filename(moment)
        # The location is only provenance for DpcRadarApi.fetch; it contains no
        # pre-signed transfer credential and is never written to the NiB run.
        source.fetch(Observation(time=moment, name=name, location=f"dpc/SRI/{stamp(moment)}"))
    source.prune(timedelta(hours=24))
    return source._directory, anchor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--atlas-revision", required=True)
    parser.add_argument(
        "--run-time",
        help="Last DPC input scan in UTC; omit to use the latest API scan",
    )
    args = parser.parse_args()
    run_time = utc(args.run_time) if args.run_time else None
    source_directory, anchor = download_window(args.cache_dir, run_time)
    # create() expects the first time of the six-frame input window.
    start = anchor - timedelta(minutes=CADENCE_MINUTES * (CONTEXT_STEPS - 1))
    directory = create(source_directory, args.runs, args.id, start, args.atlas_revision)
    print(
        f"Downloaded DPC window ending {stamp(anchor)} and created "
        f"{directory}"
    )


if __name__ == "__main__":
    main()
