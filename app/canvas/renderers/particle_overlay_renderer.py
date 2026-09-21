"""Canvas renderer for SourceType.PARTICLE_OVERLAY, split out of
SourceItem._paint_legacy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QPainter

from app.canvas.renderers.base import paint_background, paint_selection_guide
from app.utils.particle_painter import paint_particles

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem


def render(
    item: "SourceItem", painter: QPainter, option: object, widget: object | None = None,
) -> None:
    rect, _fill, _pen = paint_background(item, painter)
    paint_particles(
        painter,
        rect,
        style=item.source.particle_style,
        color=item.source.fill_color,
        secondary_color=item.source.particle_secondary_color,
        density=item.source.particle_density,
        speed=item.source.particle_speed,
        minimum_size=item.source.particle_min_size,
        maximum_size=item.source.particle_max_size,
        particle_opacity=item.source.particle_opacity,
        direction=item.source.particle_direction,
        drift=item.source.particle_drift,
        twinkle=item.source.particle_twinkle,
        glow=item.source.particle_glow,
        seed=item.source.particle_seed,
    )
    paint_selection_guide(item, painter, rect)
