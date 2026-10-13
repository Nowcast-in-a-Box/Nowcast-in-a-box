"""Create one offline HTML view of an existing VIS-006 run manifest."""

from __future__ import annotations

import json
import os
import tempfile
from html import escape
from pathlib import Path
from urllib.parse import quote

from PIL import Image, UnidentifiedImageError

from nib_visualization.artifacts import sha256_file


def _artifact(manifest: dict, key: str, media_type: str, root: Path) -> Path:
    outputs = manifest.get("outputs")
    entry = outputs.get(key) if isinstance(outputs, dict) else None
    if not isinstance(entry, dict):
        raise ValueError(f"manifest outputs.{key} is missing")
    raw = entry.get("path")
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError(f"manifest outputs.{key}.path is not a safe relative path")
    relative = Path(raw)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"manifest outputs.{key}.path is not a safe relative path")
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"manifest outputs.{key}.path escapes the manifest directory")
    if not target.is_file():
        raise ValueError(f"manifest outputs.{key} file missing: {target}")
    if entry.get("media_type") != media_type:
        raise ValueError(f"manifest outputs.{key}.media_type must be {media_type}")
    expected = entry.get("sha256")
    if not isinstance(expected, str) or sha256_file(target) != expected:
        raise ValueError(f"manifest outputs.{key} SHA-256 mismatch: {target}")
    try:
        with Image.open(target) as image:
            actual = image.format
    except (OSError, UnidentifiedImageError) as err:
        raise ValueError(f"manifest outputs.{key} is not a readable image: {target}") from err
    if actual != media_type.split("/")[1].upper():
        raise ValueError(f"manifest outputs.{key} is not a {media_type} file: {target}")
    return target


def _uri(target: Path, output_dir: Path) -> str:
    relative = Path(os.path.relpath(target, output_dir))
    return "/".join(quote(part, safe="") for part in relative.parts)


def _nested(data: object, *keys: str) -> object:
    value = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _display(value: object) -> str:
    if value is None or value == "":
        return "not recorded"
    return str(value)


def _frame(manifest: dict, index: int, key: str) -> object:
    frames = _nested(manifest, "time", "frames")
    if not isinstance(frames, list) or not frames:
        return None
    return _nested(frames[index], key)


