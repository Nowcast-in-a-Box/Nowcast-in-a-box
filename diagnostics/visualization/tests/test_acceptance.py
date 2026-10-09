"""VIS-006 Core acceptance tests: the offline demo chain and manifest truth.

E1 — the ``demo`` command chains the real VIS-001–005 APIs (make_fixture ->
loader -> prepare_scene -> render_frame -> write_animation -> write_manifest)
for the real WMO cache on both grids with the network blocked; the explicit
explicit-cache-missing case must FAIL, not skip.

E2 — the manifest is compared against the actual products it points at
(paths/hashes/parameters/time series/frame counts recomputed here from the
fixture and the written files); a same-parameter rerun keeps the stable
fields identical while audit timestamps may move; the WMO checksum
verification result is reproduced by independent re-execution, and a
fabricated corrupt cache proves the verifier hashes real bytes; one minimal
OCHA demo smoke covers the single-file GeoPackage checksum branch.

This module does not repeat the VIS-002 validator matrix, VIS-004 spatial
oracles or VIS-005 animation semantics: those gates stay with their cards.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import unittest.mock as mock
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from nib_visualization import make_fixture
from nib_visualization.artifacts import (
    digest_field_inputs,
    sha256_file,
    verify_wmo_checksum_manifest,
)
from nib_visualization.cli import main

ROOT = Path(__file__).resolve().parents[1]
WMO_SOURCE_RECORD = ROOT / "basemap_sources" / "wmo-source.json"
OCHA_SOURCE_RECORD = ROOT / "basemap_sources" / "selected-source.json"
PRODUCT_FILES = {"animation.gif", "map.png", "manifest.json"}


def _block_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("the demo chain must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def _blocked_network():
    """Socket-blocked context usable from module-scoped fixtures."""
    from contextlib import ExitStack

    def blocked(*_args, **_kwargs):
        raise AssertionError("the demo chain must not touch the network")

    stack = ExitStack()
    for target, attribute in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "create_connection"),
    ):
        stack.enter_context(mock.patch.object(target, attribute, blocked))
    return stack


def _wmo_cache():
    cache = os.environ.get("NIB_WMO_BASEMAP_PATH")
    if not cache:
        pytest.skip("set NIB_WMO_BASEMAP_PATH for the real-cache Core acceptance chain")
    return cache


def _ocha_cache():
    cache = os.environ.get("NIB_BASEMAP_PATH")
    if not cache:
        pytest.skip("set NIB_BASEMAP_PATH for the OCHA demo smoke")
    return cache


def _run_demo(
    monkeypatch,
    output: Path,
    *,
    provider="wmo",
    grid="latlon",
    basemap=None,
    source_record=None,
    n_leads=None,
):
    _block_network(monkeypatch)
    args = ["demo", "--provider", provider, "--grid", grid]
    if basemap is not None:
        args += ["--basemap", str(basemap)]
    if source_record is not None:
        args += ["--source-record", str(source_record)]
    if n_leads is not None:
        args += ["--n-leads", str(n_leads)]
    args += ["--output", str(output)]
    return main(args)


# ---------------------------------------------------------------------------
# E1 — offline end-to-end chain on the real WMO cache
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grid", ["latlon", "projected"])
def test_e1_wmo_demo_chains_offline_for_both_grids(tmp_path, monkeypatch, grid):
    """Full WMO demo under blocked sockets; PNG/GIF/JSON readable, provider right."""
    cache = _wmo_cache()
    output = tmp_path / f"wmo-{grid}"
    assert _run_demo(monkeypatch, output, grid=grid, basemap=cache) == 0

    assert {p.name for p in output.iterdir()} == PRODUCT_FILES
    with Image.open(output / "map.png") as png:
        assert png.format == "PNG"
        assert png.size == (1000, 700)
    with Image.open(output / "animation.gif") as gif:
        assert gif.format == "GIF"
        assert gif.n_frames == 12
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    basemap = manifest["basemap"]
    assert basemap["provider"] == "wmo"
    assert basemap["cache_kind"] == "vector_tile_mirror"
    assert basemap["source_id"] == "wmo-agreed-basemap-wgs84"
    assert Path(basemap["source_record"]["path"]).name == "wmo-source.json"
    assert Path(basemap["cache_path"]).resolve() == Path(cache).resolve()
    # outputs recorded relative to the manifest's own directory
    for entry in manifest["outputs"].values():
        assert (output / entry["path"]).is_file()


def test_e1_missing_explicit_cache_fails_not_skips(tmp_path, monkeypatch):
    """An explicitly configured but absent cache is a FAIL, never a skip."""
    output = tmp_path / "missing-cache"
    rc = _run_demo(monkeypatch, output, basemap=tmp_path / "no-such-cache")
    assert rc == 1
    assert not (output / "manifest.json").exists()


def test_e1_mismatched_source_record_rejected_before_rendering(tmp_path, monkeypatch):
    """A WMO cache opened with the OCHA record fails before any artifact exists."""
    cache = _wmo_cache()
    output = tmp_path / "mismatched-record"
    rc = _run_demo(
        monkeypatch,
        output,
        basemap=cache,
        source_record=OCHA_SOURCE_RECORD,
    )
    assert rc == 1
    assert not output.exists() or not any(output.iterdir())


# ---------------------------------------------------------------------------
# E2 — manifest truthfulness against the actual products
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def wmo_latlon_manifest(tmp_path_factory):
    """One real, offline WMO demo run shared by the E2 manifest checks."""
    cache = _wmo_cache()
    output = tmp_path_factory.mktemp("e2-wmo") / "latlon"
    with _blocked_network():
        assert main(
            [
                "demo",
                "--provider",
                "wmo",
                "--grid",
                "latlon",
                "--basemap",
                cache,
                "--output",
                str(output),
            ]
        ) == 0
    return output, json.loads((output / "manifest.json").read_text(encoding="utf-8"))


def test_e2_manifest_matches_actual_products(wmo_latlon_manifest):
    output, manifest = wmo_latlon_manifest

    outputs = manifest["outputs"]
    assert set(outputs) == {"map_png", "animation_gif"}  # never its own checksum
    assert outputs["map_png"]["sha256"] == sha256_file(output / "map.png")
    assert outputs["animation_gif"]["sha256"] == sha256_file(output / "animation.gif")
    assert outputs["map_png"]["media_type"] == "image/png"
    assert outputs["animation_gif"]["media_type"] == "image/gif"

    basemap = manifest["basemap"]
    assert basemap["source_record"]["sha256"] == sha256_file(WMO_SOURCE_RECORD)
    assert basemap["acquisition_record"]["sha256"] == sha256_file(
        ROOT / "basemap_sources" / "wmo-acquisition.json"
    )


def test_e2_input_digests_and_time_series_reproduce_from_fixture(wmo_latlon_manifest):
    _output, manifest = wmo_latlon_manifest
    field = make_fixture("latlon")

    digests = digest_field_inputs(field)
    assert manifest["input"]["digest"]["values"] == digests["values"]
    assert manifest["input"]["digest"]["coords"] == digests["coords"]

    leads = field.values.coords["lead_time"].values
    expected_frames = [
        {
            "index": index,
            "lead_time_seconds": int(lead / np.timedelta64(1, "s")),
            "valid_time_utc": f"{np.datetime_as_string(field.valid_time[index], unit='s')}Z",
        }
        for index, lead in enumerate(leads)
    ]
    assert manifest["time"]["frames"] == expected_frames
    assert manifest["time"]["init_time_utc"] == "2026-09-21T00:00:00Z"


def test_e2_render_block_matches_actual_gif_and_defaults(wmo_latlon_manifest):
    output, manifest = wmo_latlon_manifest
    render = manifest["render"]

    with Image.open(output / "animation.gif") as gif:
        assert render["frame_count"] == gif.n_frames == len(manifest["time"]["frames"])

    field = make_fixture("latlon")
    finite = field.values.values[np.isfinite(field.values.values)]
    assert render["vmin"] == pytest.approx(float(finite.min()))
    assert render["vmax"] == pytest.approx(float(finite.max()))
    assert render["colormap"] == "viridis"
    assert render["fps"] == 2.0
    assert render["figure_size"] == [10.0, 7.0]
    assert render["dpi"] == 100
    assert "transparent" in render["missing_style"]

    assert manifest["checks"]["visual_review"]["status"] == "pending"
    assert all(check["result"] == "pass" for check in manifest["checks"]["automated"])


def test_e2_wmo_checksum_verification_reproduced_independently(wmo_latlon_manifest):
    """The manifest's cache verification is reproducible by re-execution here."""
    _output, manifest = wmo_latlon_manifest
    recorded = manifest["basemap"]["checksum"]

    reproduced = verify_wmo_checksum_manifest(_wmo_cache())
    for key in ("manifest_sha256", "listed_files", "verified", "mismatched", "method"):
        assert recorded[key] == reproduced[key]
    assert reproduced["verified"] == reproduced["listed_files"]
    assert reproduced["mismatched"] == []
    # the recorded SHA256SUMS digest matches the file's actual bytes
    assert reproduced["manifest_sha256"] == sha256_file(
        Path(_wmo_cache()) / "SHA256SUMS.txt"
    )


