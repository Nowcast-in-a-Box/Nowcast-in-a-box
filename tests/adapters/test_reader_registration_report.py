"""Check the PR report itself. Actual catalog drift is advisory."""

import json
from pathlib import Path
from runpy import run_path

report = run_path(str(Path(__file__).with_name("report_atlas_readers.py")))["report"]


def test_report_names_missing_and_stale_registrations(tmp_path):
    source = tmp_path / "adapters" / "new" / "source.py"
    source.parent.mkdir(parents=True)
    source.touch()
    for rel, ids in (("atlas/runtime/adapters-catalog.json", ["old", "old"]),):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "data_adapters": [
                        {"id": key, "path": key, "version": "1"} for key in ids
                    ]
                }
            )
        )
    counts, problems = report(tmp_path)
    assert counts == ["1 implementations", "2 Atlas entries"]
    assert {
        "Atlas: register new",
        "Atlas: missing reader for old",
        "Atlas: duplicate reader old",
    } <= set(problems)
