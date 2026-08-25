from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.preview.gpu_texture_surface import (
    GPU_TEXTURE_SURFACE_AVAILABLE,
    GpuBackendInfo, GpuPreviewLayer,
    GpuTexturePreviewSurface, _TextureEntry,
)


class GpuTextureSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_backend_label_keeps_api_version_and_renderer(self) -> None:
        info = GpuBackendInfo(
            api="OpenGL", version="4.6", vendor="Vendor", renderer="Renderer",
        )
        self.assertEqual(info.label, "OpenGL 4.6 · Renderer")

    @unittest.skipUnless(
        GPU_TEXTURE_SURFACE_AVAILABLE, "Qt OpenGL widgets are unavailable",
    )
    def test_surface_queue_keeps_only_the_newest_frame_before_upload(self) -> None:
        surface = GpuTexturePreviewSurface()
        first = QImage(8, 8, QImage.Format.Format_RGBA8888)
        first.fill(QColor(255, 0, 0))
        second = QImage(8, 8, QImage.Format.Format_RGBA8888)
        second.fill(QColor(0, 0, 255))

        surface.set_image(first)
        surface.set_image(second)

        self.assertEqual(
            surface._pending_image.pixelColor(4, 4), QColor(0, 0, 255),
        )
        self.assertEqual(surface.upload_stats.submitted_frames, 2)
        self.assertEqual(surface.upload_stats.dropped_pending_frames, 1)
        self.assertFalse(surface.ready)
        surface.deleteLater()

    @unittest.skipUnless(
        GPU_TEXTURE_SURFACE_AVAILABLE, "Qt OpenGL widgets are unavailable",
    )
    def test_surface_queue_keeps_ordered_layers_and_latest_scene(self) -> None:
        surface = GpuTexturePreviewSurface()
        base = QImage(16, 9, QImage.Format.Format_RGBA8888)
        base.fill(QColor("#112233"))
        overlay = QImage(4, 3, QImage.Format.Format_RGBA8888)
        overlay.fill(QColor(255, 0, 0, 128))
        surface.set_layers(QSize(16, 9), (
            GpuPreviewLayer("base", base, QRectF(0, 0, 16, 9)),
            GpuPreviewLayer("overlay", overlay, QRectF(2, 1, 4, 3), rotation=12),
        ))

        self.assertEqual(surface._pending_canvas_size, QSize(16, 9))
        self.assertEqual(
            [layer.key for layer in surface._pending_layers],
            ["base", "overlay"],
        )
        self.assertEqual(surface._pending_layers[1].rotation, 12)
        overlay.fill(QColor("#00FF00"))
        self.assertEqual(
            surface._pending_layers[1].image.pixelColor(1, 1),
            QColor(255, 0, 0, 128),
        )
        surface.deleteLater()

    @unittest.skipUnless(
        GPU_TEXTURE_SURFACE_AVAILABLE, "Qt OpenGL widgets are unavailable",
    )
    def test_surface_queue_retains_native_video_frame_without_image_copy(self) -> None:
        surface = GpuTexturePreviewSurface()
        image = QImage(16, 9, QImage.Format.Format_RGBA8888)
        image.fill(QColor("#123456"))
        frame = QVideoFrame(image)

        surface.set_layers(QSize(16, 9), (
            GpuPreviewLayer(
                "native-video", QImage(), QRectF(0, 0, 16, 9),
                video_frame=frame, native_serial=7,
            ),
        ))

        self.assertEqual(len(surface._pending_layers), 1)
        queued = surface._pending_layers[0]
        self.assertTrue(queued.image.isNull())
        self.assertIsNotNone(queued.video_frame)
        assert queued.video_frame is not None
        self.assertTrue(queued.video_frame.isValid())
        self.assertEqual(queued.native_serial, 7)
        surface.deleteLater()

    @unittest.skipUnless(
        GPU_TEXTURE_SURFACE_AVAILABLE, "Qt OpenGL widgets are unavailable",
    )
    def test_nv12_frame_upload_copies_planes_without_rgba_conversion(self) -> None:
        class TextureStub:
            def isCreated(self) -> bool:
                return True

            def destroy(self) -> None:
                pass

        frame_format = QVideoFrameFormat(
            QSize(4, 4), QVideoFrameFormat.PixelFormat.Format_NV12,
        )
        frame = QVideoFrame(frame_format)
        self.assertTrue(frame.map(QVideoFrame.MapMode.WriteOnly))
        try:
            for plane, rows, row_bytes, value in (
                (0, 4, 4, 0x40), (1, 2, 4, 0x80),
            ):
                bits = memoryview(frame.bits(plane)).cast("B")
                bits[:] = bytes(len(bits))
                stride = frame.bytesPerLine(plane)
                for row in range(rows):
                    start = row * stride
                    bits[start:start + row_bytes] = bytes([value]) * row_bytes
        finally:
            frame.unmap()
        surface = GpuTexturePreviewSurface()
        uploads: list[bytes] = []

        def make_texture(
            _width: int, _height: int, _texture_format: object,
            _pixel_format: object, data: bytearray,
        ) -> TextureStub:
            uploads.append(bytes(data))
            return TextureStub()

        layer = GpuPreviewLayer(
            "nv12", QImage(), QRectF(0, 0, 4, 4),
            video_frame=frame, native_serial=1, native_scale=0.5,
        )
        with patch.object(surface, "_new_raw_texture", side_effect=make_texture):
            surface._upload_video_frame_layer(layer)

        self.assertEqual(uploads, [bytes([0x40]) * 4, bytes([0x80]) * 2])
        entry = surface._textures["nv12"]
        self.assertEqual(entry.video_format, "Format_NV12")
        self.assertEqual((entry.width, entry.height), (2, 2))
        self.assertEqual(entry.allocated_bytes, 6)
        with patch.object(surface, "_upload_raw_texture") as upload_again:
            surface._upload_video_frame_layer(layer)
        upload_again.assert_not_called()
        self.assertEqual(surface.upload_stats.texture_reuses, 1)
        surface._textures.clear()
        surface.deleteLater()

    @unittest.skipUnless(
        GPU_TEXTURE_SURFACE_AVAILABLE, "Qt OpenGL widgets are unavailable",
    )
    def test_dynamic_upload_uses_existing_storage_without_redefining_it(self) -> None:
        from PySide6.QtOpenGL import QOpenGLTexture

        class TextureStub:
            def __init__(self) -> None:
                self.arguments: tuple[object, ...] = ()

            def isCreated(self) -> bool:
                return True

            def setData(self, *arguments: object) -> None:
                self.arguments = arguments

        surface = GpuTexturePreviewSurface()
        texture = TextureStub()
        surface._textures["dynamic"] = _TextureEntry(
            texture, 1, 16, 8, 16 * 8 * 4, 0,
        )
        image = QImage(16, 8, QImage.Format.Format_RGBA8888)
        image.fill(QColor("#336699"))

        surface._upload_layer(
            GpuPreviewLayer("dynamic", image, QRectF(0, 0, 16, 8)),
        )

        self.assertEqual(
            texture.arguments[:2],
            (
                QOpenGLTexture.PixelFormat.RGBA,
                QOpenGLTexture.PixelType.UInt8,
            ),
        )
        self.assertIsInstance(texture.arguments[2], int)
        self.assertIs(surface._textures["dynamic"].texture, texture)
        surface._textures.clear()
        surface.deleteLater()

    @unittest.skipUnless(
        GPU_TEXTURE_SURFACE_AVAILABLE, "Qt OpenGL widgets are unavailable",
    )
    def test_visible_surface_allocates_one_texture_per_layer(self) -> None:
        surface = GpuTexturePreviewSurface()
        failures: list[str] = []
        surface.backend_failed.connect(failures.append)
        base = QImage(64, 36, QImage.Format.Format_RGBA8888)
        base.fill(QColor("#112233"))
        overlay = QImage(16, 12, QImage.Format.Format_RGBA8888)
        overlay.fill(QColor(255, 255, 255, 128))
        surface.resize(320, 180)
        surface.set_layers(QSize(64, 36), (
            GpuPreviewLayer("base", base, QRectF(0, 0, 64, 36)),
            GpuPreviewLayer("overlay", overlay, QRectF(8, 6, 16, 12)),
        ))
        surface.show()
        QTest.qWait(250)
        if not surface.ready:
            surface.close()
            self.skipTest(failures[0] if failures else "OpenGL context unavailable")
        self.assertEqual(set(surface._textures), {"base", "overlay"})
        self.assertTrue(all(
            entry.texture.isCreated() for entry in surface._textures.values()
        ))
        initial_uploads = surface.upload_stats.texture_uploads
        surface.set_layers(QSize(64, 36), (
            GpuPreviewLayer("base", base, QRectF(0, 0, 64, 36)),
            GpuPreviewLayer("overlay", overlay, QRectF(8, 6, 16, 12)),
        ))
        QTest.qWait(100)
        self.assertEqual(surface.upload_stats.texture_uploads, initial_uploads)
        self.assertGreaterEqual(surface.upload_stats.texture_reuses, 2)
        frame = surface.grabFramebuffer()
        self.assertFalse(frame.isNull())
        self.assertNotEqual(
            frame.pixelColor(60, 50), frame.pixelColor(10, 10),
        )
        surface.close()
        surface.deleteLater()

    @unittest.skipUnless(
        GPU_TEXTURE_SURFACE_AVAILABLE, "Qt OpenGL widgets are unavailable",
    )
    def test_same_size_dynamic_layer_updates_existing_texture_storage(self) -> None:
        surface = GpuTexturePreviewSurface()
        failures: list[str] = []
        surface.backend_failed.connect(failures.append)
        first = QImage(32, 18, QImage.Format.Format_RGBA8888)
        first.fill(QColor("#AA2233"))
        second = QImage(32, 18, QImage.Format.Format_RGBA8888)
        second.fill(QColor("#2266CC"))
        target = QRectF(0, 0, 32, 18)
        surface.resize(320, 180)
        surface.set_layers(
            QSize(32, 18), (GpuPreviewLayer("dynamic", first, target),),
        )
        surface.show()
        QTest.qWait(150)
        if not surface.ready:
            surface.close()
            self.skipTest(failures[0] if failures else "OpenGL context unavailable")
        texture = surface._textures["dynamic"].texture
        initial_uploads = surface.upload_stats.texture_uploads

        surface.set_layers(
            QSize(32, 18), (GpuPreviewLayer("dynamic", second, target),),
        )
        QTest.qWait(150)

        self.assertIs(surface._textures["dynamic"].texture, texture)
        self.assertEqual(surface.upload_stats.texture_uploads, initial_uploads + 1)
        self.assertFalse(failures)
        surface.close()
        surface.deleteLater()

    @unittest.skipUnless(
        GPU_TEXTURE_SURFACE_AVAILABLE, "Qt OpenGL widgets are unavailable",
    )
    def test_texture_cache_evicts_inactive_layer_when_budget_is_exceeded(self) -> None:
        surface = GpuTexturePreviewSurface(texture_budget_bytes=1024 * 1024)
        first = QImage(512, 512, QImage.Format.Format_RGBA8888)
        first.fill(QColor("#223344"))
        second = QImage(512, 512, QImage.Format.Format_RGBA8888)
        second.fill(QColor("#445566"))
        target = QRectF(0, 0, 512, 512)
        surface.resize(256, 256)
        surface.set_layers(
            QSize(512, 512), (GpuPreviewLayer("first", first, target),),
        )
        surface.show()
        QTest.qWait(150)
        if not surface.ready:
            surface.close()
            self.skipTest("OpenGL context unavailable")
        self.assertEqual(set(surface._textures), {"first"})

        surface.set_layers(
            QSize(512, 512), (GpuPreviewLayer("second", second, target),),
        )
        QTest.qWait(150)
        self.assertEqual(set(surface._textures), {"second"})
        self.assertGreaterEqual(surface.upload_stats.texture_evictions, 1)
        self.assertLessEqual(
            surface.upload_stats.allocated_bytes,
            surface.upload_stats.texture_budget_bytes,
        )
        surface.close()
        surface.deleteLater()


if __name__ == "__main__":
    unittest.main()
