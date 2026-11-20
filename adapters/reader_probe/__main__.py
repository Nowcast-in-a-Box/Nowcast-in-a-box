"""Read one local sample through a data adapter."""

import argparse
import json
import sys
from pathlib import Path

from adapters.__main__ import adapter_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--data", required=True, type=Path)
    args = parser.parse_args()
    try:
        adapter = adapter_for(args.dataset)
        print(json.dumps(adapter.inspect_sample(args.data.absolute()), allow_nan=False))
    except Exception as exc:
        print(f"Inspect failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
