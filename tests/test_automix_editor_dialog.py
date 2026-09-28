"""The AutoMix editor: its own plan, undo steps, typed values, modes, and what it never touches."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import Mock, patch  # noqa: E402

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.automix.overrides import TransitionOverride, pair_key  # noqa: E402
from app.controllers.preview_controller import PreviewController  # noqa: E402
from app.dialogs.automix_editor_dialog import AutoMixEditorDialog  # noqa: E402
from app.models.project import ProjectSettings  # noqa: E402
from app.utils.i18n import Translator  # noqa: E402
from tests.test_automix_manual_transitions import _analyses, _tracks  # noqa: E402


class AutoMixEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        QSettings().setValue("automix_editor/advanced", False)

    def _host(self, overrides=None):
        host = QWidget()
        self.addCleanup(host.deleteLater)
        tracks = _tracks()
        host.playlist_service = SimpleNamespace(tracks=tracks)
        host.translator = Translator()
        host.project_settings = ProjectSettings(automix_overrides=overrides or {})
        host.automix_analyses = _analyses(tracks)
        host.automix_structures = {}
        host._set_automix_override = Mock()
        return host

    def _editor(self, host=None, pair=("a", "b")):
        host = host or self._host()
        editor = AutoMixEditorDialog(host, pair)
        self.addCleanup(lambda: editor._finished or editor.close())
        return host, editor

    def test_opens_on_the_chip_junction_without_preview_or_any_render(self):
        host = self._host()
        controller = PreviewController(host)
        controller.open_playlist_preview = Mock()
        with patch("app.renderer.ffmpeg_renderer.FFmpegRenderer.prepare_playlist_audio") as full_render:
            controller.edit_transition("b", "c")
            editor = host.findChild(AutoMixEditorDialog)
            self.addCleanup(editor.close)
            self.assertEqual(editor._index, 1)
            controller.open_playlist_preview.assert_not_called()
            full_render.assert_not_called()
        self.assertEqual(editor.audition.render_count, 0)  # no FFmpeg configured: nothing renders
        self.assertEqual(editor.audition.state, "unavailable")
        self.assertFalse(editor.play_button.isEnabled())
        controller.edit_transition()  # the playlist's "AutoMix editor" button: first transition
        self.assertEqual([e._index for e in host.findChildren(AutoMixEditorDialog)][-1], 0)

    def test_an_edit_is_stored_replanned_and_undone_in_one_step(self):
        host, editor = self._editor()
        following = editor._junctions[1].start
        editor.properties.outgoing_spin.setValue(editor.properties.outgoing_spin.value() - 8.0)
        editor.properties.flush()
        key, override = host._set_automix_override.call_args.args
        self.assertEqual(key, pair_key("a", "b"))
        # The whole plan follows at once (CPU only): the next transition moved 8 s earlier.
        self.assertAlmostEqual(editor._junctions[1].start, following - 8.0, places=3)
        self.assertTrue(editor.transition_combo.currentText().startswith("✎"))
        self.assertEqual(editor.undo_stack.count(), 1)
        editor.undo_button.click()
        self.assertEqual(host._set_automix_override.call_args.args, (key, None))
        self.assertAlmostEqual(editor._junctions[1].start, following, places=6)
        editor.redo_button.click()
        self.assertEqual(host._set_automix_override.call_args.args, (key, override))

    def test_a_length_preset_keeps_the_cues(self):
        host, editor = self._editor()
        before = editor._base()
        editor.properties.length_presets[4.0].click()
        after = host._set_automix_override.call_args.args[1]
        self.assertEqual(after.duration, 4.0)
        self.assertAlmostEqual(after.outgoing_cue, before.outgoing_cue, places=6)
        self.assertAlmostEqual(after.incoming_cue, before.incoming_cue, places=6)

    def test_modes_show_the_same_edit_and_switching_changes_nothing(self):
        host, editor = self._editor()
        self.assertTrue(editor.properties.cue_box.isHidden())
        editor._set_advanced(True)
        self.assertFalse(editor.properties.cue_box.isHidden())
        self.assertTrue(editor.timeline.advanced)
        editor.properties.style_buttons["vocal_safe_eq"].click()
        editor.properties.handoff_slider.setValue(65)
        editor.properties.flush()
        edited = editor._overrides[pair_key("a", "b")]
        self.assertEqual(edited.vocal_handoff, 0.65)
        calls = host._set_automix_override.call_count
        editor._set_advanced(False)
        editor._set_advanced(True)
        self.assertEqual(host._set_automix_override.call_count, calls)
        self.assertEqual(editor._overrides[pair_key("a", "b")], edited)

    def test_a_value_being_typed_lands_on_its_own_transition(self):
        host, editor = self._editor()
        editor.properties.length_spin.setValue(5.5)
        self.assertTrue(editor.properties.pending)
        editor.next_button.click()  # moves before the debounce fired
        self.assertEqual(editor._index, 1)
        self.assertEqual(host._set_automix_override.call_args.args[0], pair_key("a", "b"))
        self.assertEqual(host._set_automix_override.call_args.args[1].duration, 5.5)
        self.assertNotIn(pair_key("b", "c"), editor._overrides)

    def test_closing_saves_a_pending_typed_value(self):
        host, editor = self._editor()
        editor.properties.length_spin.setValue(7.0)
        editor.close()
        self.assertEqual(host._set_automix_override.call_args.args[1].duration, 7.0)

    def test_a_drag_is_one_undo_step_and_clicks_onto_the_downbeat(self):
        host, editor = self._editor()
        editor.timeline.resize(1000, 400)
        before = editor._base()
        editor._drag_started("move")
        editor._drag_moved("move", -2.0, False)
        editor._drag_moved("move", -4.2, False)
        self.assertEqual(host._set_automix_override.call_count, 0)  # nothing stored mid-drag
        self.assertTrue(editor.timeline.draft)
        editor._drag_finished()
        self.assertEqual(editor.undo_stack.count(), 1)
        after = host._set_automix_override.call_args.args[1]
        self.assertLess(after.outgoing_cue, before.outgoing_cue)
        self.assertIn(after.outgoing_cue, _analyses(_tracks())["a"].downbeats)
        self.assertFalse(editor.timeline.draft)

    def test_a_drag_stops_at_the_song_end_and_says_why(self):
        _host, editor = self._editor()
        editor.timeline.resize(1000, 400)
        junction = editor.junction
        editor._drag_started("move")
        editor._drag_moved("move", 60.0, True)
        self.assertIn("나가는 곡 끝", editor.timeline.drag_hint)
        drawn = editor.timeline.junction
        self.assertAlmostEqual(drawn.end - drawn.start, junction.end - junction.start, places=3)
        editor._drag_finished()

    def test_nudge_moves_the_selected_handle_by_a_beat(self):
        host, editor = self._editor()
        before = editor._base()
        editor.timeline.selected_part = "start"
        editor.timeline.setFocus()
        QTest.keyClick(editor.timeline, Qt.Key.Key_Left)
        after = host._set_automix_override.call_args.args[1]
        self.assertAlmostEqual(after.outgoing_cue, before.outgoing_cue - 0.5, places=3)  # 120 BPM

    def test_typing_space_in_a_number_field_does_not_start_playback(self):
        _host, editor = self._editor()
        editor._set_advanced(True)
        editor.show()
        spin = editor.properties.outgoing_spin
        spin.setFocus()
        self.app.processEvents()
        with patch.object(editor, "_toggle_play") as toggle:
            QTest.keyClick(spin, Qt.Key.Key_Space)
            QTest.keyClick(spin, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        toggle.assert_not_called()
        self.assertEqual(editor.undo_stack.count(), 0)

    def test_copy_paste_carries_how_it_mixes_not_where(self):
        host, editor = self._editor()
        editor.properties.style_buttons["cut"].click()
        editor._copy()
        editor._user_select(1)
        cues = editor._base()
        editor._paste()
        pasted = host._set_automix_override.call_args.args
        self.assertEqual(pasted[0], pair_key("b", "c"))
        self.assertEqual(pasted[1].style, "cut")
        self.assertEqual((pasted[1].outgoing_cue, pasted[1].incoming_cue), (cues.outgoing_cue, cues.incoming_cue))

    def test_reset_goes_back_to_automatic_and_band_drags_become_custom_eq(self):
        host, editor = self._editor()
        editor.properties.style_buttons["bass_swap"].click()
        bands = (((0.1, 0.3), (0.2, 0.4)),) * 3
        editor._bands_changed(bands)
        self.assertEqual(host._set_automix_override.call_args.args[1].style, "eq")
        self.assertEqual(host._set_automix_override.call_args.args[1].eq_bands, bands)
        editor.properties.reset_button.click()
        self.assertEqual(host._set_automix_override.call_args.args, (pair_key("a", "b"), None))
        self.assertFalse(editor.properties.reset_button.isEnabled())

    def test_new_analysis_keeps_the_selection_and_the_view(self):
        host, editor = self._editor(pair=("b", "c"))
        view = editor.timeline.view()
        host.automix_analyses = {}
        editor._analysis_arrived()
        self.assertEqual(editor._index, 1)
        self.assertEqual(editor.timeline.view(), view)

    def test_closing_re_mixes_an_open_preview_once_and_only_after_an_edit(self):
        host = self._host()
        host._inline_preview = SimpleNamespace(_transition_mode="automix", remix_automix=Mock(),
                                               _automix_settings=None)
        _host, untouched = self._editor(host)
        untouched.close()
        host._inline_preview.remix_automix.assert_not_called()
        _host, editor = self._editor(host)
        editor.properties.length_presets[4.0].click()
        editor.properties.length_presets[16.0].click()
        host.project_settings = ProjectSettings(
            automix_overrides={"a>b": TransitionOverride(150.0).to_dict()})
        editor.close()
        host._inline_preview.remix_automix.assert_called_once()
        self.assertEqual(host._inline_preview._automix_settings.override_for("a", "b"), TransitionOverride(150.0))


if __name__ == "__main__":
    unittest.main()
