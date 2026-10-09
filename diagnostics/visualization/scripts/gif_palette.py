"""Shared GIF palette and decoded-frame checks for source-side previews."""

from __future__ import annotations

import hashlib
from math import ceil, floor
from pathlib import Path

from PIL import Image, ImageColor, ImageSequence


def save_gif_with_shared_palette(
    frame_paths: list[Path], output: Path, protected_colors: list[str], duration_ms: int
) -> dict:
    """Use one palette for all PNG frames so fixed legends retain their colors."""
    images = []
    for path in frame_paths:
        with Image.open(path) as frame:
            images.append(frame.convert("RGB"))

    required = []
    for color in protected_colors:
        rgb = ImageColor.getrgb(color)
        if rgb not in required:
            required.append(rgb)
    adaptive_slots = 256 - len(required)
    if adaptive_slots < 1:
        raise ValueError("Protected colors leave no GIF palette slots")
    adaptive = images[0].quantize(
        colors=adaptive_slots,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    adaptive_palette = adaptive.getpalette()
    extras = []
    for _count, index in sorted(adaptive.getcolors(maxcolors=256) or [], reverse=True):
        rgb = tuple(adaptive_palette[3 * index : 3 * index + 3])
        if rgb not in required and rgb not in extras:
            extras.append(rgb)
    shared_colors = (required + extras)[:256]
    shared_colors += [(0, 0, 0)] * (256 - len(shared_colors))
    palette = Image.new("P", (1, 1))
    palette.putpalette([channel for rgb in shared_colors for channel in rgb])
    quantized = [
        image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images
    ]
    quantized[0].save(
        output,
        save_all=True,
        append_images=quantized[1:],
        duration=duration_ms,
        loop=0,
        optimize=False,
        disposal=2,
    )
    for image in images + quantized:
        image.close()
    palette.close()
    adaptive.close()
    return {
        "method": "one shared 256-color palette for all frames; no dithering",
        "protected_color_count": len(required),
        "adaptive_color_slots": adaptive_slots,
    }


def axes_pixel_box(fig, axes) -> tuple[int, int, int, int]:
    """Return a Matplotlib axes interior in GIF image coordinates."""
    fig.canvas.draw()
    width, height = fig.canvas.get_width_height()
    x0, y0, x1, y1 = axes.get_window_extent().extents
    return (
        max(0, ceil(x0) + 2),
        max(0, ceil(height - y1) + 2),
        min(width, floor(x1) - 2),
        min(height, floor(height - y0) - 2),
    )


def verify_gif(gif_path: Path, expected_frames: int, duration_ms: int, colorbar_box) -> dict:
    """Decode every frame and reject a GIF whose fixed colorbar changes."""
    bar_digests = set()
    full_digests = set()
    with Image.open(gif_path) as gif:
        if gif.n_frames != expected_frames:
            raise ValueError(f"GIF has {gif.n_frames} frames; expected {expected_frames}")
        for frame in ImageSequence.Iterator(gif):
            frame.load()
            if frame.info.get("duration") != duration_ms:
                raise ValueError("GIF frame duration changed")
            rgb = frame.convert("RGB")
            bar_digests.add(hashlib.sha256(rgb.crop(colorbar_box).tobytes()).hexdigest())
            full_digests.add(hashlib.sha256(rgb.tobytes()).hexdigest())
            rgb.close()
    if len(bar_digests) != 1:
        raise ValueError(f"GIF colorbar changed across {len(bar_digests)} decoded versions")
    return {
        "gif_full_decode": (
            f"passed: {expected_frames}/{expected_frames} frames at {duration_ms} ms"
        ),
        "static_colorbar_across_gif": "passed: one pixel-identical colorbar across all frames",
        "unique_full_frames": len(full_digests),
    }
