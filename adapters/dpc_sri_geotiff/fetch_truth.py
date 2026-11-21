"""Download an exact future DPC truth range for retrospective verification.

The files go only into the DPC API-managed cache.  Use ``attach_target``
afterwards to copy the model-domain fields into an existing NiB run.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

from irene_nowcast.entrypoint import geotiff
from irene_nowcast.source import DpcRadarApi
from nowcast_runtime.builtin.sources import RetryPolicy
from nowcast_runtime.ports import Observation

from adapters.dpc_sri_geotiff.ingest import CADENCE_MINUTES, stamp, utc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--start", required=True, help="First truth scan, UTC")
    parser.add_argument("--steps", type=int, default=12)
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be positive")
    start = utc(args.start)
    product = geotiff()
    source = DpcRadarApi(
        product_type="SRI",
        cache=args.cache_dir,
        retry=RetryPolicy(
            timeout_seconds=60,
            attempts=3,
            retry_delay_seconds=5,
            max_retry_delay_seconds=30,
        ),
        filename=product.filename,
        verify=product.validate,
    )
    for index in range(args.steps):
        moment = start + timedelta(minutes=CADENCE_MINUTES * index)
        source.fetch(
            Observation(
                time=moment,
                name=product.filename(moment),
                location=f"dpc/SRI/{stamp(moment)}",
            )
        )
    print(
        f"Downloaded {args.steps} DPC truth scans from {stamp(start)} through "
        f"{stamp(start + timedelta(minutes=CADENCE_MINUTES * (args.steps - 1)))} "
        f"into {source._directory}"
    )


if __name__ == "__main__":
    main()
