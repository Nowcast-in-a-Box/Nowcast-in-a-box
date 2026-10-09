"""VIS-005 lead-time GIF animation acceptance tests.

A1: real WMO cache, 12 non-uniform leads — one real GIF write; decoded frame
count/size/uniform per-frame duration; the recorded lead render order and the
first/middle/last time texts against an independent oracle; extent and color
limits fixed across every frame; a second write at another fps changes only
the playback speed, never the time labels.
A2: after prepare_scene, the frame loop does no WMO tile work — no PBF
read/decode, no tile/LOD resolution, no re-crop (operation counters that
provably fired during preparation stay unchanged through the animation).
A3: singleton field — decodable one-frame GIF through the exact same path;
input values/coords stay untouched.
OCHA smoke: the CLI ``animate`` entry with provider=ocha and a two-lead
fixture (compatibility entry only, not the A1–A3 matrix).

Real-cache cases are gated on the documented env vars and FAIL (not skip) when
a variable is set but its cache is missing, matching the VIS-001/VIS-004
gating convention. All expected time strings are derived by hand from the
published fixture constants, never from the renderer's own helpers.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest
import xarray as xr
from PIL import Image, ImageChops, ImageSequence

import nib_visualization.animation as animation_module
from nib_visualization import RenderConfig, make_fixture, prepare_scene, write_animation
from nib_visualization.field import VisualizationField, validate_field
from nib_visualization.wmo_basemap import load_wmo_basemap

REPO_ROOT = Path(__file__).resolve().parents[1]
WMO_SOURCE_RECORD = REPO_ROOT / "basemap_sources" / "wmo-source.json"
OCHA_SOURCE_RECORD = REPO_ROOT / "basemap_sources" / "selected-source.json"

# 12 clearly non-uniform leads (minutes): consecutive deltas grow 5..55 min.
# Uniform GIF playback spacing must never imply uniform scientific lead
# spacing, so the animation oracle uses this irregular sequence.
NON_UNIFORM_LEAD_MINUTES = (5, 10, 20, 35, 55, 80, 110, 145, 185, 230, 280, 335)

DEFAULT_FPS = 2
DEFAULT_FRAME_DURATION_MS = 500  # int(1000 / fps) written by PillowWriter


def wmo_cache() -> Path:
    cache = os.environ.get("NIB_WMO_BASEMAP_PATH")
    if not cache:
        pytest.skip("set NIB_WMO_BASEMAP_PATH for the real WMO tile integration")
    root = Path(cache)
    if not (root / "VectorTileServer" / "metadata.json").is_file():
        pytest.fail(f"NIB_WMO_BASEMAP_PATH={root} does not point to a WMO tile cache")
    return root


def non_uniform_leads() -> np.ndarray:
    return np.array(NON_UNIFORM_LEAD_MINUTES, dtype="timedelta64[m]").astype("timedelta64[s]")


def non_uniform_latlon_fixture() -> VisualizationField:
    """Default latlon fixture values with the irregular lead sequence above.

    ``valid_time`` is left to ``validate_field`` to derive (init + lead), so
    the irregular spacing flows into both lead and valid-time semantics.
    """
    src = make_fixture("latlon")
    values = src.values.assign_coords(lead_time=non_uniform_leads())
    return validate_field(
        VisualizationField(
            values=values,
            crs=src.crs,
            init_time=src.init_time,
            temporal_statistic=src.temporal_statistic,
            provenance=src.provenance,
        )
    )


def decode_gif(path: Path) -> dict:
    """Decode one GIF: frame count, per-frame sizes/durations, loop count."""
    with Image.open(path) as gif:
        sizes = set()
        durations = []
        for index in range(gif.n_frames):
            gif.seek(index)
            sizes.add(gif.size)
            durations.append(gif.info.get("duration"))
        return {
            "n_frames": gif.n_frames,
            "sizes": sizes,
            "durations": durations,
            "loop": gif.info.get("loop"),
        }


def rendered_axes_box(scene, axes) -> tuple[int, int, int, int]:
    """Convert a Matplotlib axes bounds to an inset PIL crop box."""
    scene.figure.canvas.draw()
    width, height = scene.figure.canvas.get_width_height()
    x0, y0, x1, y1 = axes.get_window_extent().extents
    return (
        max(0, int(np.ceil(x0)) + 2),
        max(0, int(np.ceil(height - y1)) + 2),
        min(width, int(np.floor(x1)) - 2),
        min(height, int(np.floor(height - y0)) - 2),
    )


# ---------------------------------------------------------------------------
# A1 - one real 12-lead WMO GIF
# ---------------------------------------------------------------------------


def test_a1_real_wmo_twelve_lead_gif(tmp_path, monkeypatch):
    field = non_uniform_latlon_fixture()
    scene = prepare_scene(field, RenderConfig(), load_wmo_basemap(wmo_cache(), WMO_SOURCE_RECORD))

    recorded: list[dict] = []
    original_render = animation_module.render_frame

    def recording_render(scene_arg, field_arg, lead_index):
        figure = original_render(scene_arg, field_arg, lead_index)
        recorded.append(
            {
                "lead_index": lead_index,
                "time_text": scene_arg.time_artist.get_text(),
                "extent": np.asarray(scene_arg.axes.get_extent(scene_arg.display_crs)),
                "vmin": scene_arg.norm.vmin,
                "vmax": scene_arg.norm.vmax,
            }
        )
        return figure

    monkeypatch.setattr(animation_module, "render_frame", recording_render)

    try:
        out = tmp_path / "a1_wmo_12lead.gif"
        assert write_animation(scene, field, out, fps=DEFAULT_FPS) == out
        assert out.is_file()
        bar_box = rendered_axes_box(scene, scene.colorbar.ax)
        map_box = rendered_axes_box(scene, scene.axes)
    finally:
        plt.close(scene.figure)

    # every existing lead rendered exactly once, in the stored lead order
    assert [entry["lead_index"] for entry in recorded] == list(range(12))
    texts = [entry["time_text"] for entry in recorded]
    assert len(set(texts)) == 12  # each frame carries its own real time label

    # independent time oracle: init 2026-09-21T00:00:00 + hand-computed leads
    init = "2026-09-21T00:00:00"
    first, middle, last = texts[0], texts[5], texts[11]
    assert f"init {init} UTC" in first
    assert "lead +5 min" in first and "valid 2026-09-21T00:05:00 UTC" in first
    assert "lead +80 min" in middle and "valid 2026-09-21T01:20:00 UTC" in middle
    assert "lead +335 min" in last and "valid 2026-09-21T05:35:00 UTC" in last
    # the non-uniform spacing is what the labels preserve: no frame was
    # interpolated, reordered or resampled to the uniform GIF playback rate
    assert "lead +10 min" in texts[1]
    assert "lead +20 min" in texts[2]

    # fixed geographic extent and fixed color limits on every single frame
    extents = np.asarray([entry["extent"] for entry in recorded])
    assert np.allclose(extents, extents[0], rtol=0, atol=1e-9)
    assert len({(entry["vmin"], entry["vmax"]) for entry in recorded}) == 1

    decoded = decode_gif(out)
    assert decoded["n_frames"] == 12
    assert decoded["sizes"] == {(1000, 700)}
    # fps only sets playback speed: one uniform duration for every frame,
    # while the leads above stay irregular at 5..335 min
    assert decoded["durations"] == [DEFAULT_FRAME_DURATION_MS] * 12

    # Inspect decoded GIF pixels, not just the stable Matplotlib Normalize.
    # A GIF writer with a separate palette per frame can change the colorbar
    # even while scene.norm and the pre-encoding figure stay fixed.
    with Image.open(out) as gif:
        rgb_frames = [frame.convert("RGB") for frame in ImageSequence.Iterator(gif)]
    try:
        bars = {frame.crop(bar_box).tobytes() for frame in rgb_frames}
        assert len(bars) == 1
        assert ImageChops.difference(
            rgb_frames[0].crop(map_box), rgb_frames[-1].crop(map_box)
        ).getbbox() is not None
    finally:
        for frame in rgb_frames:
            frame.close()

    # a second write at another fps reproduces identical frame semantics
    # (same leads, same labels) with only the playback duration changed
    scene = prepare_scene(field, RenderConfig(), load_wmo_basemap(wmo_cache(), WMO_SOURCE_RECORD))
    recorded.clear()
    try:
        faster = tmp_path / "a1_wmo_12lead_fps4.gif"
        write_animation(scene, field, faster, fps=4)
    finally:
        plt.close(scene.figure)
    assert [entry["time_text"] for entry in recorded] == texts
    faster_decoded = decode_gif(faster)
    assert faster_decoded["n_frames"] == 12
    assert faster_decoded["durations"] == [250] * 12


# ---------------------------------------------------------------------------
# A2 - the frame loop does no WMO basemap work
# ---------------------------------------------------------------------------


def test_a2_frame_loop_does_no_wmo_basemap_work(tmp_path, monkeypatch):
    import nib_visualization.render as render_module
    import nib_visualization.wmo_basemap as wmo_module

    counts = {"resolve_tile": 0, "read_dataframe": 0, "list_layers": 0, "crop": 0}

    original_resolve = wmo_module.resolve_tile
    original_read = wmo_module.pyogrio.read_dataframe
    original_list = wmo_module.pyogrio.list_layers
    original_crop = render_module.crop_basemap

    def counting_resolve(*args, **kwargs):
        counts["resolve_tile"] += 1
        return original_resolve(*args, **kwargs)

    def counting_read(*args, **kwargs):
        counts["read_dataframe"] += 1
        return original_read(*args, **kwargs)

    def counting_list(*args, **kwargs):
        counts["list_layers"] += 1
        return original_list(*args, **kwargs)

    def counting_crop(*args, **kwargs):
        counts["crop"] += 1
        return original_crop(*args, **kwargs)

    monkeypatch.setattr(wmo_module, "resolve_tile", counting_resolve)
    monkeypatch.setattr(wmo_module.pyogrio, "read_dataframe", counting_read)
    monkeypatch.setattr(wmo_module.pyogrio, "list_layers", counting_list)
    monkeypatch.setattr(render_module, "crop_basemap", counting_crop)

    field = make_fixture("latlon")
    scene = prepare_scene(field, RenderConfig(), load_wmo_basemap(wmo_cache(), WMO_SOURCE_RECORD))
    after_prepare = dict(counts)
    # the probes provably fired during preparation (false-PASS resistance:
    # a counter that never counted proves nothing about the frame loop)
    assert after_prepare["crop"] >= 1
    assert after_prepare["resolve_tile"] >= 1
    assert after_prepare["read_dataframe"] >= 1
    assert after_prepare["list_layers"] >= 1

    try:
        write_animation(scene, field, tmp_path / "a2_wmo.gif")
        assert decode_gif(tmp_path / "a2_wmo.gif")["n_frames"] == 12
    finally:
        plt.close(scene.figure)

    # the 12-frame loop added zero tile I/O, tile/LOD resolution or re-crop
    assert counts == after_prepare


# ---------------------------------------------------------------------------
# A3 - singleton through the same path
# ---------------------------------------------------------------------------


def test_a3_singleton_one_frame_gif_input_unchanged(tmp_path):
    field = make_fixture("latlon", n_leads=1)
    before = copy.deepcopy(field)
    scene = prepare_scene(field, RenderConfig(), load_wmo_basemap(wmo_cache(), WMO_SOURCE_RECORD))
    try:
        out = tmp_path / "a3_singleton.gif"
        write_animation(scene, field, out)
        decoded = decode_gif(out)
        assert decoded["n_frames"] == 1
        assert decoded["sizes"] == {(1000, 700)}
        assert decoded["durations"] == [DEFAULT_FRAME_DURATION_MS]
        # the single frame keeps real time semantics of the only lead (+5 min)
        text = scene.time_artist.get_text()
        assert "lead +5 min" in text
        assert "valid 2026-09-21T00:05:00 UTC" in text
    finally:
        plt.close(scene.figure)

    xr.testing.assert_identical(before.values, field.values)
    np.testing.assert_array_equal(before.valid_time, field.valid_time)
    assert before.init_time == field.init_time
    assert dict(before.provenance or {}) == dict(field.provenance or {})


# ---------------------------------------------------------------------------
# OCHA CLI compatibility smoke (two leads)
# ---------------------------------------------------------------------------


def test_ocha_cli_two_frame_smoke(tmp_path, monkeypatch, capsys):
    cache = os.environ.get("NIB_BASEMAP_PATH")
    if not cache:
        pytest.skip("set NIB_BASEMAP_PATH for the real OCHA GeoPackage integration")
    if not Path(cache).is_file():
        pytest.fail(f"NIB_BASEMAP_PATH={cache} does not point to an existing cache file")

    import nib_visualization.cli as cli_module
    from nib_visualization.cli import main as cli_main

    # the CLI owns no lead-count parameter; the smoke swaps the fixture for a
    # two-lead variant, exactly as the task card allows
    monkeypatch.setattr(
        cli_module, "make_fixture", lambda grid: make_fixture(grid, n_leads=2)
    )

    out = tmp_path / "ocha" / "animate.gif"
    rc = cli_main(
        [
            "animate",
            "--provider",
            "ocha",
            "--grid",
            "latlon",
            "--basemap",
            cache,
            "--source-record",
            str(OCHA_SOURCE_RECORD),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    assert str(out) in capsys.readouterr().out
    decoded = decode_gif(out)
    assert decoded["n_frames"] == 2
    assert decoded["sizes"] == {(1000, 700)}
    assert decoded["durations"] == [DEFAULT_FRAME_DURATION_MS, DEFAULT_FRAME_DURATION_MS]


# ---------------------------------------------------------------------------
# F-001 regression - a failed run leaves no look-alike product
# (VIS-005 independent review: a frame-6 render failure used to leave a
# decodable truncated GIF at the final CLI output path)
# ---------------------------------------------------------------------------


def _fail_render_at_sixth_frame(monkeypatch, message: str) -> None:
    """Wrap the writer's render_frame to raise on the 6th frame (index 5)."""
    original_render = animation_module.render_frame

    def failing_render(scene_arg, field_arg, lead_index):
        if lead_index == 5:
            raise ValueError(message)
        return original_render(scene_arg, field_arg, lead_index)

    monkeypatch.setattr(animation_module, "render_frame", failing_render)


