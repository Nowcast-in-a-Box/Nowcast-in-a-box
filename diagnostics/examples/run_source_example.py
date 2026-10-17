"""Inspect a saved run using only the four-method data handoff (no inference)."""

import argparse
import json
from pathlib import Path

import numpy as np

from diagnostics.run_source import RunSource


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="saved CMA run directory")
    parser.add_argument("--metrics", required=True, help="JSON list of metric groups")
    parser.add_argument(
        "--version", required=True, type=int, help="frozen group version"
    )
    parser.add_argument("--group-id")
    parser.add_argument("--models", nargs="+", help="saved model IDs; default: all")
    args = parser.parse_args()
    source = RunSource(
        args.run,
        metrics=json.loads(Path(args.metrics).read_text()),
        version=args.version,
        group_id=args.group_id,
        models=args.models,
    )
    group = source.get_group(source.list_groups()[0])
    print(json.dumps(group, indent=2, ensure_ascii=False))
    for member in group["forecasts"]:
        lead = member["leads_minutes"][0]
        forecast = source.read_forecast(
            member["name"], group["time"]["init_from"], lead
        )
        truth = source.read_truth(group["truth"]["name"], forecast.attrs["valid_time"])
        assert truth.dims == forecast.dims and truth.shape == forecast.shape
        assert truth.attrs["crs"] == forecast.attrs["crs"]
        assert truth.attrs["units"] == forecast.attrs["units"]
        assert isinstance(truth.values, np.ndarray)
        for coordinate in truth.coords:
            np.testing.assert_array_equal(truth[coordinate], forecast[coordinate])
        print(
            f"\n{member['display_name']} / +{lead} min / {forecast.attrs['valid_time']}"
        )
        print(truth)
        print(forecast)
    print(f"\nVerified cube loads: {source.file_reads}; no outputs written.")


if __name__ == "__main__":
    main()