def _page(manifest: dict, map_url: str, gif_url: str, manifest_url: str) -> str:
    mock_notice = (
        '<p class="notice">MOCK FORECAST — demo data, not a real forecast.</p>'
        if _nested(manifest, "input", "mock") is True else ""
    )
    rows = [
        ("Variable", _nested(manifest, "variable", "name")),
        ("Units", _nested(manifest, "variable", "units")),
        ("Synthetic input", _nested(manifest, "input", "synthetic")),
        ("Mock input", _nested(manifest, "input", "mock")),
        ("Real forecast", _nested(manifest, "input", "provenance", "is_real_forecast")),
        ("Source semantics", _nested(manifest, "input", "provenance", "source_semantics")),
        ("Initialization (UTC)", _nested(manifest, "time", "init_time_utc")),
        ("First lead (s)", _frame(manifest, 0, "lead_time_seconds")),
        ("First valid time (UTC)", _frame(manifest, 0, "valid_time_utc")),
        ("Last lead (s)", _frame(manifest, -1, "lead_time_seconds")),
        ("Last valid time (UTC)", _frame(manifest, -1, "valid_time_utc")),
        ("Frames", _nested(manifest, "render", "frame_count")),
        ("Playback (fps)", _nested(manifest, "render", "fps")),
        ("Data CRS", _nested(manifest, "space", "data_crs")),
        ("Display CRS", _nested(manifest, "space", "display_crs")),
        ("Basemap provider", _nested(manifest, "basemap", "provider")),
        ("Basemap source", _nested(manifest, "basemap", "source_id")),
        ("Basemap title", _nested(manifest, "basemap", "title")),
        ("Terrain source", _nested(manifest, "basemap", "raster", "source_id")),
        ("Artifact visual review", _nested(manifest, "checks", "visual_review", "status")),
        ("Artifact reviewer", _nested(manifest, "checks", "visual_review", "reviewer_role")),
    ]
    metadata = "\n".join(
        f"<div class=\"metadata-row\"><dt>{escape(label)}</dt>"
        f"<dd>{escape(_display(value), quote=True)}</dd></div>"
        for label, value in rows
    )
    source_notes = []
    for label, source in (
        ("Vector basemap", _nested(manifest, "basemap")),
        ("Terrain", _nested(manifest, "basemap", "raster")),
    ):
        if isinstance(source, dict) and (source.get("attribution") or source.get("disclaimer")):
            source_notes.append(
                f'<div class="source"><h3>{label}</h3>'
                f'<p>{escape(_display(source.get("attribution")))}</p>'
                f'<p>{escape(_display(source.get("disclaimer")))}</p></div>'
            )
    sources = (
        '<section class="metadata" aria-labelledby="sources-title">'
        '<h2 id="sources-title">Sources and use notes</h2>'
        f'<div class="sources">{"".join(source_notes)}</div></section>'
        if source_notes else ""
    )
    map_url = escape(map_url, quote=True)
    gif_url = escape(gif_url, quote=True)
    manifest_url = escape(manifest_url, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NiB Visualization · local artifact gallery</title>
  <style>
    :root {{ color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #1e2b31; background: #edf1f2; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; }}
    a {{ color: #126f77; text-underline-offset: .16em; }}
    a:focus-visible {{ outline: 2px solid #126f77; outline-offset: 3px; }}
    .shell {{ max-width: 1480px; margin: 0 auto; padding: 32px clamp(16px, 3vw, 48px) 48px; }}
    .eyebrow {{ margin: 0 0 6px; color: #126f77; font-size: .78rem; font-weight: 700;
      letter-spacing: .09em; text-transform: uppercase; }}
    h1 {{ margin: 0; font-size: clamp(1.55rem, 2.5vw, 2.2rem); font-weight: 650; }}
    .intro {{ margin: 8px 0 28px; color: #52636a; line-height: 1.5; }}
    .notice {{ margin: 0 0 24px; padding: 12px 16px; border-left: 4px solid #9b6500;
      background: #fff6df; color: #6b4500; font-weight: 650; line-height: 1.5; }}
    .views {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }}
    .view {{ min-width: 0; border: 1px solid #cad4d7; background: #fff; }}
    .view h2 {{ margin: 0; padding: 17px 20px 14px; border-bottom: 1px solid #dce3e5;
      font-size: 1.05rem; font-weight: 650; }}
    figure {{ margin: 0; }}
    figure img {{ display: block; width: 100%; height: auto; object-fit: contain; }}
    figcaption {{ padding: 12px 20px 17px; color: #52636a; font-size: .88rem; }}
    .metadata {{ margin-top: 26px; padding: 22px 24px;
      border: 1px solid #cad4d7; background: #fff; }}
    .metadata h2 {{ margin: 0 0 16px; font-size: 1.05rem; }}
    dl {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 0 24px; margin: 0; }}
    .metadata-row {{ min-width: 0; padding: 11px 0; border-top: 1px solid #e3e8e9; }}
    dt {{ color: #52636a; font-size: .77rem; }}
    dd {{ margin: 4px 0 0; font-size: .91rem; overflow-wrap: anywhere; }}
    .review-note {{ margin: 16px 0 0; color: #52636a; font-size: .82rem; line-height: 1.5; }}
    .sources {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }}
    .source {{ min-width: 0; overflow-wrap: anywhere; }}
    .source h3 {{ margin: 0; font-size: .95rem; }}
    .source p {{ margin: 8px 0 0; color: #52636a; font-size: .85rem; line-height: 1.6; }}
    .files {{ display: flex; flex-wrap: wrap; gap: 10px 24px; margin: 24px 0 0;
      padding-top: 16px; border-top: 1px solid #cad4d7; font-size: .88rem; }}
    @media (max-width: 900px) {{
      .views, .sources {{ grid-template-columns: minmax(0, 1fr); }}
      dl {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 520px) {{ dl {{ grid-template-columns: minmax(0, 1fr); }} }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <p class="eyebrow">Local artifact gallery · one run</p>
      <h1>NiB Visualization</h1>
      <p class="intro">Existing map and lead-time animation,
        with metadata from the run manifest.</p>
      {mock_notice}
    </header>
    <div class="views">
      <section class="view" aria-labelledby="map-title">
        <h2 id="map-title">2D Spatial Field Map</h2>
        <figure><img src="{map_url}" alt="2D spatial field map from this run">
          <figcaption>Static map · <a href="{map_url}">Open original PNG</a></figcaption>
        </figure>
      </section>
      <section class="view" aria-labelledby="animation-title">
        <h2 id="animation-title">Time / Lead-time Animation</h2>
        <figure><img src="{gif_url}" alt="Lead-time animation from this run">
          <figcaption>Original animated GIF · <a href="{gif_url}">Open original GIF</a></figcaption>
        </figure>
      </section>
    </div>
    <section class="metadata" aria-labelledby="metadata-title">
      <h2 id="metadata-title">Run metadata</h2>
      <dl>{metadata}</dl>
      <p class="review-note">Review metadata refers to the PNG/GIF artifacts;
        it is not a browser acceptance result.</p>
    </section>
    {sources}
    <footer class="files"><a href="{map_url}">map.png</a>
      <a href="{gif_url}">animation.gif</a>
      <a href="{manifest_url}">manifest.json</a></footer>
  </main>
</body>
</html>
"""


def write_gallery(manifest_path: Path | str, output_path: Path | str | None = None) -> Path:
    """Validate two existing artifacts, then atomically write their local gallery."""
    source = Path(manifest_path)
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise ValueError(f"cannot read manifest {source}: {err}") from err
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != "1":
        raise ValueError("gallery requires manifest_version == '1'")
    root = source.parent.resolve()
    map_path = _artifact(manifest, "map_png", "image/png", root)
    gif_path = _artifact(manifest, "animation_gif", "image/gif", root)
    output = Path(output_path) if output_path is not None else source.parent / "index.html"
    if output.resolve() in {source.resolve(), map_path, gif_path}:
        raise ValueError(f"gallery output cannot overwrite manifest, PNG or GIF: {output}")
    page = _page(
        manifest,
        _uri(map_path, output.parent.resolve()),
        _uri(gif_path, output.parent.resolve()),
        _uri(source.resolve(), output.parent.resolve()),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix=".gallery-", suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(page)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output
