"""Lead-time GIF writer (VIS-005).

Animates a validated forecast sequence by replaying the *existing* leads of a
field on one prepared scene through the very same
:func:`~nib_visualization.render.render_frame` (Common Agreement A-08/A-13):
the caller calls :func:`~nib_visualization.render.prepare_scene` exactly once,
then :func:`write_animation` walks the lead indices in their stored order and
writes one GIF frame per lead with Matplotlib's PillowWriter.

Boundaries (VIS-005 task card + main plan ``api``/``engineering`` sections):

- No scene creation here: extent, projection, normalization, colormap,
  colorbar, basemap artists and pixel size are fixed by the prepared scene and
  never re-derived, re-cropped or re-decoded per frame; only the mesh values
  and the real lead/valid-time label change.
- Existing leads only: no interpolation, no resampling, no reordering, no
  dropped frames; a singleton field takes the identical path and yields a
  one-frame GIF. ``fps`` only sets the GIF playback speed — it never alters
  the scientific time semantics or the frame count.
- Fail atomically: the GIF is rendered into a temporary sibling of the target
  and replaces it only after the complete save succeeded, so a failed run
  never leaves a decodable truncated GIF at the final path and never clobbers
  a file already sitting there. A first-frame failure also surfaces the
  original render error: the writer's zero-frames ``IndexError`` never masks
  it (re-review F-R1).
- No provider knowledge: this writer never selects or loads a basemap (the
  CLI owns provider/cache resolution); it performs no network access.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering, no display required

from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402
from PIL import Image  # noqa: E402

from nib_visualization.field import VisualizationField  # noqa: E402
from nib_visualization.render import PreparedScene, render_frame  # noqa: E402

#: engineering default from the main plan: GIF playback at 2 frames per second
DEFAULT_FPS = 2.0


class _FixedPalettePillowWriter(PillowWriter):
    """Encode all frames with one palette so a static colorbar cannot flicker."""

    def finish(self) -> None:
        palette = self._frames[0].convert("RGB").quantize(
            colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
        )
        frames = [
            frame.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE)
            for frame in self._frames
        ]
        try:
            frames[0].save(
                self.outfile,
                save_all=True,
                append_images=frames[1:],
                duration=int(1000 / self.fps),
                loop=0,
                optimize=False,
                disposal=2,
            )
        finally:
            for frame in frames:
                frame.close()
            palette.close()


def write_animation(
    scene: PreparedScene,
    field: VisualizationField,
    path: Path | str,
    fps: float = DEFAULT_FPS,
) -> Path:
    """Write one GIF frame per existing lead of ``field`` onto ``scene``.

    Iterates the lead indices ``0 .. n_leads-1`` in their stored order, calling
    the scene's own :func:`~nib_visualization.render.render_frame` for each, so
    the fixed extent/projection/Normalize/colorbar/basemap of the prepared
    scene apply to every frame and only the mesh values plus the real
    lead/valid-time label are updated. The scene figure stays open afterwards
    (the caller owns its lifecycle, e.g. for a final PNG or a re-run at another
    ``fps``, which changes only the per-frame playback duration).

    The GIF is first rendered into a temporary sibling of ``path`` (same
    directory) and atomically moved onto ``path`` only after the complete
    save succeeded: a failed run leaves no truncated GIF at ``path`` and
    never clobbers a file already sitting there (VIS-005 review F-001).
    When the very first frame fails, the original exception is the one that
    propagates, not the writer's zero-frames ``IndexError`` (re-review F-R1).

    Returns the written :class:`~pathlib.Path`.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps!r}")
    n_leads = int(field.values.sizes["lead_time"])
    if n_leads < 1:
        raise ValueError("field has no lead frames to animate")

    render_failure: BaseException | None = None

    def draw_lead(lead_index: int):
        # remember the first real render failure (and re-raise it untouched):
        # with zero grabbed frames PillowWriter.finish() raises its own
        # IndexError from ``self._frames[0]`` inside MovieWriter.saving's
        # finally block, which would otherwise replace — and hide — the
        # original error (re-review F-R1)
        nonlocal render_failure
        try:
            return render_frame(scene, field, lead_index)
        except BaseException as error:
            if render_failure is None:
                render_failure = error
            raise

    animation = FuncAnimation(
        scene.figure,
        draw_lead,
        frames=n_leads,
        # the scene is fully drawn by prepare_scene (first frame mesh + time
        # label), so saving needs no extra initial draw of lead 0: every lead
        # is rendered exactly once, inside the grabbed frame loop
        init_func=lambda: None,
        cache_frame_data=False,
    )
    output = Path(path)
    # A render failure does not stop PillowWriter from producing a file:
    # MovieWriter.saving calls finish() from its finally block, so every frame
    # grabbed before the failure becomes a decodable truncated GIF at the save
    # target (VIS-005 review F-001). Saving to a temporary sibling keeps that
    # product away from the final path; os.replace then publishes the complete
    # GIF atomically (the sibling lives in the same directory, hence on the
    # same filesystem).
    handle, temp_name = tempfile.mkstemp(
        # the temp name must end in .gif: PillowWriter infers the output
        # format from the save target's file extension
        dir=output.parent,
        prefix=f".{output.stem}.",
        suffix=".tmp.gif",
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        animation.save(temp_path, writer=_FixedPalettePillowWriter(fps=fps), dpi=scene.config.dpi)
        # mkstemp creates 0o600; keep the readability of a directly written file
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, output)
    except IndexError:
        # the zero-frames mask of F-R1: finish() ran from MovieWriter.saving's
        # finally block and indexed an empty frame list, discarding the real
        # render failure — re-raise that remembered failure instead (from
        # None: the writer's IndexError is a known artifact, not a cause). A
        # later frame failure keeps propagating as itself (finish() then
        # succeeds and the original exception passes through untouched)
        if render_failure is not None:
            raise render_failure from None
        raise
    finally:
        temp_path.unlink(missing_ok=True)
    return output
