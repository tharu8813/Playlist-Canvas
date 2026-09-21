"""Canvas renderer for SourceType.AUDIO_LEVEL_METER, split out of
SourceItem._paint_legacy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QPainter

from app.canvas.renderers.base import paint_background, paint_selection_guide
from app.utils.level_meter_painter import paint_level_meter

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem


def render(
    item: "SourceItem", painter: QPainter, option: object, widget: object | None = None,
) -> None:
    rect, _fill, _pen = paint_background(item, painter)
    legacy_led = item.source.level_meter_mode == "led"
    channels = 1 if item.source.level_meter_mode == "mono" else 2
    sample_levels = (0.72,) if channels == 1 else (0.72, 0.54)
    sample_peaks = (0.82,) if channels == 1 else (0.82, 0.66)
    paint_level_meter(
        painter,
        rect,
        sample_levels,
        sample_peaks,
        style="led" if legacy_led else item.source.level_meter_style,
        orientation=item.source.level_meter_orientation,
        segments=item.source.level_meter_segments,
        gap=item.source.level_meter_gap,
        track_color=item.source.level_meter_track_color,
        low_color=item.source.level_meter_low_color,
        mid_color=item.source.level_meter_mid_color,
        high_color=item.source.level_meter_high_color,
        show_peak=item.source.level_meter_show_peak,
    )
    paint_selection_guide(item, painter, rect)