def _animate_cli(wmo: Path, out: Path) -> int:
    from nib_visualization.cli import main as cli_main

    argv = [
        "animate",
        "--provider",
        "wmo",
        "--grid",
        "latlon",
        "--basemap",
        str(wmo),
        "--source-record",
        str(WMO_SOURCE_RECORD),
        "--output",
        str(out),
    ]
    try:
        return cli_main(argv)
    finally:
        # the CLI error path returns before closing the scene figure
        plt.close("all")


def test_f001_failure_writes_no_truncated_gif(tmp_path, monkeypatch):
    wmo = wmo_cache()
    _fail_render_at_sixth_frame(monkeypatch, "F-001 injected frame-6 render failure")

    out = tmp_path / "anim" / "animation.gif"
    assert _animate_cli(wmo, out) == 1

    # no decodable truncated GIF appeared at the final path...
    assert not out.exists()
    # ...and no temporary file survived next to it
    assert list(out.parent.iterdir()) == []


def test_f001_failure_preserves_existing_target(tmp_path, monkeypatch):
    wmo = wmo_cache()
    _fail_render_at_sixth_frame(monkeypatch, "F-001 injected frame-6 render failure")

    out = tmp_path / "anim" / "animation.gif"
    out.parent.mkdir(parents=True)
    # a real decodable predecessor GIF already sits at the final path (the
    # two frames must differ, otherwise Pillow deduplicates them to one)
    with (
        Image.new("RGB", (10, 10)) as first,
        Image.new("RGB", (10, 10)) as second,
    ):
        first.putpixel((0, 0), (255, 0, 0))
        second.putpixel((0, 0), (0, 0, 255))
        first.save(out, save_all=True, append_images=[second], duration=500)
    original_bytes = out.read_bytes()

    assert _animate_cli(wmo, out) == 1

    # the pre-existing product is byte-identical and still the only file there
    assert out.read_bytes() == original_bytes
    assert [entry.name for entry in out.parent.iterdir()] == [out.name]
    assert decode_gif(out)["n_frames"] == 2


