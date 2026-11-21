"""Report reader registration drift in PR checks. Run directly from any directory."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def report(root):
    implemented = {p.parent.name for p in (root / "adapters").glob("*/source.py")}
    problems = []
    counts = [f"{len(implemented)} implementations"]
    label = "Atlas"
    registry = "atlas/runtime/adapters-catalog.json"
    contracts = "atlas/contracts/data"
    rows = json.loads((root / registry).read_text())["data_adapters"]
    counts.append(f"{len(rows)} {label} entries")
    ids = set()
    for row in rows:
        key = row["id"]
        if key in ids:
            problems.append(f"{label}: duplicate reader {key}")
        ids.add(key)
        if row.get("path") != key:
            problems.append(f"{label}: {key} declares a different reader path")
        path = root / contracts / f"{key}.json"
        if not path.is_file():
            problems.append(f"{label}: {key} is missing {contracts}/{key}.json")
            continue
        declaration = json.loads(path.read_text())
        if (declaration.get("id"), declaration.get("version")) != (
            key,
            row.get("version"),
        ):
            problems.append(f"{label}: {key} ID or version differs in {contracts}")
    problems.extend(f"{label}: register {key}" for key in sorted(implemented - ids))
    problems.extend(
        f"{label}: missing reader for {key}" for key in sorted(ids - implemented)
    )
    return counts, problems


def main():
    try:
        counts, problems = report(ROOT)
    except (KeyError, ValueError, TypeError, OSError) as error:
        counts, problems = [], [str(error)]
    lines = ["## Data reader registration", "", ", ".join(counts), ""]
    lines.extend(f"- {p}" for p in problems)
    if not problems:
        lines.append(
            "All implemented readers match the registered IDs, paths and versions."
        )
    summary = "\n".join(lines) + "\n"
    print(summary)
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as output:
            output.write(summary)
    if problems and os.getenv("GITHUB_ACTIONS"):
        print(
            "::warning::Reader registration differs. See the check summary for affected IDs."
        )
    return int(bool(problems))


if __name__ == "__main__":
    sys.exit(main())
