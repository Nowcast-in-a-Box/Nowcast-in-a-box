import json
from pathlib import Path

from nib_verify.core.metrics import list_families, list_metrics

CATALOG = (
    Path(__file__).parents[2] / "atlas" / "catalog" / "metrics" / "verification-v1.json"
)


def test_atlas_metric_catalog_matches_registry():
    saved = json.loads(CATALOG.read_text())
    families = list_families()
    metrics = list_metrics()
    defaults = {item["family"] for item in families if item["added_by_default"]}
    assert saved["families"] == families
    assert saved["metrics"] == metrics
    assert saved["default_methods"] == sorted(
        item["method"] for item in metrics if item["family"] in defaults
    )
    assert "corr" not in saved["default_methods"]
