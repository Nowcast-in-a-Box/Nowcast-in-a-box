"""VIS-008: one-run, local gallery contract and failure safety."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from nib_visualization.cli import main
from nib_visualization.gallery import write_gallery


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def run(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "run"
    root.mkdir()
    map_path = root / "map #1.png"
    gif_path = root / "animation.gif"
    Image.new("RGB", (8, 6), "teal").save(map_path)
    frames = [Image.new("RGB", (8, 6), color) for color in ("red", "blue")]
    frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=500, loop=0)
    manifest = {
        "manifest_version": "1",
        "input": {"synthetic": True},
        "variable": {"name": "rain <script>alert(1)</script>", "units": 'mm & "h"'},
        "time": {
            "init_time_utc": "2026-09-21T00:00:00Z",
            "frames": [
                {"lead_time_seconds": 300, "valid_time_utc": "2026-09-21T00:05:00Z"},
                {"lead_time_seconds": 600, "valid_time_utc": "2026-09-21T00:10:00Z"},
            ],
        },
        "render": {"frame_count": 2, "fps": 2},
        "space": {"data_crs": "EPSG:4326", "display_crs": "PlateCarree"},
        "basemap": {"provider": "wmo", "source_id": "local-test", "title": "A & B"},
        "checks": {"visual_review": {"status": "pending"}},
        "outputs": {
            "map_png": {
                "path": map_path.name,
                "media_type": "image/png",
                "sha256": _sha(map_path),
            },
            "animation_gif": {
                "path": gif_path.name,
                "media_type": "image/gif",
                "sha256": _sha(gif_path),
            },
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, manifest


def _save(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_default_gallery_content_and_inputs_unchanged(run: tuple[Path, dict]) -> None:
    manifest_path, manifest = run
    inputs = [
        manifest_path,
        manifest_path.parent / "map #1.png",
        manifest_path.parent / "animation.gif",
    ]
    before = [_sha(path) for path in inputs]

    result = write_gallery(manifest_path)

    assert result == manifest_path.parent / "index.html"
    page = result.read_text(encoding="utf-8")
    assert "NiB Visualization" in page and "local artifact gallery" in page
    assert "2D Spatial Field Map" in page and "Time / Lead-time Animation" in page
    assert page.count('src="map%20%231.png"') == 1
    assert page.count('src="animation.gif"') == 1
    assert 'href="map%20%231.png"' in page
    assert 'href="animation.gif"' in page
    assert 'href="manifest.json"' in page
    assert "rain &lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "mm &amp; &quot;h&quot;" in page
    assert "A &amp; B" in page
    assert "<script>alert(1)</script>" not in page
    for value in ("True", "2026-09-21T00:05:00Z", "2026-09-21T00:10:00Z",
                  "300", "600", "EPSG:4326", "PlateCarree", "local-test", "pending"):
        assert value in page
    assert not re.search(r"(?:src|href)=[\"']https?://", page)
    assert [_sha(path) for path in inputs] == before
    assert sorted(path.name for path in manifest_path.parent.iterdir()) == [
        "animation.gif", "index.html", "manifest.json", "map #1.png"
    ]
    assert manifest["outputs"]["map_png"]["path"] == "map #1.png"


def test_explicit_output_uses_encoded_relative_links(
    run: tuple[Path, dict], tmp_path: Path
) -> None:
    manifest_path, _ = run
    output = tmp_path / "review space" / "index.html"
    assert write_gallery(manifest_path, output) == output
    page = output.read_text(encoding="utf-8")
    assert 'src="../run/map%20%231.png"' in page
    assert 'src="../run/animation.gif"' in page
    assert 'href="../run/manifest.json"' in page


def test_mock_and_terrain_provenance_is_visible_without_changing_artifacts(
    run: tuple[Path, dict],
) -> None:
    manifest_path, manifest = run
    manifest["input"] = {
        "synthetic": False,
        "mock": True,
        "provenance": {
            "is_real_forecast": False,
            "source_semantics": "Observations replayed as pseudo forecast leads",
        },
    }
    manifest["basemap"].update({
        "attribution": "Vector attribution & terms",
        "disclaimer": "No official endorsement",
        "raster": {
            "source_id": "test-terrain-source",
            "attribution": "Terrain <credit> & provider; 30 arc-second derivative",
            "disclaimer": "Not for navigation <script>alert(2)</script>",
        },
    })
    manifest["checks"]["visual_review"].update({
        "status": "pass", "reviewer_role": "implementer; not independent review",
    })
    _save(manifest_path, manifest)
    inputs = [manifest_path, manifest_path.parent / "map #1.png",
              manifest_path.parent / "animation.gif"]
    before = [_sha(path) for path in inputs]

    page = write_gallery(manifest_path).read_text(encoding="utf-8")

    assert "MOCK FORECAST" in page and "not a real forecast" in page
    for label, value in (
        ("Synthetic input", "False"), ("Mock input", "True"), ("Real forecast", "False"),
        ("Source semantics", "Observations replayed as pseudo forecast leads"),
        ("Terrain source", "test-terrain-source"),
        ("Artifact visual review", "pass"),
        ("Artifact reviewer", "implementer; not independent review"),
    ):
        assert f"<dt>{label}</dt><dd>{value}</dd>" in page
    assert "Vector attribution &amp; terms" in page
    assert "No official endorsement" in page
    assert "Terrain &lt;credit&gt; &amp; provider; 30 arc-second derivative" in page
    assert "Not for navigation &lt;script&gt;alert(2)&lt;/script&gt;" in page
    assert "<script>alert(2)</script>" not in page
    assert "not a browser acceptance result" in page
    assert [_sha(path) for path in inputs] == before


@pytest.mark.parametrize("synthetic", [True, False])
def test_missing_provenance_does_not_imply_real_forecast(
    run: tuple[Path, dict], synthetic: bool,
) -> None:
    manifest_path, manifest = run
    manifest["input"] = {"synthetic": synthetic}
    _save(manifest_path, manifest)

    page = write_gallery(manifest_path).read_text(encoding="utf-8")

    for label in ("Mock input", "Real forecast", "Source semantics", "Terrain source",
                  "Artifact reviewer"):
        assert f"<dt>{label}</dt><dd>not recorded</dd>" in page
    assert "MOCK FORECAST" not in page
    assert "ETOPO" not in page


@pytest.mark.parametrize(
    "bad_path", ["../outside.png", "/tmp/outside.png", "sub/../../outside.png"]
)
def test_rejects_path_escape_without_touching_old_page(
    run: tuple[Path, dict], bad_path: str
) -> None:
    manifest_path, manifest = run
    old = manifest_path.parent / "index.html"
    old.write_bytes(b"old gallery")
    manifest["outputs"]["map_png"]["path"] = bad_path
    _save(manifest_path, manifest)
    with pytest.raises(ValueError, match="path"):
        write_gallery(manifest_path)
    assert old.read_bytes() == b"old gallery"


@pytest.mark.parametrize("failure", ["missing", "hash"])
def test_bad_artifact_leaves_no_new_page(run: tuple[Path, dict], failure: str) -> None:
    manifest_path, manifest = run
    if failure == "missing":
        (manifest_path.parent / "animation.gif").unlink()
    else:
        manifest["outputs"]["animation_gif"]["sha256"] = "0" * 64
        _save(manifest_path, manifest)
    with pytest.raises(ValueError, match="missing|SHA-256"):
        write_gallery(manifest_path)
    assert not (manifest_path.parent / "index.html").exists()


@pytest.mark.parametrize("failure", ["json", "version", "outputs", "media"])
def test_bad_manifest_keeps_existing_page(run: tuple[Path, dict], failure: str) -> None:
    manifest_path, manifest = run
    old = manifest_path.parent / "index.html"
    old.write_bytes(b"old gallery")
    if failure == "json":
        manifest_path.write_text("{broken", encoding="utf-8")
    else:
        if failure == "version":
            manifest["manifest_version"] = "2"
        elif failure == "outputs":
            manifest["outputs"] = None
        else:
            manifest["outputs"]["map_png"]["media_type"] = "image/jpeg"
        _save(manifest_path, manifest)
    with pytest.raises(ValueError):
        write_gallery(manifest_path)
    assert old.read_bytes() == b"old gallery"


def test_optional_metadata_is_not_invented(run: tuple[Path, dict]) -> None:
    manifest_path, manifest = run
    manifest.pop("checks")
    manifest["time"]["frames"] = []
    _save(manifest_path, manifest)
    page = write_gallery(manifest_path).read_text(encoding="utf-8")
    assert "not recorded" in page
    assert "pending" not in page


@pytest.mark.parametrize("protected", ["manifest.json", "map #1.png", "animation.gif"])
def test_cannot_overwrite_inputs(run: tuple[Path, dict], protected: str) -> None:
    manifest_path, _ = run
    path = manifest_path.parent / protected
    before = _sha(path)
    with pytest.raises(ValueError, match="overwrite"):
        write_gallery(manifest_path, path)
    assert _sha(path) == before


def test_cli_success_and_failure(
    run: tuple[Path, dict], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, manifest = run
    assert main(["gallery", "--manifest", str(manifest_path)]) == 0
    assert str(manifest_path.parent / "index.html") in capsys.readouterr().out
    old = (manifest_path.parent / "index.html").read_bytes()
    manifest["outputs"]["map_png"]["sha256"] = "0" * 64
    _save(manifest_path, manifest)
    assert main(["gallery", "--manifest", str(manifest_path)]) != 0
    assert "SHA-256" in capsys.readouterr().err
    assert (manifest_path.parent / "index.html").read_bytes() == old
