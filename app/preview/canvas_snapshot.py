"""Rasterize the current Qt canvas artboard for the FFmpeg renderer."""

from __future__ import annotations

from collections.abc import Sequence
from math import ceil, floor
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from app.canvas.live_canvas import CanvasScene
from app.canvas.source_item import SourceItem
from app.animation.curves import (
    ease_in_out_cubic, ease_in_quint, ease_out_quint,
    hidden_rotation_offset, hidden_scale_factor,
    slide_distance,
)
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.preview.album_art import (
    adjust_personal_color,
    create_cached_ambient_background,
    extract_track_cover,
    extract_track_personal_color,
)
from app.preview.text_template import expand_track_template
from app.services.lyrics_service import LyricsService


# Track-driven album covers and ambient backgrounds are re-injected on every
# captured frame. Filtering them there would re-run the colour/blur pass in the
# playback loop, so the filtered result is memoised per (artwork, filter) key.
_FILTERED_TRACK_PIXMAP_CACHE: dict[tuple, QPixmap] = {}


def _filtered_track_pixmap(
    graphics_item: SourceItem, base: QPixmap, key: tuple, *, include_blur: bool = True,
) -> QPixmap:
    """Apply the source's brightness/contrast/blur to a track pixmap, memoised."""
    cached = _FILTERED_TRACK_PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached
    result = graphics_item._apply_image_filters(base, include_blur=include_blur)
    if len(_FILTERED_TRACK_PIXMAP_CACHE) > 96:
        _FILTERED_TRACK_PIXMAP_CACHE.clear()
    _FILTERED_TRACK_PIXMAP_CACHE[key] = result
    return result


