"""Demo sample refreshes must preserve files outside the download manifest."""

import hashlib
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch-demo-data.py"
spec = importlib.util.spec_from_file_location("fetch_demo_data", SCRIPT)
fetch_demo_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_demo_data)


def test_revision_refresh_repairs_samples_and_preserves_local_data(
    tmp_path, monkeypatch
):
    local_file = tmp_path / "cma_radar" / "local-only.bin"
    local_file.parent.mkdir()
    local_file.write_bytes(b"local-only fixture")
    (tmp_path / ".rev").write_text("previous-revision\n")

    sample = tmp_path / "SEVIR" / "processed" / "sevir_vil.h5"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"stale")
    content = b"fresh"
    entry = {
        "path": "SEVIR/processed/sevir_vil.h5",
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    monkeypatch.setattr(
        fetch_demo_data,
        "read_manifest",
        lambda: {"datasets": {"SEVIR": {"files": [entry]}}},
    )
    downloads = []

    def download(entry, target):
        downloads.append(entry["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    monkeypatch.setattr(fetch_demo_data, "download", download)
    args = ["--dest", str(tmp_path)]
    assert fetch_demo_data.main([*args, "--check"]) == 1
    assert fetch_demo_data.main(args) == 0
    assert local_file.read_bytes() == b"local-only fixture"
    assert sample.read_bytes() == content
    assert (tmp_path / ".rev").read_text().strip() == fetch_demo_data.REV
    assert fetch_demo_data.main([*args, "--check"]) == 0
    assert fetch_demo_data.main(args) == 0
    assert downloads == [entry["path"]]