def test_e2_checksum_verifier_hashes_real_bytes(tmp_path):
    """A fabricated cache with one tampered entry proves the verifier really hashes."""
    (tmp_path / "a.pbf").write_bytes(b"tile-a")
    (tmp_path / "b.pbf").write_bytes(b"tile-b")
    good = hashlib.sha256(b"tile-a").hexdigest()
    # deliberately wrong digest for b.pbf (it is tile-a's digest, not tile-b's)
    wrong = hashlib.sha256(b"not-tile-b").hexdigest()
    (tmp_path / "SHA256SUMS.txt").write_text(
        f"{good}  a.pbf\n{wrong}  b.pbf\n", encoding="utf-8"
    )

    result = verify_wmo_checksum_manifest(tmp_path)
    assert result["listed_files"] == 2
    assert result["verified"] == 1
    assert result["mismatched"] == ["b.pbf"]


def test_e2_repeat_run_keeps_stable_fields(tmp_path, monkeypatch):
    """Same parameters rerun: input/time/render stay identical; audit time may move."""
    cache = _wmo_cache()
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    assert _run_demo(monkeypatch, first_dir, basemap=cache) == 0
    assert _run_demo(monkeypatch, second_dir, basemap=cache) == 0

    first = json.loads((first_dir / "manifest.json").read_text(encoding="utf-8"))
    second = json.loads((second_dir / "manifest.json").read_text(encoding="utf-8"))

    for stable in ("input", "space", "variable", "time", "render"):
        assert first[stable] == second[stable], stable
    for key in ("provider", "cache_kind", "source_id", "checksum"):
        assert first["basemap"][key] == second["basemap"][key], key
    # created_utc is an audit timestamp: equality is not asserted, only permitted


def test_e2_ocha_minimal_demo_smoke(tmp_path, monkeypatch):
    """One minimal OCHA demo covers the single-file GeoPackage checksum branch."""
    cache = _ocha_cache()
    output = tmp_path / "ocha-smoke"
    assert _run_demo(monkeypatch, output, provider="ocha", grid="latlon",
                     basemap=cache, n_leads=2) == 0

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    basemap = manifest["basemap"]
    assert basemap["provider"] == "ocha"
    assert basemap["cache_kind"] == "geopackage"
    assert "wmo" not in basemap
    assert basemap["checksum"]["algorithm"] == "sha256"
    assert basemap["checksum"]["value"] == sha256_file(cache)
    assert manifest["render"]["frame_count"] == 2
    assert len(manifest["time"]["frames"]) == 2