class CanvasSnapshot:
    """Captures only the export artboard, without editor handles or workspace chrome."""

    @staticmethod
    def capture(scene: CanvasScene, output_scale: float = 1.0,
                z_min: float | None = None, z_max: float | None = None,
                transparent: bool = False, image_buffer: QImage | None = None,
                capture_rect: QRectF | None = None) -> QImage:
        """Render all or one Z-index band of the artboard at project resolution.

        Transparent Z bands are the basis for interleaving static Canvas content
        with audio-reactive video layers during export.
        """
        artboard = scene.artboard_rect
        requested_rect = capture_rect if capture_rect is not None else artboard
        source_rect = requested_rect.intersected(artboard)
        if source_rect.isEmpty():
            source_rect = QRectF(artboard.left(), artboard.top(), 1.0, 1.0)
        scale = max(0.25, min(1.0, output_scale))
        width = max(1, round(source_rect.width() * scale))
        height = max(1, round(source_rect.height() * scale))
        image_format = (
            QImage.Format.Format_ARGB32_Premultiplied
            if transparent else QImage.Format.Format_RGB32
        )
        if (image_buffer is not None and image_buffer.size().width() == width
                and image_buffer.size().height() == height
                and image_buffer.format() == image_format):
            image = image_buffer
        else:
            image = QImage(width, height, image_format)
        image.fill(Qt.GlobalColor.transparent if transparent else Qt.GlobalColor.black)
        selected_items = scene.selectedItems()
        grid_visible = scene.show_grid
        guide_x, guide_y = scene.guide_x, scene.guide_y
        background_suppressed = scene.suppress_render_background
        hidden_items: list[SourceItem] = []
        scene.blockSignals(True)
        try:
            scene.clearSelection()
            scene.show_grid = False
            scene.guide_x = None
            scene.guide_y = None
            scene.suppress_render_background = transparent
            if z_min is not None or z_max is not None:
                for item in scene.items():
                    if not isinstance(item, SourceItem) or not item.isVisible():
                        continue
                    z_value = item.source.z_index
                    if ((z_min is not None and z_value < z_min)
                            or (z_max is not None and z_value > z_max)):
                        hidden_items.append(item)
                        item.setVisible(False)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            scene.render(
                painter,
                QRectF(0, 0, image.width(), image.height()),
                source_rect,
                Qt.AspectRatioMode.IgnoreAspectRatio,
            )
            painter.end()
            for item in selected_items:
                item.setSelected(True)
        finally:
            for item in hidden_items:
                item.setVisible(True)
            scene.show_grid = grid_visible
            scene.guide_x = guide_x
            scene.guide_y = guide_y
            scene.suppress_render_background = background_suppressed
            scene.blockSignals(False)
        return image

    @staticmethod
    def _dynamic_band_capture_rect(
        scene: CanvasScene,
        z_min: float | None,
        z_max: float | None,
    ) -> QRectF | None:
        """Return pixel-aligned bounds for the currently visible band items."""
        artboard = scene.artboard_rect
        bounds = QRectF()
        found = False
        for item in scene.items():
            if not isinstance(item, SourceItem) or not item.isVisible():
                continue
            source = item.source
            if (
                not source.visible
                or (z_min is not None and source.z_index < z_min)
                or (z_max is not None and source.z_index > z_max)
            ):
                continue
            item_bounds = item.mapRectToScene(item.content_rect())
            # SourceItem paints a few effects beyond its nominal content.  The
            # item transform already includes current animation movement,
            # scale, and rotation; this scene-space padding covers antialiasing,
            # outlines, shadows, and lyric reveal blur without clipping them.
            shadow_padding = 0.0
            if source.shadow.enabled:
                shadow_padding = (
                    abs(source.shadow.offset_x)
                    + abs(source.shadow.offset_y)
                    + source.shadow.blur_radius * 0.25
                )
            visual_padding = max(
                3.0,
                source.outline_width + 2.0,
                shadow_padding + 2.0,
                source.subtitle_previous_blur * 2.0 + 2.0
                if source.source_type is SourceType.LYRICS else 0.0,
            ) * max(1.0, abs(item.scale()))
            item_bounds = item_bounds.adjusted(
                -visual_padding,
                -visual_padding,
                visual_padding,
                visual_padding,
            )
            bounds = item_bounds if not found else bounds.united(item_bounds)
            found = True
        if not found:
            return QRectF(artboard.left(), artboard.top(), 1.0, 1.0)
        bounds = bounds.intersected(artboard)
        if bounds.isEmpty():
            return QRectF(artboard.left(), artboard.top(), 1.0, 1.0)
        left = max(artboard.left(), floor(bounds.left()))
        top = max(artboard.top(), floor(bounds.top()))
        right = min(artboard.right(), ceil(bounds.right()))
        bottom = min(artboard.bottom(), ceil(bounds.bottom()))
        return QRectF(
            left,
            top,
            max(1.0, right - left),
            max(1.0, bottom - top),
        )

    @staticmethod
    def band_capture_envelope(
        scene: CanvasScene,
        z_min: float | None,
        z_max: float | None,
        *,
        maximum_area_ratio: float = 0.9,
    ) -> QRectF | None:
        """Return one safe fixed crop for a complete transparent Z stream."""
        artboard = scene.artboard_rect
        bounds = QRectF()
        found = False
        slide_styles = {
            "slide_left", "slide_right", "slide_up", "slide_down",
        }
        for item in scene.items():
            if not isinstance(item, SourceItem) or not item.isVisible():
                continue
            source = item.source
            if (
                not source.visible
                or (z_min is not None and source.z_index < z_min)
                or (z_max is not None and source.z_index > z_max)
            ):
                continue
            item_bounds = item.mapRectToScene(item.content_rect())
            scale = max(1.0, abs(item.scale()))
            shadow_padding = 0.0
            if source.shadow.enabled:
                shadow_padding = (
                    abs(source.shadow.offset_x)
                    + abs(source.shadow.offset_y)
                    + source.shadow.blur_radius * 0.25
                )
            padding = max(
                3.0,
                source.outline_width + 2.0,
                shadow_padding + 2.0,
                source.subtitle_previous_blur * 2.0 + 2.0
                if source.source_type is SourceType.LYRICS else 0.0,
            ) * scale
            animation_styles = {source.animation_in, source.animation_out}
            if animation_styles & slide_styles:
                padding += slide_distance(source.width, source.height) * scale
            if "rotate" in animation_styles:
                # The animation rotates up to 12 degrees around the centre.
                padding += (
                    (source.width ** 2 + source.height ** 2) ** 0.5
                    * 0.22 * scale
                )
            if source.source_type is SourceType.NOW_PLAYING:
                if source.now_playing_exit_animation in slide_styles:
                    padding += 24.0 * scale
                elif source.now_playing_exit_animation == "zoom":
                    padding += max(source.width, source.height) * 0.04 * scale
            item_bounds = item_bounds.adjusted(
                -padding, -padding, padding, padding,
            )
            bounds = item_bounds if not found else bounds.united(item_bounds)
            found = True
        if not found:
            return None
        bounds = bounds.intersected(artboard)
        if bounds.isEmpty():
            return None
        left = max(artboard.left(), floor(bounds.left()))
        top = max(artboard.top(), floor(bounds.top()))
        right = min(artboard.right(), ceil(bounds.right()))
        bottom = min(artboard.bottom(), ceil(bounds.bottom()))
        envelope = QRectF(
            left,
            top,
            max(1.0, right - left),
            max(1.0, bottom - top),
        )
        artboard_area = max(1.0, artboard.width() * artboard.height())
        if envelope.width() * envelope.height() >= (
            artboard_area * max(0.1, min(1.0, maximum_area_ratio))
        ):
            return None
        return envelope

    @staticmethod
    def _expand_partial_capture(
        partial_image: QImage,
        capture_rect: QRectF,
        artboard: QRectF,
        output_scale: float,
    ) -> QImage:
        """Place a cropped transparent render into a full-size export frame."""
        scale = max(0.25, min(1.0, output_scale))
        image = QImage(
            max(1, round(artboard.width() * scale)),
            max(1, round(artboard.height() * scale)),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        target_x = round((capture_rect.left() - artboard.left()) * scale)
        target_y = round((capture_rect.top() - artboard.top()) * scale)
        painter = QPainter(image)
        painter.drawImage(target_x, target_y, partial_image)
        painter.end()
        return image

    @staticmethod
    def z_bands(scene: CanvasScene, dynamic_source_ids: set[str]) -> list[tuple[float | None, float | None]]:
        """Return static Z-index intervals separated by reactive source layers."""
        dynamic_z = sorted({item.source.z_index for item in scene.items()
                            if isinstance(item, SourceItem) and item.isVisible()
                            and item.source.visible and item.source.id in dynamic_source_ids})
        if not dynamic_z:
            return [(None, None)]
        bands: list[tuple[float | None, float | None]] = []
        lower: float | None = None
        for z_value in dynamic_z:
            bands.append((lower, z_value - 1e-6))
            # Put a static source sharing the same legacy Z value into the
            # foreground band. The former +epsilon lower bound excluded it
            # from every band, so it vanished only in the exported video.
            lower = z_value
        bands.append((lower, None))
        static_z = [
            item.source.z_index for item in scene.items()
            if isinstance(item, SourceItem) and item.isVisible() and item.source.visible
            and item.source.id not in dynamic_source_ids
        ]

        def contains_static(band: tuple[float | None, float | None]) -> bool:
            minimum, maximum = band
            return any(
                (minimum is None or z_value >= minimum)
                and (maximum is None or z_value <= maximum)
                for z_value in static_z
            )

        # The first band is always the opaque base video.  Later transparent
        # bands are useful only when they contain an actual static source.
        return [bands[0], *(band for band in bands[1:] if contains_static(band))]

    @staticmethod
    def source_is_capture_invariant(
        source: Source, playlist_duration_seconds: float,
    ) -> bool:
        """Return whether a source is identical for every export sample.

        This deliberately accepts only a small allow-list.  A false negative
        merely keeps the established sequential capture path, while a false
        positive could freeze a time-dependent element in the final video.
        """
        if source.animation_in != "none" or source.animation_out != "none":
            return False
        if source.personal_color_enabled:
            return False
        if source.timeline_start > 0.0:
            return False
        if (
            source.timeline_duration > 0.0
            and source.timeline_start + source.timeline_duration
            < playlist_duration_seconds
        ):
            return False
        if source.source_type is SourceType.TEXT:
            return "%" not in source.text
        if source.source_type is SourceType.ALBUM_COVER:
            return bool(source.content_path)
        if source.source_type is SourceType.BACKGROUND:
            return source.background_mode != "album_art"
        return source.source_type in {
            SourceType.IMAGE,
            SourceType.SHAPE,
            SourceType.LOGO,
            SourceType.WATERMARK,
        }

    @staticmethod
    def split_mixed_capture_bands(
        scene: CanvasScene,
        dynamic_source_ids: set[str],
        z_bands: list[tuple[float | None, float | None]],
        playlist_duration_seconds: float,
        *,
        max_streams: int = 12,
    ) -> list[tuple[float | None, float | None]]:
        """Split broad Z bands into consecutive invariant/dynamic sublayers.

        Sources sharing one Z value remain together so their QGraphicsScene
        stacking order cannot change.  A global cap prevents an alternating
        project from creating an unbounded number of lossless video streams.
        """
        visible_sources = [
            item.source for item in scene.items()
            if isinstance(item, SourceItem)
            and item.isVisible()
            and item.source.visible
            and item.source.id not in dynamic_source_ids
        ]
        split_by_original: list[list[dict[str, object]]] = []
        for original_min, original_max in z_bands:
            band_sources = [
                source for source in visible_sources
                if (original_min is None or source.z_index >= original_min)
                and (original_max is None or source.z_index <= original_max)
            ]
            grouped: dict[float, list[Source]] = {}
            for source in band_sources:
                grouped.setdefault(float(source.z_index), []).append(source)
            if not grouped:
                split_by_original.append([{
                    "minimum": original_min,
                    "maximum": original_max,
                    "invariant": True,
                    "source_count": 0,
                }])
                continue
            z_values = sorted(grouped)
            group_states = [
                all(
                    CanvasSnapshot.source_is_capture_invariant(
                        source, playlist_duration_seconds,
                    )
                    for source in grouped[z_value]
                )
                for z_value in z_values
            ]
            runs: list[dict[str, object]] = []
            run_start = 0
            for group_index in range(1, len(z_values) + 1):
                at_end = group_index == len(z_values)
                if not at_end and group_states[group_index] == group_states[run_start]:
                    continue
                runs.append({
                    "minimum": (
                        original_min if run_start == 0 else z_values[run_start]
                    ),
                    "maximum": (
                        original_max if at_end else z_values[group_index - 1]
                    ),
                    "invariant": group_states[run_start],
                    "source_count": sum(
                        len(grouped[z_value])
                        for z_value in z_values[run_start:group_index]
                    ),
                })
                run_start = group_index
            split_by_original.append(runs)

        stream_limit = max(len(z_bands), max(1, int(max_streams)))

        def coalesce_equal_states(runs: list[dict[str, object]]) -> None:
            index = 0
            while index + 1 < len(runs):
                left, right = runs[index], runs[index + 1]
                if bool(left["invariant"]) != bool(right["invariant"]):
                    index += 1
                    continue
                left["maximum"] = right["maximum"]
                left["source_count"] = (
                    int(left["source_count"]) + int(right["source_count"])
                )
                runs.pop(index + 1)

        while sum(len(runs) for runs in split_by_original) > stream_limit:
            candidates: list[tuple[int, int, int, int, int]] = []
            for band_index, runs in enumerate(split_by_original):
                for run_index, (left, right) in enumerate(zip(runs, runs[1:])):
                    lost_invariant_sources = (
                        int(left["source_count"]) if bool(left["invariant"]) else 0
                    ) + (
                        int(right["source_count"]) if bool(right["invariant"]) else 0
                    )
                    candidates.append((
                        lost_invariant_sources,
                        int(left["source_count"]) + int(right["source_count"]),
                        band_index,
                        run_index,
                        len(runs),
                    ))
            if not candidates:
                break
            _lost, _size, band_index, run_index, _run_count = min(candidates)
            runs = split_by_original[band_index]
            left, right = runs[run_index], runs[run_index + 1]
            runs[run_index:run_index + 2] = [{
                "minimum": left["minimum"],
                "maximum": right["maximum"],
                "invariant": bool(left["invariant"]) and bool(right["invariant"]),
                "source_count": (
                    int(left["source_count"]) + int(right["source_count"])
                ),
            }]
            coalesce_equal_states(runs)

        return [
            (run["minimum"], run["maximum"])
            for runs in split_by_original
            for run in runs
        ]

    @staticmethod
    def invariant_stream_keys(
        scene: CanvasScene,
        dynamic_source_ids: set[str],
        z_bands: list[tuple[float | None, float | None]],
        playlist_duration_seconds: float,
    ) -> set[str]:
        """Find complete Z bands that are safe to capture only once.

        The scene itself always remains on the GUI thread.  Only bands whose
        every visible Canvas source is immutable are admitted to this cache;
        dynamic FFmpeg overlays are excluded because they are hidden during
        Canvas capture and composed later.
        """
        visible_sources = [
            item.source for item in scene.items()
            if isinstance(item, SourceItem)
            and item.isVisible()
            and item.source.visible
            and item.source.id not in dynamic_source_ids
        ]
        invariant: set[str] = set()
        for index, (z_min, z_max) in enumerate(z_bands):
            sources = [
                source for source in visible_sources
                if (z_min is None or source.z_index >= z_min)
                and (z_max is None or source.z_index <= z_max)
            ]
            if all(
                CanvasSnapshot.source_is_capture_invariant(
                    source, playlist_duration_seconds,
                )
                for source in sources
            ):
                invariant.add("base" if index == 0 else f"layer:{index - 1}")
        return invariant

    @staticmethod
    def capture_track(scene: CanvasScene, track: PlaylistTrack, track_number: int,
                      track_total: int, start_seconds: float, animation_phase: str | None = None,
                      animation_progress: float = 1.0, elapsed_seconds: float = 0.0,
                      hide_visualizers: set[str] | None = None,
                      playlist_duration_seconds: float | None = None,
                      playlist_tracks: list[PlaylistTrack] | None = None,
                      output_scale: float = 1.0, z_min: float | None = None,
                      z_max: float | None = None, transparent: bool = False,
                      hide_source_ids: set[str] | None = None,
                      image_buffer: QImage | None = None,
                      capture_rect: QRectF | None = None,
                      timeline_seconds: float | None = None,
                      animation_phase_duration: float | None = None,
                      partial_render: bool = False,
                      render_metrics: dict[str, object] | None = None,
                      band_source_items: Sequence[SourceItem] | None = None) -> QImage:
        """Capture one track state with metadata, cover art, and an optional Z band."""
        original_text: list[tuple[SourceItem, str]] = []
        original_transforms: list[
            tuple[SourceItem, object, float, float, float]
        ] = []
        original_progress: list[tuple[SourceItem, float]] = []
        original_covers: list[tuple[SourceItem, QPixmap]] = []
        original_backgrounds: list[tuple[SourceItem, QPixmap]] = []
        original_visibility: list[tuple[SourceItem, bool]] = []
        original_outline_colors: list[tuple[SourceItem, str]] = []
        original_subtitle_lines: list[tuple[SourceItem, int, int]] = []
        original_subtitle_offsets: list[tuple[SourceItem, float]] = []
        original_subtitle_transitions: list[tuple[SourceItem, float]] = []
        original_subtitle_anchors: list[
            tuple[SourceItem, int, int, int]
        ] = []
        original_track_list_rows: list[tuple[SourceItem, int]] = []
        original_personal_colors: list[
            tuple[SourceItem, dict[str, str], tuple[str, str] | None]
        ] = []
        # Removed sources are deliberately retained as hidden Qt items for safe Undo.
        # They must never participate in preview/export captures after a preset swap.
        source_items = [
            item for item in (
                band_source_items
                if band_source_items is not None else scene.items()
            )
            if isinstance(item, SourceItem) and item.isVisible() and item.source.visible
            and (z_min is None or item.source.z_index >= z_min)
            and (z_max is None or item.source.z_index <= z_max)
        ]
        hidden_source_ids = set(hide_visualizers or ()) | set(hide_source_ids or ())
        global_seconds = (
            max(0.0, timeline_seconds)
            if timeline_seconds is not None
            else max(0.0, start_seconds + elapsed_seconds)
        )
        needs_embedded_cover = any(
            item.source.id not in hidden_source_ids
            and (
                (item.source.source_type is SourceType.ALBUM_COVER and not item.source.content_path)
                or (item.source.source_type is SourceType.BACKGROUND
                    and item.source.background_mode == "album_art")
                or item.source.personal_color_enabled
            )
            for item in source_items
        )
        track_cover = (
            extract_track_cover(track.file_path, track.cover_path)
            if needs_embedded_cover else QPixmap()
        )
        personal_color = (
            extract_track_personal_color(track.file_path, track.cover_path)
            if any(item.source.personal_color_enabled for item in source_items)
            else QColor()
        )
        for graphics_item in source_items:
            if not isinstance(graphics_item, SourceItem):
                continue
            source = graphics_item.source
            hidden_for_capture = source.id in hidden_source_ids
            timing_end = source.timeline_start + source.timeline_duration
            outside_timing = (
                global_seconds < source.timeline_start
                or (source.timeline_duration > 0.0 and global_seconds >= timing_end)
            )
            if hidden_for_capture or outside_timing:
                original_visibility.append((graphics_item, graphics_item.isVisible()))
                graphics_item.setVisible(False)
                # A split preview pass deliberately excludes this source.  Do
                # not also expand templates, recreate cover backgrounds, or
                # mutate its animation state only to restore it immediately.
                continue
            if (source.source_type is SourceType.TIME
                    or (source.source_type is SourceType.TEXT and "%" in source.text)):
                original_text.append((graphics_item, source.text))
                template = (
                    source.text if "%" in source.text else "%current_time%"
                )
                source.text = expand_track_template(
                    template, track, track_number, track_total,
                    global_seconds - elapsed_seconds,
                    elapsed_seconds, playlist_duration_seconds,
                )
                graphics_item.update()
            if source.source_type is SourceType.LYRICS:
                original_text.append((graphics_item, source.text))
                original_outline_colors.append((graphics_item, source.outline_color))
                original_subtitle_lines.append((
                    graphics_item, source.subtitle_current_line,
                    source.subtitle_current_line_count,
                ))
                original_subtitle_offsets.append((graphics_item, source.subtitle_scroll_offset))
                original_subtitle_transitions.append((
                    graphics_item, graphics_item._subtitle_transition_progress,
                ))
                original_subtitle_anchors.append((
                    graphics_item, graphics_item._subtitle_anchor_line,
                    graphics_item._subtitle_anchor_line_count,
                    graphics_item._subtitle_previous_line_count,
                ))
                graphics_item._subtitle_transition_progress = 1.0
                graphics_item._subtitle_previous_line_count = 0
                effective_lyric_offset = (
                    track.lyrics_timing_offset_seconds
                    + source.subtitle_timing_offset
                )
                lyric_elapsed = max(0.0, elapsed_seconds + effective_lyric_offset)
                active_cue_index = LyricsService.current_cue_index(
                    track.lyrics, lyric_elapsed
                )
                cue_index = LyricsService.display_cue_index(track.lyrics, lyric_elapsed)
                lyric_cue = track.lyrics[cue_index] if cue_index is not None else None
                lyric = LyricsService.decode_line_breaks(
                    lyric_cue.get("text", "") if lyric_cue else ""
                )
                if cue_index is not None:
                    first = max(0, cue_index - max(0, source.subtitle_context_lines))
                    last = min(len(track.lyrics), cue_index + max(0, source.subtitle_next_lines) + 1)
                    blocks = [
                        LyricsService.decode_line_breaks(cue.get("text", "")).strip()
                        for cue in track.lyrics[first:last]
                    ]
                    block_line_counts = [
                        max(1, len([line for line in block.splitlines() if line.strip()]))
                        for block in blocks
                    ]
                    source.text = "\n".join(block for block in blocks if block) or source.subtitle_fallback
                    relative_index = max(0, cue_index - first)
                    anchor_line = sum(block_line_counts[:relative_index])
                    graphics_item._subtitle_anchor_line = anchor_line
                    graphics_item._subtitle_anchor_line_count = block_line_counts[relative_index]
                    if active_cue_index == cue_index:
                        source.subtitle_current_line = anchor_line
                        source.subtitle_current_line_count = block_line_counts[relative_index]
                    else:
                        source.subtitle_current_line = -1
                        source.subtitle_current_line_count = 1
                else:
                    source.text = lyric or source.text or source.subtitle_fallback
                    source.subtitle_current_line = -1
                    source.subtitle_current_line_count = 1
                    graphics_item._subtitle_anchor_line = -1
                    graphics_item._subtitle_anchor_line_count = 1
                if source.subtitle_style == "karaoke":
                    source.outline_color = "#FFE08A"
                elif source.subtitle_style == "minimal":
                    source.outline_color = "#FFFFFF"
                elif source.subtitle_style == "neon":
                    source.outline_color = "#72E8FF"
                if (active_cue_index == cue_index and lyric_cue
                        and source.subtitle_animation != "none"):
                    cue_start = (
                        float(lyric_cue.get("start", lyric_elapsed))
                        - effective_lyric_offset
                    )
                    progress = max(0.0, min(
                        1.0, (elapsed_seconds - cue_start) / max(0.05, source.subtitle_animation_duration)
                    ))
                    # A symmetric ease prevents the lyric stack from jumping
                    # most of its distance during the first few frames.
                    eased = ease_in_out_cubic(progress)
                    graphics_item._subtitle_transition_progress = eased
                    # Keep the lyric card/background stable. Only its text layout
                    # moves, so context lines no longer pulse and fade each time a
                    # cue changes. A full previous-cue height compensates for the
                    # new anchored layout and produces a continuous upward scroll.
                    previous_line_count = 0
                    if cue_index > 0 and source.subtitle_context_lines > 0:
                        previous_text = LyricsService.decode_line_breaks(
                            track.lyrics[cue_index - 1].get("text", "")
                        )
                        previous_line_count = max(
                            1, len([line for line in previous_text.splitlines() if line.strip()])
                        )
                    graphics_item._subtitle_previous_line_count = previous_line_count
                    line_height = graphics_item._lyric_line_height()
                    source.subtitle_scroll_offset = (
                        previous_line_count * line_height * (1.0 - eased)
                    )
                graphics_item.update()
            if source.source_type is SourceType.TRACK_LIST:
                original_text.append((graphics_item, source.text))
                original_track_list_rows.append(
                    (graphics_item, source.track_list_current_row)
                )
                tracks = playlist_tracks or [track]
                current_index = max(0, min(len(tracks) - 1, track_number - 1))
                count = max(1, source.track_list_count)
                if source.track_list_window == "upcoming":
                    first = current_index
                elif source.track_list_window == "history":
                    first = max(0, current_index - count + 1)
                else:
                    first = max(0, current_index - count // 2)
                last = min(len(tracks), first + count)
                first = max(0, last - count)
                lines: list[str] = []
                marker = {
                    "play": "▶", "dot": "●", "line": "▌", "none": "",
                }.get(source.track_list_marker, "▶")
                for list_index in range(first, last):
                    entry = tracks[list_index]
                    title = entry.title or Path(entry.file_path).stem
                    prefix = f"{list_index + 1:02d}. " if source.track_list_show_number else ""
                    active_marker = marker if list_index == current_index else " " * len(marker)
                    details: list[str] = []
                    if source.track_list_show_artist and entry.artist:
                        details.append(entry.artist)
                    if source.track_list_show_album and entry.album:
                        details.append(entry.album)
                    suffix = f" — {' · '.join(details)}" if details else ""
                    lines.append(f"{active_marker} {prefix}{title}{suffix}".strip())
                source.track_list_current_row = max(0, current_index - first)
                source.text = "\n".join(lines)
                graphics_item.update()
            if source.source_type is SourceType.NOW_PLAYING:
                original_text.append((graphics_item, source.text))
                original_visibility.append((graphics_item, graphics_item.isVisible()))
                title = track.title or Path(track.file_path).stem
                artist = track.artist or "Unknown artist"
                album = f"\n{track.album}" if track.album else ""
                source.text = f"NOW PLAYING\n{title}\n{artist}{album}"
                visible = elapsed_seconds <= source.now_playing_duration
                graphics_item.setVisible(visible)
                exit_duration = min(source.now_playing_exit_duration, source.now_playing_duration)
                exit_start = source.now_playing_duration - exit_duration
                if visible and elapsed_seconds >= exit_start and exit_duration > 0:
                    exit_progress = max(0.0, min(1.0, (elapsed_seconds - exit_start) / exit_duration))
                    # The previous quintic exit stayed almost fully opaque
                    # until the last few frames, which made moving cards look
                    # as if they popped out. Couple position/scale and opacity
                    # to a balanced curve so every exit style visibly fades
                    # while its configured motion continues.
                    exit_motion = ease_in_out_cubic(exit_progress)
                    exit_opacity = 1.0 - exit_motion
                    original_transforms.append((
                        graphics_item, graphics_item.pos(), graphics_item.scale(),
                        graphics_item.rotation(), graphics_item.opacity(),
                    ))
                    graphics_item._suppress_position_sync = True
                    if source.now_playing_exit_animation == "fade":
                        graphics_item.setOpacity(exit_opacity)
                    elif source.now_playing_exit_animation == "slide_up":
                        graphics_item.setPos(graphics_item.pos().x(), graphics_item.pos().y() - 24.0 * exit_motion)
                        graphics_item.setOpacity(exit_opacity)
                    elif source.now_playing_exit_animation == "slide_down":
                        graphics_item.setPos(graphics_item.pos().x(), graphics_item.pos().y() + 24.0 * exit_motion)
                        graphics_item.setOpacity(exit_opacity)
                    elif source.now_playing_exit_animation == "zoom":
                        graphics_item.setScale(source.scale * (1.0 - exit_motion * 0.08))
                        graphics_item.setOpacity(exit_opacity)
                graphics_item.update()
            if source.source_type is SourceType.PROGRESS_BAR:
                original_progress.append((graphics_item, source.progress_value))
                if source.progress_mode == "video":
                    source.progress_value = max(0.0, min(
                        1.0,
                        global_seconds
                        / max(0.01, playlist_duration_seconds or track.duration_seconds),
                    ))
                else:
                    source.progress_value = max(0.0, min(
                        1.0, elapsed_seconds / max(0.01, track.duration_seconds)
                    ))
                graphics_item.update()
            filter_key = (
                track.file_path, track.cover_path,
                source.brightness, source.contrast, source.blur,
            )
            if source.source_type is SourceType.ALBUM_COVER and not source.content_path:
                original_covers.append((graphics_item, QPixmap(graphics_item._pixmap)))
                graphics_item._pixmap = _filtered_track_pixmap(
                    graphics_item, QPixmap(track_cover), ("cover", *filter_key),
                )
                graphics_item.update()
            if source.source_type is SourceType.BACKGROUND and source.background_mode == "album_art":
                original_backgrounds.append((graphics_item, QPixmap(graphics_item._pixmap)))
                if source.background_ambient:
                    graphics_item._pixmap = _filtered_track_pixmap(
                        graphics_item,
                        create_cached_ambient_background(
                            track.file_path, max(1, round(source.width)),
                            max(1, round(source.height)), max(18.0, source.blur),
                            track.cover_path,
                        ),
                        ("ambient", round(source.width), round(source.height), *filter_key),
                        include_blur=False,
                    )
                else:
                    graphics_item._pixmap = _filtered_track_pixmap(
                        graphics_item, QPixmap(track_cover), ("bgcover", *filter_key),
                    )
                graphics_item.update()
            if source.personal_color_enabled and personal_color.isValid():
                fields = CanvasSnapshot._personal_color_fields(source)
                original = {field: str(getattr(source, field)) for field in fields}
                gradient_original = (
                    (source.gradient.start_color, source.gradient.end_color)
                    if "fill_color" in fields and source.gradient.enabled else None
                )
                for field, fallback in original.items():
                    setattr(source, field, adjust_personal_color(
                        personal_color,
                        fallback,
                        brightness=source.personal_color_brightness,
                        saturation=source.personal_color_saturation,
                        hue_shift=source.personal_color_hue_shift,
                        strength=source.personal_color_strength,
                    ))
                if gradient_original is not None:
                    source.gradient.start_color = adjust_personal_color(
                        personal_color, gradient_original[0],
                        brightness=source.personal_color_brightness,
                        saturation=source.personal_color_saturation,
                        hue_shift=source.personal_color_hue_shift,
                        strength=source.personal_color_strength,
                    )
                    source.gradient.end_color = adjust_personal_color(
                        personal_color, gradient_original[1],
                        brightness=source.personal_color_brightness,
                        saturation=source.personal_color_saturation,
                        hue_shift=source.personal_color_hue_shift,
                        strength=source.personal_color_strength,
                    )
                original_personal_colors.append((
                    graphics_item, original, gradient_original,
                ))
                graphics_item.update()
            if animation_phase:
                style = graphics_item.source.animation_in if animation_phase == "in" else graphics_item.source.animation_out
                if style != "none":
                    if animation_phase_duration is not None:
                        configured_duration = (
                            graphics_item.source.animation_in_duration
                            if animation_phase == "in"
                            else graphics_item.source.animation_out_duration
                        )
                        effective_duration = max(
                            0.001,
                            min(
                                configured_duration,
                                animation_phase_duration,
                            ),
                        )
                        if animation_phase == "in":
                            raw_progress = elapsed_seconds / effective_duration
                        else:
                            source_exit_start = max(
                                0.0, track.duration_seconds - effective_duration
                            )
                            raw_progress = (
                                elapsed_seconds - source_exit_start
                            ) / effective_duration
                        phase_progress = max(0.0, min(1.0, raw_progress))
                    else:
                        # Backwards-compatible path for isolated callers that only
                        # provide normalized animation progress.
                        phase_progress = max(0.0, min(1.0, animation_progress))
                    local_progress = max(0.0, min(1.0, phase_progress))
                    motion_progress = (
                        ease_out_quint(local_progress)
                        if animation_phase == "in" else
                        1.0 - ease_in_quint(local_progress)
                    )
                    opacity_progress = (
                        ease_in_out_cubic(local_progress)
                        if animation_phase == "in" else
                        1.0 - ease_in_out_cubic(local_progress)
                    )
                    original_transforms.append((
                        graphics_item, graphics_item.pos(), graphics_item.scale(),
                        graphics_item.rotation(), graphics_item.opacity(),
                    ))
                    graphics_item._suppress_position_sync = True
                    graphics_item.setOpacity(opacity_progress)
                    if style in {"zoom", "pop", "rotate"}:
                        hidden_scale = hidden_scale_factor(style)
                        graphics_item.setScale(
                            graphics_item.source.scale
                            * (hidden_scale + (1.0 - hidden_scale) * motion_progress)
                        )
                    if style == "rotate":
                        graphics_item.setRotation(
                            graphics_item.source.rotation
                            + hidden_rotation_offset(
                                style, animation_phase == "in",
                            ) * (1.0 - motion_progress)
                        )
                    distance = slide_distance(
                        source.width, source.height,
                    ) * (1.0 - motion_progress)
                    offset = {
                        "slide_left": (-distance, 0.0), "slide_right": (distance, 0.0),
                        "slide_up": (0.0, -distance), "slide_down": (0.0, distance),
                    }.get(style)
                    if offset:
                        graphics_item.setPos(graphics_item.pos().x() + offset[0], graphics_item.pos().y() + offset[1])
        try:
            effective_capture_rect = capture_rect
            used_partial_render = False
            if partial_render and transparent and capture_rect is None:
                candidate = CanvasSnapshot._dynamic_band_capture_rect(
                    scene, z_min, z_max,
                )
                artboard = scene.artboard_rect
                if candidate is not None:
                    artboard_area = max(1.0, artboard.width() * artboard.height())
                    candidate_area = candidate.width() * candidate.height()
                    # A near-full crop adds a blit without materially reducing
                    # scene painting. Keep the established full render there.
                    if candidate_area < artboard_area * 0.9:
                        effective_capture_rect = candidate
                        used_partial_render = True
            captured = CanvasSnapshot.capture(
                scene, output_scale, z_min=z_min, z_max=z_max, transparent=transparent,
                image_buffer=(None if used_partial_render else image_buffer),
                capture_rect=effective_capture_rect,
            )
            if used_partial_render and effective_capture_rect is not None:
                captured = CanvasSnapshot._expand_partial_capture(
                    captured,
                    effective_capture_rect,
                    scene.artboard_rect,
                    output_scale,
                )
            if render_metrics is not None:
                render_metrics["partial_render"] = used_partial_render
                render_metrics["capture_rect"] = effective_capture_rect
            return captured
        finally:
            for graphics_item, colors, gradient_colors in original_personal_colors:
                for field, value in colors.items():
                    setattr(graphics_item.source, field, value)
                if gradient_colors is not None:
                    graphics_item.source.gradient.start_color = gradient_colors[0]
                    graphics_item.source.gradient.end_color = gradient_colors[1]
                graphics_item.update()
            for graphics_item, text in original_text:
                graphics_item.source.text = text
                graphics_item.update()
            for graphics_item, outline_color in original_outline_colors:
                graphics_item.source.outline_color = outline_color
                graphics_item.update()
            for graphics_item, current_line, line_count in original_subtitle_lines:
                graphics_item.source.subtitle_current_line = current_line
                graphics_item.source.subtitle_current_line_count = line_count
                graphics_item.update()
            for graphics_item, scroll_offset in original_subtitle_offsets:
                graphics_item.source.subtitle_scroll_offset = scroll_offset
                graphics_item.update()
            for graphics_item, progress in original_subtitle_transitions:
                graphics_item._subtitle_transition_progress = progress
                graphics_item.update()
            for (
                graphics_item, anchor_line, anchor_count, previous_line_count,
            ) in original_subtitle_anchors:
                graphics_item._subtitle_anchor_line = anchor_line
                graphics_item._subtitle_anchor_line_count = anchor_count
                graphics_item._subtitle_previous_line_count = previous_line_count
                graphics_item.update()
            for graphics_item, current_row in original_track_list_rows:
                graphics_item.source.track_list_current_row = current_row
                graphics_item.update()
            for graphics_item, progress in original_progress:
                graphics_item.source.progress_value = progress
                graphics_item.update()
            for graphics_item, pixmap in original_covers:
                graphics_item._pixmap = pixmap
                graphics_item.update()
            for graphics_item, pixmap in original_backgrounds:
                graphics_item._pixmap = pixmap
                graphics_item.update()
            for graphics_item, visible in original_visibility:
                graphics_item.setVisible(visible)
            for graphics_item, position, scale, rotation, opacity in original_transforms:
                graphics_item.setPos(position)
                graphics_item.setScale(scale)
                graphics_item.setRotation(rotation)
                graphics_item.setOpacity(opacity)
                graphics_item._suppress_position_sync = False

    @staticmethod
    def _personal_color_fields(source: Source) -> tuple[str, ...]:
        """Return the meaningful primary color channels for one source type."""
        if source.source_type in {SourceType.TEXT, SourceType.TIME, SourceType.LYRICS}:
            return ("outline_color",)
        if source.source_type is SourceType.TRACK_LIST:
            # Keep the row background independent so the artwork color never
            # makes highlighted text disappear against an identical fill.
            return ("track_list_current_color",)
        if source.source_type is SourceType.AUDIO_LEVEL_METER:
            return (
                "level_meter_low_color", "level_meter_mid_color",
                "level_meter_high_color",
            )
        if source.source_type is SourceType.PARTICLE_OVERLAY:
            return ("fill_color", "particle_secondary_color")
        return ("fill_color",)
