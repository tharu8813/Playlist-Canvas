"""Embedded album-art extraction for track-aware Canvas previews."""

from __future__ import annotations

from base64 import b64decode
from collections import Counter
from functools import lru_cache
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen import MutagenError
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import (
    QBrush, QColor, QImage, QImageReader, QPainter, QPixmap, QRadialGradient,
)


def extract_embedded_cover(audio_path: str | Path) -> QPixmap:
    """Return the first readable embedded cover image for an audio file.

    The function deliberately returns a null pixmap when a file has no artwork;
    the Canvas source then keeps its normal placeholder.  Images are cached as
    ``QImage`` objects so changing tracks does not repeatedly parse tag data.
    """
    path = Path(audio_path)
    if not path.is_file():
        return QPixmap()
    image = _cached_cover(str(path.resolve()))
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


def extract_track_cover(
    audio_path: str | Path, cover_path: str | Path = "",
) -> QPixmap:
    """Return a project override image, falling back to embedded audio art."""
    override = Path(cover_path) if cover_path else None
    if override is not None and override.is_file():
        try:
            stat = override.stat()
            image = _cached_image_cover(
                str(override.resolve()), stat.st_mtime_ns, stat.st_size,
            )
            if not image.isNull():
                return QPixmap.fromImage(image)
        except OSError:
            pass
    return extract_embedded_cover(audio_path)


