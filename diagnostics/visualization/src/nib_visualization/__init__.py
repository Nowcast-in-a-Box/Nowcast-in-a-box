"""NiB Visualization kernel: local basemap access and headless rendering (MVP)."""

from nib_visualization.animation import write_animation
from nib_visualization.artifacts import write_manifest
from nib_visualization.basemap import (
    BASEMAP_UNAVAILABLE,
    BasemapError,
    BasemapView,
    CompositeBasemap,
    LocalBasemap,
    crop_basemap,
    load_basemap,
)
from nib_visualization.field import (
    FieldValidationError,
    VisualizationField,
    frame_at,
    validate_field,
)
from nib_visualization.fixtures import make_fixture
from nib_visualization.gallery import write_gallery
from nib_visualization.render import (
    PreparedScene,
    RenderConfig,
    prepare_scene,
    render_frame,
)

__version__ = "0.1.0"

__all__ = [
    "BASEMAP_UNAVAILABLE",
    "BasemapError",
    "BasemapView",
    "CompositeBasemap",
    "FieldValidationError",
    "LocalBasemap",
    "PreparedScene",
    "RenderConfig",
    "VisualizationField",
    "crop_basemap",
    "frame_at",
    "load_basemap",
    "make_fixture",
    "prepare_scene",
    "render_frame",
    "validate_field",
    "write_animation",
    "write_gallery",
    "write_manifest",
]
