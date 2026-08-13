"""Rasterize the current Qt canvas artboard for the FFmpeg renderer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap

from app.canvas.live_canvas import CanvasScene
from app.canvas.source_item import SourceItem
from app.models.playlist import PlaylistTrack
from app.models.source import SourceType
from app.preview.album_art import create_cached_ambient_background, extract_embedded_cover
from app.preview.text_template import expand_track_template
from app.services.lyrics_service import LyricsService


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
                      animation_phase_duration: float | None = None) -> QImage:
        """Capture one track state with metadata, cover art, and an optional Z band."""
        original_text: list[tuple[SourceItem, str]] = []
        original_transforms: list[tuple[SourceItem, object, float, float]] = []
        original_progress: list[tuple[SourceItem, float]] = []
        original_covers: list[tuple[SourceItem, QPixmap]] = []
        original_backgrounds: list[tuple[SourceItem, QPixmap]] = []
        original_visibility: list[tuple[SourceItem, bool]] = []
        original_outline_colors: list[tuple[SourceItem, str]] = []
        original_subtitle_lines: list[tuple[SourceItem, int, int]] = []
        original_subtitle_offsets: list[tuple[SourceItem, float]] = []
        original_subtitle_transitions: list[tuple[SourceItem, float]] = []
        original_track_list_rows: list[tuple[SourceItem, int]] = []
        # Removed sources are deliberately retained as hidden Qt items for safe Undo.
        # They must never participate in preview/export captures after a preset swap.
        source_items = [
            item for item in scene.items()
            if isinstance(item, SourceItem) and item.isVisible() and item.source.visible
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
            )
            for item in source_items
        )
        embedded_cover = extract_embedded_cover(track.file_path) if needs_embedded_cover else QPixmap()
        for index, graphics_item in enumerate(source_items):
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
                graphics_item._subtitle_transition_progress = 1.0
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
                    source.text = "\n".join(block for block in blocks if block) or source.subtitle_fallback
                    if active_cue_index == cue_index:
                        relative_index = max(0, cue_index - first)
                        source.subtitle_current_line = sum(
                            len([line for line in block.splitlines() if line.strip()])
                            for block in blocks[:relative_index]
                        )
                        source.subtitle_current_line_count = max(
                            1,
                            len([
                                line for line in blocks[relative_index].splitlines()
                                if line.strip()
                            ]),
                        )
                    else:
                        source.subtitle_current_line = -1
                        source.subtitle_current_line_count = 1
                else:
                    source.text = lyric or source.text or source.subtitle_fallback
                    source.subtitle_current_line = -1
                    source.subtitle_current_line_count = 1
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
                    eased = CanvasSnapshot._ease_out_cubic(progress)
                    graphics_item._subtitle_transition_progress = eased
                    original_transforms.append((
                        graphics_item, graphics_item.pos(), graphics_item.scale(), graphics_item.opacity()
                    ))
                    graphics_item._suppress_position_sync = True
                    if source.subtitle_animation == "fade":
                        graphics_item.setOpacity(source.opacity * eased)
                    elif source.subtitle_animation in {"scroll_up", "slide_up"}:
                        source.subtitle_scroll_offset = (
                            source.font_size + source.subtitle_line_spacing
                        ) * (1.0 - eased)
                        graphics_item.setOpacity(source.opacity * (0.32 + 0.68 * eased))
                    elif source.subtitle_animation == "scroll_down":
                        source.subtitle_scroll_offset = -(
                            source.font_size + source.subtitle_line_spacing
                        ) * (1.0 - eased)
                        graphics_item.setOpacity(source.opacity * (0.32 + 0.68 * eased))
                    elif source.subtitle_animation == "pop":
                        graphics_item.setScale(source.scale * (0.90 + 0.10 * eased))
                        graphics_item.setOpacity(source.opacity * (0.45 + 0.55 * eased))
                    elif source.subtitle_animation == "apple_music":
                        line_height = source.font_size + source.subtitle_line_spacing
                        source.subtitle_scroll_offset = line_height * 0.30 * (1.0 - eased)
                        graphics_item.setScale(source.scale * (0.975 + 0.025 * eased))
                        graphics_item.setOpacity(source.opacity * (0.20 + 0.80 * eased))
                    elif source.subtitle_animation == "spotify":
                        # A shorter, snappier lift than the soft-focus music style.
                        smooth = progress * progress * (3.0 - 2.0 * progress)
                        graphics_item._subtitle_transition_progress = smooth
                        line_height = source.font_size + source.subtitle_line_spacing
                        source.subtitle_scroll_offset = line_height * 0.11 * (1.0 - smooth)
                        graphics_item.setScale(source.scale * (0.988 + 0.012 * smooth))
                        graphics_item.setOpacity(source.opacity * (0.48 + 0.52 * smooth))
                    elif source.subtitle_animation == "blur_reveal":
                        graphics_item.setScale(source.scale * (0.985 + 0.015 * eased))
                        graphics_item.setOpacity(source.opacity * (0.26 + 0.74 * eased))
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
                    original_transforms.append((
                        graphics_item, graphics_item.pos(), graphics_item.scale(), graphics_item.opacity()
                    ))
                    graphics_item._suppress_position_sync = True
                    if source.now_playing_exit_animation == "fade":
                        graphics_item.setOpacity(source.opacity * (1.0 - exit_progress))
                    elif source.now_playing_exit_animation == "slide_up":
                        graphics_item.setPos(graphics_item.pos().x(), graphics_item.pos().y() - 24.0 * exit_progress)
                        graphics_item.setOpacity(source.opacity * (1.0 - exit_progress * 0.6))
                    elif source.now_playing_exit_animation == "slide_down":
                        graphics_item.setPos(graphics_item.pos().x(), graphics_item.pos().y() + 24.0 * exit_progress)
                        graphics_item.setOpacity(source.opacity * (1.0 - exit_progress * 0.6))
                    elif source.now_playing_exit_animation == "zoom":
                        graphics_item.setScale(source.scale * (1.0 - exit_progress * 0.12))
                        graphics_item.setOpacity(source.opacity * (1.0 - exit_progress))
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
            if source.source_type is SourceType.ALBUM_COVER and not source.content_path:
                original_covers.append((graphics_item, QPixmap(graphics_item._pixmap)))
                graphics_item._pixmap = QPixmap(embedded_cover)
                graphics_item.update()
            if source.source_type is SourceType.BACKGROUND and source.background_mode == "album_art":
                original_backgrounds.append((graphics_item, QPixmap(graphics_item._pixmap)))
                graphics_item._pixmap = (
                    create_cached_ambient_background(
                        track.file_path, max(1, round(source.width)),
                        max(1, round(source.height)), max(18.0, source.blur),
                    )
                    if source.background_ambient else QPixmap(embedded_cover)
                )
                graphics_item.update()
            if animation_phase:
                style = graphics_item.source.animation_in if animation_phase == "in" else graphics_item.source.animation_out
                if style != "none":
                    stagger = min(0.28, (index % 5) * 0.055)
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
                    local_progress = max(0.0, min(1.0, (phase_progress - stagger) / (1.0 - stagger)))
                    progress = (
                        CanvasSnapshot._ease_out_cubic(local_progress)
                        if animation_phase == "in"
                        else 1.0 - CanvasSnapshot._ease_in_cubic(local_progress)
                    )
                    original_transforms.append((graphics_item, graphics_item.pos(), graphics_item.scale(), graphics_item.opacity()))
                    graphics_item._suppress_position_sync = True
                    opacity = graphics_item.source.opacity * (
                        progress if style in {"fade", "zoom"} else 0.35 + 0.65 * progress
                    )
                    graphics_item.setOpacity(opacity)
                    if style == "zoom":
                        graphics_item.setScale(graphics_item.source.scale * (0.76 + 0.24 * progress))
                    distance = min(180.0, max(72.0, max(source.width, source.height) * 0.22)) * (1.0 - progress)
                    offset = {
                        "slide_left": (-distance, 0.0), "slide_right": (distance, 0.0),
                        "slide_up": (0.0, -distance), "slide_down": (0.0, distance),
                    }.get(style)
                    if offset:
                        graphics_item.setPos(graphics_item.pos().x() + offset[0], graphics_item.pos().y() + offset[1])
        try:
            return CanvasSnapshot.capture(
                scene, output_scale, z_min=z_min, z_max=z_max, transparent=transparent,
                image_buffer=image_buffer, capture_rect=capture_rect,
            )
        finally:
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
            for graphics_item, position, scale, opacity in original_transforms:
                graphics_item.setPos(position)
                graphics_item.setScale(scale)
                graphics_item.setOpacity(opacity)
                graphics_item._suppress_position_sync = False

    @staticmethod
    def _ease_out_cubic(value: float) -> float:
        """Return a responsive but natural entrance easing value."""
        return 1.0 - (1.0 - value) ** 3

    @staticmethod
    def _ease_in_cubic(value: float) -> float:
        """Return a deliberate exit easing value."""
        return value ** 3
