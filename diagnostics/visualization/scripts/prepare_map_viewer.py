"""Prepare the shared offline global basemap. Does not read weather fields.

All inputs are explicit. Missing arguments fail; there is no data-directory
fallback and no network download. Source records in basemap_sources/ document
the pinned inputs; they are not implicit paths.
"""

import argparse
from pathlib import Path

from nib_visualization.viewer_basemap import prepare_basemap_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wmo-cache",
        type=Path,
        required=True,
        help="local WMO tile cache root (acquisition.json and VectorTileServer)",
    )
    parser.add_argument(
        "--terrain", type=Path, required=True, help="local ETOPO NetCDF"
    )
    parser.add_argument("--terrain-source-record", type=Path, required=True)
    parser.add_argument(
        "--terrain-sha256", required=True, help="64 lowercase hex digits"
    )
    parser.add_argument(
        "--basemap-output",
        type=Path,
        required=True,
        help="directory to publish, typically <wmo-cache>/viewer",
    )
    parser.add_argument("--terrain-width", type=int, default=7200)
    args = parser.parse_args(argv)
    bundle = prepare_basemap_bundle(
        args.wmo_cache,
        args.terrain,
        args.terrain_source_record,
        args.terrain_sha256,
        args.basemap_output,
        terrain_width=args.terrain_width,
    )
    print(
        f"Global basemap ready at {args.basemap_output} "
        f"({bundle['terrain']['shape'][1]}×{bundle['terrain']['shape'][0]})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