def create_ambient_background(cover: QPixmap, width: int, height: int,
                              blur_radius: float = 24.0) -> QPixmap:
    """Create a palette-only, Apple Music-like backdrop from album artwork.

    The cover itself is intentionally not enlarged behind the UI.  Instead, a
    few dominant colors are rendered as oversized, very soft radial blobs so
    faces, text, and photographic details cannot remain visible.
    """
    if cover.isNull() or width <= 0 or height <= 0:
        return QPixmap()
    palette = _dominant_colors(cover)
    base_color = palette[0]
    base_color = QColor(base_color)
    hue = base_color.hue() if base_color.hue() >= 0 else 215
    base_color.setHsv(hue, min(255, base_color.saturation() + 18),
                      max(18, int(base_color.value() * 0.32)))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(base_color)
    painter = QPainter(image)
    positions = ((0.18, 0.30), (0.78, 0.23), (0.55, 0.82), (0.92, 0.72))
    radius = max(width, height) * (0.50 + min(0.18, blur_radius / 180))
    for index, color in enumerate(palette[1:]):
        center_x = width * positions[index][0]
        center_y = height * positions[index][1]
        glow = QRadialGradient(center_x, center_y, radius)
        vivid = QColor(color)
        vivid.setAlpha(150 if index < 2 else 105)
        edge = QColor(vivid)
        edge.setAlpha(0)
        glow.setColorAt(0.0, vivid)
        glow.setColorAt(0.72, QColor(vivid.red(), vivid.green(), vivid.blue(), vivid.alpha() // 3))
        glow.setColorAt(1.0, edge)
        painter.fillRect(0, 0, width, height, QBrush(glow))
    shade = QColor("#05070C")
    shade.setAlpha(65)
    painter.fillRect(0, 0, width, height, shade)
    painter.end()
    return QPixmap.fromImage(image)


def create_cached_ambient_background(audio_path: str | Path, width: int, height: int,
                                     blur_radius: float = 24.0,
                                     cover_path: str | Path = "") -> QPixmap:
    """Return a per-track ambient backdrop without rebuilding it every frame."""
    audio = Path(audio_path)
    override = Path(cover_path) if cover_path else None
    if width <= 0 or height <= 0:
        return QPixmap()
    source_path = ""
    direct_image = False
    stat = None
    try:
        if override is not None and override.is_file():
            stat = override.stat()
            source_path = str(override.resolve())
            direct_image = True
        elif audio.is_file():
            stat = audio.stat()
            source_path = str(audio.resolve())
    except OSError:
        pass
    if not source_path or stat is None:
        return QPixmap()
    image = _cached_ambient_background(
        source_path, direct_image, stat.st_mtime_ns, stat.st_size, width, height,
        round(max(0.0, blur_radius) * 10),
    )
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


def _dominant_colors(cover: QPixmap) -> list[QColor]:
    """Extract a compact, saturated palette without retaining image detail."""
    sample = cover.toImage().scaled(
        48, 48, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_ARGB32)
    counts: Counter[tuple[int, int, int]] = Counter()
    for y in range(sample.height()):
        for x in range(sample.width()):
            color = sample.pixelColor(x, y)
            if color.alpha() < 40:
                continue
            # Quantization preserves the dominant palette while eliminating
            # individual photographic pixels and tiny high-contrast details.
            key = (color.red() // 32 * 32, color.green() // 32 * 32,
                   color.blue() // 32 * 32)
            counts[key] += 1
    if not counts:
        return [QColor("#202633"), QColor("#42536B"), QColor("#765B8E")]
    ordered = sorted(
        counts.items(),
        key=lambda item: item[1] * (1.25 if QColor(*item[0]).saturation() > 65 else 1.0),
        reverse=True,
    )
    colors = [QColor(*rgb) for rgb, _count in ordered[:4]]
    while len(colors) < 4:
        colors.append(QColor(colors[-1]))
    return colors


@lru_cache(maxsize=128)
def _cached_cover(audio_path: str) -> QImage:
    """Read cover bytes from common Mutagen tag representations."""
    try:
        audio = MutagenFile(audio_path)
    except (MutagenError, OSError):
        return QImage()
    if audio is None:
        return QImage()
    for data in _cover_candidates(audio):
        image = QImage.fromData(data)
        if not image.isNull():
            return image
    return QImage()


@lru_cache(maxsize=128)
def _cached_image_cover(
    image_path: str, _modified_ns: int, _file_size: int,
) -> QImage:
    reader = QImageReader(image_path)
    size = reader.size()
    if size.isValid() and (size.width() > 4096 or size.height() > 4096):
        reader.setScaledSize(size.scaled(
            QSize(4096, 4096), Qt.AspectRatioMode.KeepAspectRatio,
        ))
    image = reader.read()
    return image if not image.isNull() else QImage()


@lru_cache(maxsize=128)
def _cached_ambient_background(source_path: str, direct_image: bool,
                               modified_ns: int, file_size: int,
                               width: int, height: int,
                               blur_radius_tenths: int) -> QImage:
    """Cache the expensive palette extraction and radial-paint work as QImage."""
    cover_image = (
        _cached_image_cover(source_path, modified_ns, file_size) if direct_image
        else _cached_cover(source_path)
    )
    if cover_image.isNull():
        return QImage()
    ambient = create_ambient_background(
        QPixmap.fromImage(cover_image), width, height, blur_radius_tenths / 10.0,
    )
    return ambient.toImage()


def _cover_candidates(audio: object) -> list[bytes]:
    """Collect artwork byte payloads from ID3, FLAC, MP4, and generic tags."""
    candidates: list[bytes] = []
    pictures = getattr(audio, "pictures", None)
    if pictures:
        candidates.extend(
            bytes(picture.data) for picture in pictures if getattr(picture, "data", None)
        )
    tags = getattr(audio, "tags", None)
    if not tags:
        return candidates
    getall = getattr(tags, "getall", None)
    if callable(getall):
        candidates.extend(
            bytes(frame.data) for frame in getall("APIC") if getattr(frame, "data", None)
        )
    values = getattr(tags, "values", None)
    if callable(values):
        for value in values():
            if getattr(value, "data", None):
                candidates.append(bytes(value.data))
            elif isinstance(value, (bytes, bytearray)):
                candidates.append(bytes(value))
            elif isinstance(value, (list, tuple)):
                candidates.extend(bytes(entry) for entry in value if isinstance(entry, (bytes, bytearray)))
    get = getattr(tags, "get", None)
    if callable(get):
        encoded_picture = get("metadata_block_picture")
        if encoded_picture:
            try:
                payload = encoded_picture[0] if isinstance(encoded_picture, list) else encoded_picture
                decoded = b64decode(str(payload))
                # FLAC picture blocks contain a header before image bytes.  They
                # are uncommon here, so QImage is allowed to reject the block.
                candidates.append(decoded)
            except (ValueError, TypeError):
                pass
    return candidates
