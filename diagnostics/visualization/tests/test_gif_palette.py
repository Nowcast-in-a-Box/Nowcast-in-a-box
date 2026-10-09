"""GIF acceptance: animated maps change while a fixed colorbar stays fixed."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from scripts.gif_palette import save_gif_with_shared_palette, verify_gif


def make_frame(path: Path, map_color: str, bar_color: str = "#ff00ff") -> None:
    image = Image.new("RGB", (40, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 24, 29), fill=map_color)
    draw.rectangle((30, 2, 35, 27), fill=bar_color)
    image.save(path)
    image.close()


def test_shared_palette_keeps_bar_fixed_while_map_changes(tmp_path):
    paths = [tmp_path / f"{i}.png" for i in range(3)]
    for path, color in zip(paths, ("#00c0ff", "#00c000", "#ff9000"), strict=True):
        make_frame(path, color)

    output = tmp_path / "fixed.gif"
    save_gif_with_shared_palette(paths, output, ["#ff00ff", "#ffffff"], 200)
    checks = verify_gif(output, 3, 200, (30, 2, 36, 28))
    assert checks["static_colorbar_across_gif"].startswith("passed:")
    assert checks["unique_full_frames"] == 3

    # An output with an actually changing bar must fail the same acceptance.
    bad_paths = [tmp_path / "bad0.png", tmp_path / "bad1.png"]
    make_frame(bad_paths[0], "#00c0ff")
    make_frame(bad_paths[1], "#00c000", bar_color="#0000ff")
    bad_gif = tmp_path / "changed-bar.gif"
    with Image.open(bad_paths[0]) as first, Image.open(bad_paths[1]) as second:
        first.save(bad_gif, save_all=True, append_images=[second], duration=200, loop=0)
    with pytest.raises(ValueError, match="colorbar changed"):
        verify_gif(bad_gif, 2, 200, (30, 2, 36, 28))


def test_complex_map_exposes_per_frame_palette_regression(tmp_path):
    """A crowded map palette makes the old RGB-to-GIF path change the bar."""
    paths = []
    bar_colors = [
        (round(255 * y / 79), round(255 * (79 - y) / 79), round(150 + 80 * y / 79))
        for y in range(80)
    ]
    for frame_index in range(3):
        path = tmp_path / f"complex-{frame_index}.png"
        with Image.new("RGB", (128, 96), "white") as image:
            pixels = image.load()
            for y in range(96):
                for x in range(96):
                    pixels[x, y] = (
                        (x * 3 + frame_index * 73) % 256,
                        (y * 7 + frame_index * 31) % 256,
                        (x * y + frame_index * 59) % 256,
                    )
            for y, color in enumerate(bar_colors, start=8):
                for x in range(110, 122):
                    pixels[x, y] = color
            image.save(path)
        paths.append(path)

    bar_box = (110, 8, 122, 88)
    legacy = tmp_path / "per-frame-palette.gif"
    with Image.open(paths[0]) as first, Image.open(paths[1]) as second, Image.open(
        paths[2]
    ) as third:
        first.save(legacy, save_all=True, append_images=[second, third], duration=200, loop=0)
    with pytest.raises(ValueError, match="colorbar changed"):
        verify_gif(legacy, 3, 200, bar_box)

    fixed = tmp_path / "shared-palette.gif"
    protected = [f"#{red:02x}{green:02x}{blue:02x}" for red, green, blue in bar_colors]
    save_gif_with_shared_palette(paths, fixed, protected, 200)
    checks = verify_gif(fixed, 3, 200, bar_box)
    assert checks["unique_full_frames"] == 3