# ---------------------------------------------------------------------------
# F-R1 regression - a first-frame failure reports its original error
# (VIS-005 independent re-review: with zero grabbed frames
# PillowWriter.finish() raised IndexError("list index out of range") and the
# CLI error message masked the real render failure)
# ---------------------------------------------------------------------------


def _fail_render_at_first_frame(monkeypatch, message: str) -> None:
    """Wrap the writer's render_frame to raise on the very first frame."""
    original_render = animation_module.render_frame

    def failing_render(scene_arg, field_arg, lead_index):
        if lead_index == 0:
            raise ValueError(message)
        return original_render(scene_arg, field_arg, lead_index)

    monkeypatch.setattr(animation_module, "render_frame", failing_render)


def test_fr1_first_frame_failure_reports_original_error(tmp_path, monkeypatch, capsys):
    wmo = wmo_cache()
    _fail_render_at_first_frame(monkeypatch, "F-R1 injected frame-1 render failure")

    # fresh target: the CLI reports the injected render failure, not the
    # zero-frames IndexError of PillowWriter.finish(), and leaves neither a
    # final GIF nor a temporary sibling behind
    fresh = tmp_path / "fresh" / "animation.gif"
    assert _animate_cli(wmo, fresh) == 1
    stderr = capsys.readouterr().err
    assert "F-R1 injected frame-1 render failure" in stderr
    assert "list index out of range" not in stderr
    assert not fresh.exists()
    assert list(fresh.parent.iterdir()) == []

    # a pre-existing product survives the same first-frame failure untouched
    existing = tmp_path / "existing" / "animation.gif"
    existing.parent.mkdir(parents=True)
    # (the two frames must differ, otherwise Pillow deduplicates them to one)
    with (
        Image.new("RGB", (10, 10)) as first,
        Image.new("RGB", (10, 10)) as second,
    ):
        first.putpixel((0, 0), (255, 0, 0))
        second.putpixel((0, 0), (0, 0, 255))
        first.save(existing, save_all=True, append_images=[second], duration=500)
    original_bytes = existing.read_bytes()

    assert _animate_cli(wmo, existing) == 1
    assert "F-R1 injected frame-1 render failure" in capsys.readouterr().err
    assert existing.read_bytes() == original_bytes
    assert [entry.name for entry in existing.parent.iterdir()] == [existing.name]


# ---------------------------------------------------------------------------
# writer argument guard (no cache needed)
# ---------------------------------------------------------------------------


def test_write_animation_rejects_non_positive_fps(tmp_path):
    field = make_fixture("latlon", n_leads=1)
    with pytest.raises(ValueError, match="fps"):
        write_animation(None, field, tmp_path / "never_written.gif", fps=0)
    assert not (tmp_path / "never_written.gif").exists()
