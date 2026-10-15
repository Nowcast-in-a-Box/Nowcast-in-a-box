"""Write the atlas verification metric declaration from the registry."""

import json
import sys
from pathlib import Path

from nib_verify.core.metrics import list_families, list_metrics


def document() -> dict:
    families = list_families()
    metrics = list_metrics()
    defaults = {item["family"] for item in families if item["added_by_default"]}
    return {
        "id": "verification-v1",
        "source": "nib_verify.core.metrics",
        "families": families,
        "metrics": metrics,
        "default_methods": sorted(
            item["method"] for item in metrics if item["family"] in defaults
        ),
    }


def main() -> None:
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document(), indent=2) + "\n")


if __name__ == "__main__":
    main()
