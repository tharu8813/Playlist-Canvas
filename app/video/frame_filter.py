"""Bounded, worker-safe filtering for live video element previews."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Qt, Signal, Slot
from PySide6.QtGui import QImage


@dataclass(frozen=True, slots=True)
class VideoFrameFilterSettings:
    """Preview-only filter values and the useful display resolution."""

    width: int
    height: int
    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 1.0
    grayscale: bool = False
    blur: float = 0.0


class VideoFrameFilterSignals(QObject):
    """Return a worker result to its GUI-owned SourceItem."""

    finished = Signal(object, object)


class VideoFrameFilterTask(QRunnable):
    """Scale and filter one immutable frame outside the GUI thread."""

    def __init__(
        self,
        image: QImage,
        settings: VideoFrameFilterSettings,
        generation: object,
        signals: VideoFrameFilterSignals,
    ) -> None:
        super().__init__()
        self.image = QImage(image)
        self.settings = settings
        self.generation = generation
        self.signals = signals
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            filtered = filter_video_frame(self.image, self.settings)
        except Exception:
            filtered = QImage()
        try:
            self.signals.finished.emit(self.generation, filtered)
        except RuntimeError:
            # The owning Canvas may have closed after this bounded task began.
            pass


def filter_video_frame(
    image: QImage, settings: VideoFrameFilterSettings,
) -> QImage:
    """Return a display-sized RGBA frame using vectorized color and blur math."""
    if image.isNull():
        return QImage()
    target_width = max(8, min(1920, int(settings.width)))
    target_height = max(8, min(1080, int(settings.height)))
    scaled = image.scaled(
        target_width,
        target_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return apply_color_filters(
        scaled,
        brightness=settings.brightness,
        contrast=settings.contrast,
        saturation=settings.saturation,
        grayscale=settings.grayscale,
        blur=settings.blur,
    )


def apply_color_filters(
    image: QImage,
    *,
    brightness: float = 0.0,
    contrast: float = 0.0,
    saturation: float = 1.0,
    grayscale: bool = False,
    blur: float = 0.0,
) -> QImage:
    """Return an RGBA8888 copy of *image* with vectorized brightness, contrast,
    saturation and box-blur applied. Shared by the live-video preview and the
    static image/background element filters so both look identical."""
    result = image.convertToFormat(QImage.Format.Format_RGBA8888)
    if (
        brightness == 0.0
        and contrast == 0.0
        and saturation == 1.0
        and not grayscale
        and blur <= 0.0
    ):
        return result

    width, height = result.width(), result.height()
    stride = result.bytesPerLine()
    raw = np.frombuffer(result.bits(), dtype=np.uint8, count=result.sizeInBytes())
    pixels = raw.reshape(height, stride)[:, : width * 4].reshape(height, width, 4)
    rgb = pixels[:, :, :3].astype(np.float32)

    rgb = (rgb - 128.0) * (1.0 + float(contrast) / 100.0) + 128.0 + float(brightness) * 2.55
    effective_saturation = 0.0 if grayscale else max(0.0, min(3.0, saturation))
    if effective_saturation != 1.0:
        luminance = (
            rgb[:, :, 0:1] * 0.2126
            + rgb[:, :, 1:2] * 0.7152
            + rgb[:, :, 2:3] * 0.0722
        )
        rgb = luminance + (rgb - luminance) * effective_saturation
    radius = max(0, min(40, int(ceil(blur))))
    if radius:
        rgb = _box_blur(_box_blur(rgb, radius, axis=1), radius, axis=0)
    pixels[:, :, :3] = np.clip(rgb, 0.0, 255.0).astype(np.uint8)
    return result


def _box_blur(values: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """Apply one O(n) edge-padded box-blur pass along an image axis."""
    if radius <= 0:
        return values
    padding = [(0, 0)] * values.ndim
    padding[axis] = (radius, radius)
    padded = np.pad(values, padding, mode="edge")
    cumulative = np.cumsum(padded, axis=axis, dtype=np.float32)
    zero_shape = list(cumulative.shape)
    zero_shape[axis] = 1
    cumulative = np.concatenate(
        (np.zeros(zero_shape, dtype=np.float32), cumulative), axis=axis,
    )
    length = values.shape[axis]
    kernel = radius * 2 + 1
    upper = [slice(None)] * values.ndim
    lower = [slice(None)] * values.ndim
    upper[axis] = slice(kernel, kernel + length)
    lower[axis] = slice(0, length)
    return (cumulative[tuple(upper)] - cumulative[tuple(lower)]) / kernel
