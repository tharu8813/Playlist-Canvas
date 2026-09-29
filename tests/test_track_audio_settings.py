"""Per-track volume/EQ: stored on PlaylistTrack, rendered as the source's first filters."""

import shutil
import subprocess
import unittest

from app.automix.renderer import build_filter_graph
from app.models.playlist import PlaylistTrack
from app.timeline.render_plan import AudioRenderClip


class TrackAudioSettingsTests(unittest.TestCase):
    def test_neutral_track_has_no_filter(self) -> None:
        self.assertEqual(PlaylistTrack("a.mp3", "A").audio_filter, "")
        self.assertEqual(PlaylistTrack("a.mp3", "A", eq_db=[0.0] * 5).audio_filter, "")

    def test_filter_covers_shelves_peaks_and_volume(self) -> None:
        track = PlaylistTrack("a.mp3", "A", volume_db=-3.0, eq_db=[4.0, 0.0, -2.0, 0.0, 1.5])
        self.assertEqual(
            track.audio_filter,
            "lowshelf=f=60:g=4.00,equalizer=f=1000:t=o:w=2:g=-2.00,highshelf=f=12000:g=1.50,volume=-3.00dB",
        )

    def test_round_trip_and_validation(self) -> None:
        track = PlaylistTrack("a.mp3", "A", volume_db=2.5, eq_db=[1.0, 2.0])
        restored = PlaylistTrack.from_dict(track.to_dict())
        self.assertEqual((restored.volume_db, restored.eq_db), (2.5, [1.0, 2.0]))
        old = track.to_dict()
        del old["volume_db"], old["eq_db"]  # projects saved before this feature
        self.assertEqual(PlaylistTrack.from_dict(old).audio_filter, "")
        for bad in ({"volume_db": 40.0}, {"volume_db": float("nan")}, {"eq_db": [0.0] * 6}, {"eq_db": [13.0]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PlaylistTrack.from_dict({**track.to_dict(), **bad})

    def test_mix_graph_runs_track_filter_before_the_clip(self) -> None:
        clips = [
            AudioRenderClip("c0", "t0", 0.0, 0.0, 1.0),
            AudioRenderClip("c1", "t1", 1.0, 0.0, 1.0),
        ]
        graph, _label = build_filter_graph(clips, (), track_filters={"t1": "volume=-3.00dB", "t0": ""})
        self.assertIn("[1:a]volume=-3.00dB[src1]", graph)
        self.assertIn("[src1]atrim", graph)
        self.assertIn("[0:a]atrim", graph)

    def test_dialog_saves_volume_and_eq(self) -> None:
        from PySide6.QtWidgets import QApplication

        from app.dialogs.track_details_dialog import TrackDetailsDialog
        from app.utils.i18n import Translator

        _app = QApplication.instance() or QApplication([])
        track = PlaylistTrack("missing.mp3", "A", volume_db=-2.0, eq_db=[3.0])
        dialog = TrackDetailsDialog(track, Translator())
        try:
            self.assertEqual(dialog.track_volume_slider.value(), -4)
            self.assertEqual(dialog.eq_sliders[0].value(), 6)
            dialog.eq_sliders[2].setValue(-5)
            dialog._accept()
            self.assertEqual((dialog.selected_volume_db, dialog.selected_eq_db), (-2.0, [3.0, 0.0, -2.5, 0.0, 0.0]))
            dialog._reset_audio()
            dialog._accept()
            self.assertEqual((dialog.selected_volume_db, dialog.selected_eq_db), (0.0, []))
        finally:
            dialog.deleteLater()

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_dialog_plays_an_eq_rendered_copy_and_can_bypass_it(self) -> None:
        import time
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from PySide6.QtWidgets import QApplication

        from app.dialogs.track_details_dialog import TrackDetailsDialog
        from app.utils.i18n import Translator

        app = QApplication.instance() or QApplication([])
        ffmpeg = Path(shutil.which("ffmpeg"))
        with TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            source = Path(directory) / "tone.wav"
            subprocess.run([str(ffmpeg), "-f", "lavfi", "-t", "1", "-i", "sine=f=440", "-y", str(source)],
                           capture_output=True, check=True)
            dialog = TrackDetailsDialog(
                PlaylistTrack(str(source), "Tone", duration_seconds=1.0), Translator(), ffmpeg_executable=ffmpeg,
            )
            try:
                dialog.eq_sliders[0].setValue(8)
                dialog._eq_timer.stop()
                dialog._update_audio_source()
                self.assertEqual(dialog._eq_state, "rendering")
                deadline = time.monotonic() + 10
                while dialog._eq_state == "rendering" and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.02)
                self.assertEqual(dialog._eq_state, "eq", dialog._eq_error)
                self.assertTrue(dialog.media_player.source().toLocalFile().endswith(".flac"))
                dialog.eq_bypass_check.setChecked(True)
                self.assertEqual(Path(dialog.media_player.source().toLocalFile()), source.resolve())
                self.assertEqual(dialog._eq_state, "original")
            finally:
                dialog.done(0)
                dialog.deleteLater()

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_ffmpeg_accepts_the_filter(self) -> None:
        track = PlaylistTrack("a.mp3", "A", volume_db=6.0, eq_db=[3.0, -3.0, 2.0, -2.0, 4.0])
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-t", "0.2",
             "-i", "sine=f=440", "-af", track.audio_filter, "-f", "null", "-"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
