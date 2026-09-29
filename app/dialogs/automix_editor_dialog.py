"""AutoMix editor: set each transition by hand on a two-track timeline and hear just that window.

Independent of Preview. The editor keeps its own plan: every committed edit
(one undo step) is stored in the project at once (``override_changed``) and
re-planned on the UI thread with ``compile_automix`` -- CPU only, so a new
length or cue moves the following transitions and their tempo-ramp floors
right away -- while ``TransitionAuditionController`` renders only the
selected window in the background. Nothing here renders the playlist; Export
and Preview plan the same overrides with the same planner and DSP.
"""

from __future__ import annotations

import html
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSplitter, QToolButton, QVBoxLayout,
)

from app.automix.overrides import pair_key
from app.automix.planner import compile_automix
from app.automix.renderer import BAND_ENVELOPES, band_windows_of, transition_dsp_style
from app.automix.settings import automix_settings_for
from app.controllers.transition_audition_controller import (
    FAILED, READY, RENDERING, UNAVAILABLE, WAITING, TransitionAuditionController,
)
from app.widgets.automix_timeline import AutoMixTimeline
from app.widgets.transition_editor import (
    STYLE_CHOICES, EditContext, TransitionPropertiesPanel, bars_text, drag_override, edited_override,
    override_from_junction, style_label,
)
from app.widgets.transition_inspector import INCOMING_COLOR, OUTGOING_COLOR, _clock, plan_junctions

_SETTINGS_KEY = "automix_editor/advanced"
AUDITION_ACTIVITY = "automix_audition"
_COPIED_FIELDS = ("style", "duration", "tempo_match", "vocal_handoff", "eq_bands",
                  "echo_beats", "echo_feedback", "echo_low_cut", "tape_entry", "key_shift", "ramp_seconds")
"""What paste and presets carry to another transition: how it mixes, never where (cues belong to the songs)."""
_PRESETS_KEY = "automix_editor/presets"


class _OverrideCommand(QUndoCommand):
    """One edit of one junction: undo puts the previous override (or automatic) back."""

    def __init__(self, editor: "AutoMixEditorDialog", key: str, before, after, text: str) -> None:
        super().__init__(text)
        self.editor, self.key, self.before, self.after = editor, key, before, after

    def redo(self) -> None:
        self.editor._store(self.key, self.after)

    def undo(self) -> None:
        self.editor._store(self.key, self.before)


def _executable(window) -> Path | None:
    from app.renderer.ffmpeg_renderer import FFmpegNotFoundError, FFmpegRenderer

    settings = getattr(window, "settings_service", None)
    if settings is None:
        return None
    try:
        return FFmpegRenderer(settings.current.ffmpeg_path or None).executable
    except FFmpegNotFoundError:
        return None


class AutoMixEditorDialog(QDialog):
    """See module docstring."""

    override_changed = Signal(str, object)
    """A junction was set by hand (pair key, TransitionOverride) or put back on automatic (key, None)."""

    def __init__(self, window, pair: tuple[str, str] | None = None, executable: Path | None = None) -> None:
        super().__init__(window)
        self.host = window
        self.setObjectName("automixEditor")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(760, 520)
        self.resize(1320, 820)
        self.korean = bool(getattr(getattr(window, "translator", None), "is_korean", True))
        self._tracks = [track for track in window.playlist_service.tracks if track.enabled]
        self._settings = automix_settings_for(window.project_settings)
        self._overrides = dict(self._settings.overrides)
        self._changed_keys: set[str] = set()
        self._plan = None
        self._junctions = []
        self._index = -1
        self._drag_base = None
        self._drag_override = None
        self._copied = None
        self._finished = False
        self._audio: tuple[str, float, float, float] | None = None
        """(file, origin, start, end): see _on_audio_ready."""
        self._playhead: float | None = None
        self._player = None
        self._pending_position: float | None = None
        self._play_when_ready = False
        self._audition_state = ("idle", "")
        self.undo_stack = QUndoStack(self)
        self.audition = TransitionAuditionController(
            executable if executable is not None else _executable(window), self)
        self.audition.state_changed.connect(self._on_audition_state)
        self.audition.audio_ready.connect(self._on_audio_ready)
        self.audition.peaks_ready.connect(self._on_peaks)

        self._build()
        self.override_changed.connect(window._set_automix_override)
        self.undo_stack.indexChanged.connect(self._refresh_actions)
        for owner in (getattr(window, "automix_analysis_controller", None),
                      getattr(window, "track_analysis_controller", None)):
            for name in ("analyses_updated", "structures_updated"):
                signal = getattr(owner, name, None)
                if signal is not None:
                    # A bound slot, not a lambda: Qt drops it when this dialog is deleted.
                    signal.connect(self._queue_analysis_arrived)
        playlist_changed = getattr(getattr(window, "playlist_service", None), "playlist_changed", None)
        if playlist_changed is not None:
            playlist_changed.connect(self._playlist_changed)
        self._set_advanced(bool(QSettings().value(_SETTINGS_KEY, False, bool)), save=False)
        self._replan()
        start = next((index for index, junction in enumerate(self._junctions)
                      if (junction.outgoing.track_id, junction.incoming.track_id) == pair), 0)
        self._select(start, refit=True)
        self._retranslate()

    # -- construction ------------------------------------------------------------------

    def _build(self) -> None:
        def tool(text: str = "", checkable: bool = False) -> QToolButton:
            button = QToolButton()
            button.setText(text)
            button.setCheckable(checkable)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            return button

        self.previous_button = tool("‹")
        self.next_button = tool("›")
        self.transition_combo = QComboBox()
        self.transition_combo.setMinimumWidth(180)
        self.transition_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.transition_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.simple_button = QPushButton()
        self.advanced_button = QPushButton()
        self.mode_group = QButtonGroup(self)
        mode_box = QFrame()
        mode_box.setObjectName("transitionModeSwitch")
        mode_layout = QHBoxLayout(mode_box)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(0)
        for button in (self.simple_button, self.advanced_button):
            button.setCheckable(True)
            button.setObjectName("transitionModeButton")
            self.mode_group.addButton(button)
            mode_layout.addWidget(button)
        self.undo_button = tool("↶")
        self.redo_button = tool("↷")
        self.snap_button = tool("", checkable=True)
        self.snap_button.setChecked(True)
        self.zoom_out_button = tool("−")
        self.fit_button = tool()
        self.zoom_in_button = tool("+")
        self.copy_button = tool()
        self.paste_button = tool()
        self.presets_button = tool()
        self.presets_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.presets_menu = QMenu(self.presets_button)
        self.presets_menu.aboutToShow.connect(self._fill_presets_menu)
        self.presets_button.setMenu(self.presets_menu)
        self.properties_button = tool("", checkable=True)
        self.properties_button.setChecked(True)
        self.help_button = tool("?")

        self.previous_button.clicked.connect(lambda: self._user_select(self._index - 1))
        self.next_button.clicked.connect(lambda: self._user_select(self._index + 1))
        self.transition_combo.activated.connect(self._user_select)
        self.simple_button.clicked.connect(lambda: self._set_advanced(False))
        self.advanced_button.clicked.connect(lambda: self._set_advanced(True))
        self.undo_button.clicked.connect(self._undo)
        self.redo_button.clicked.connect(self._redo)
        self.zoom_out_button.clicked.connect(lambda: self.timeline.zoom(1.25))
        self.zoom_in_button.clicked.connect(lambda: self.timeline.zoom(0.8))
        self.fit_button.clicked.connect(self._fit)
        self.copy_button.clicked.connect(self._copy)
        self.paste_button.clicked.connect(self._paste)
        self.properties_button.toggled.connect(self._toggle_properties)
        self.help_button.clicked.connect(self._show_shortcuts)

        toolbar = QFrame()
        toolbar.setObjectName("automixEditorToolbar")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(20, 16, 20, 16)
        bar.setSpacing(8)
        self.editor_title = QLabel("AutoMix")
        self.editor_title.setObjectName("automixEditorTitle")
        bar.addWidget(self.editor_title)
        bar.addSpacing(16)
        for widget in (self.previous_button, self.transition_combo, self.next_button):
            bar.addWidget(widget)
        bar.addSpacing(12)
        bar.addWidget(mode_box)
        bar.addWidget(self.help_button)

        self.tools_bar = QFrame()
        self.tools_bar.setObjectName("automixToolsBar")
        tools_row = QHBoxLayout(self.tools_bar)
        tools_row.setContentsMargins(20, 8, 20, 8)
        tools_row.setSpacing(6)
        for widget in (self.undo_button, self.redo_button, self.copy_button, self.paste_button, self.presets_button):
            tools_row.addWidget(widget)
        tools_row.addStretch(1)
        tools_row.addWidget(self.snap_button)
        tools_row.addSpacing(12)
        for widget in (self.zoom_out_button, self.fit_button, self.zoom_in_button):
            tools_row.addWidget(widget)
        tools_row.addSpacing(12)
        tools_row.addWidget(self.properties_button)

        self.timeline = AutoMixTimeline()
        self.timeline.seek_requested.connect(self._seek)
        self.timeline.drag_started.connect(self._drag_started)
        self.timeline.drag_moved.connect(self._drag_moved)
        self.timeline.drag_finished.connect(self._drag_finished)
        self.timeline.bands_changed.connect(self._bands_changed)
        self.timeline.nudge_requested.connect(self._nudge)
        self.timeline_scroll = QScrollArea()
        self.timeline_scroll.setObjectName("automixTimelineArea")
        self.timeline_scroll.setWidgetResizable(True)
        self.timeline_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.timeline_scroll.setWidget(self.timeline)

        self.properties = TransitionPropertiesPanel()
        self.properties.changed.connect(self._properties_changed)
        self.properties.reset_requested.connect(self._reset)
        self.properties.link_toggled.connect(lambda linked: setattr(self.timeline, "band_linked", linked))
        self.properties_scroll = QScrollArea()
        self.properties_scroll.setObjectName("automixPropertiesArea")
        self.properties_scroll.setWidgetResizable(True)
        self.properties_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.properties_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.properties_scroll.setWidget(self.properties)
        self.properties_scroll.setMinimumWidth(300)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.timeline_scroll)
        self.splitter.addWidget(self.properties_scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setCollapsible(0, False)
        self.splitter.setSizes([980, 330])

        self.play_button = QPushButton()
        self.play_button.setObjectName("previewPlayButton")
        self.play_button.setCheckable(True)
        self.play_button.setMinimumWidth(96)
        self.play_button.toggled.connect(self._toggle_play)
        self.stop_button = tool("■")
        self.stop_button.clicked.connect(self._stop)
        self.loop_button = tool("", checkable=True)
        self.loop_button.setChecked(True)
        self.loop_button.toggled.connect(self._loop_toggled)
        self.compare_button = tool("", checkable=True)
        self.compare_button.toggled.connect(self._compare_toggled)
        self.time_label = QLabel()
        self.time_label.setObjectName("previewTimeLabel")
        self.time_label.setMinimumWidth(150)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(lambda value: self._seek_local(value / 1000.0))
        self.state_label = QLabel()
        self.state_label.setObjectName("automixStateChip")
        self.state_label.setWordWrap(True)
        self.retry_button = QPushButton()
        self.retry_button.clicked.connect(self.audition.retry)
        self.retry_button.hide()
        self.volume_label = QLabel()
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.valueChanged.connect(self._volume_changed)
        transport = QFrame()
        transport.setObjectName("automixTransport")
        transport_layout = QVBoxLayout(transport)
        transport_layout.setContentsMargins(20, 12, 20, 14)
        transport_layout.setSpacing(10)
        progress = QHBoxLayout()
        progress.addWidget(self.position_slider, 1)
        progress.addWidget(self.time_label)
        transport_layout.addLayout(progress)
        row = QHBoxLayout()
        row.setSpacing(8)
        for widget in (self.play_button, self.stop_button, self.loop_button, self.compare_button):
            row.addWidget(widget)
        row.addStretch(1)
        row.addWidget(self.volume_label)
        row.addWidget(self.volume_slider)
        transport_layout.addLayout(row)
        status = QHBoxLayout()
        status.addWidget(self.state_label, 1)
        status.addWidget(self.retry_button)
        self.save_label = QLabel()
        self.save_label.setObjectName("mutedLabel")
        status.addWidget(self.save_label)
        transport_layout.addLayout(status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        layout.addWidget(self.tools_bar)
        layout.addWidget(self.splitter, 1)
        layout.addWidget(transport)

        for sequence, callback in (
            ("Space", lambda: self.play_button.toggle()),
            ("L", self.loop_button.toggle),
            ("B", lambda: self.compare_button.isEnabled() and self.compare_button.toggle()),
            (QKeySequence.StandardKey.Undo, self._undo),
            (QKeySequence.StandardKey.Redo, self._redo),
            ("Ctrl+Shift+Z", self._redo),
            ("Alt+Left", lambda: self._user_select(self._index - 1)),
            ("Alt+Right", lambda: self._user_select(self._index + 1)),
            ("Ctrl+1", lambda: self._set_advanced(False)),
            ("Ctrl+2", lambda: self._set_advanced(True)),
            ("S", self.snap_button.toggle),
            (QKeySequence.StandardKey.Copy, self._copy),
            (QKeySequence.StandardKey.Paste, self._paste),
            ("Ctrl+Backspace", self._reset),
            ("Ctrl+=", lambda: self.timeline.zoom(0.8)),
            ("Ctrl++", lambda: self.timeline.zoom(0.8)),
            ("Ctrl+-", lambda: self.timeline.zoom(1.25)),
            ("Ctrl+0", self._fit),
            ("Home", lambda: self._seek_local(0.0)),
            ("F1", self._show_shortcuts),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)

    # -- plan and selection ----------------------------------------------------------

    def _context(self) -> EditContext:
        return EditContext({track.id: track for track in self._tracks},
                           dict(self.host.automix_analyses), dict(self.host.automix_structures))

    @property
    def junction(self):
        return self._junctions[self._index] if 0 <= self._index < len(self._junctions) else None

    def _key(self, index: int | None = None) -> str:
        index = self._index if index is None else index
        if not 0 <= index < len(self._junctions):
            return ""
        junction = self._junctions[index]
        return pair_key(junction.outgoing.track_id, junction.incoming.track_id)

    def _replan(self) -> None:
        """Re-plan the whole playlist (CPU only): later transitions follow an edit at once."""
        context = self._context()
        self._plan = compile_automix(
            self._tracks, context.analyses, self._settings.with_overrides(self._overrides),
            context.structures, log_diagnostics=False,
        )
        self._junctions = plan_junctions(self._plan)
        self.timeline.duration = self._plan.duration_seconds
        self._fill_combo()

    def _fill_combo(self) -> None:
        self.transition_combo.blockSignals(True)
        self.transition_combo.clear()
        titles = {track.id: track.title or track.filename for track in self._tracks}
        for index, junction in enumerate(self._junctions):
            manual = "✎ " if self._key(index) in self._overrides else ""
            self.transition_combo.addItem(
                f"{manual}{junction.index:02d} → {junction.index + 1:02d}   "
                f"{titles.get(junction.outgoing.track_id, '')} → {titles.get(junction.incoming.track_id, '')}")
        self.transition_combo.setCurrentIndex(self._index)
        self.transition_combo.blockSignals(False)

    def select_pair(self, outgoing_track_id: str, incoming_track_id: str) -> bool:
        """Select the junction between two tracks (e.g. the playlist's transition chip)."""
        for index, junction in enumerate(self._junctions):
            if (junction.outgoing.track_id, junction.incoming.track_id) == (outgoing_track_id, incoming_track_id):
                self._user_select(index)
                return True
        return False

    def _user_select(self, index: int) -> None:
        if not self._junctions:
            return
        index = min(max(index, 0), len(self._junctions) - 1)
        if index != self._index:
            self._select(index, refit=True)

    def _select(self, index: int, *, refit: bool = False) -> None:
        # A number still being typed belongs to the junction it was typed for.
        self.properties.flush()
        self._index = min(max(index, 0), len(self._junctions) - 1) if self._junctions else -1
        self.transition_combo.setCurrentIndex(self._index)
        if refit:
            self.compare_button.blockSignals(True)
            self.compare_button.setChecked(False)
            self.compare_button.blockSignals(False)
            self._stop()
            self._audio = None
            self.timeline.audition = None
            self._set_playhead(None)
        self._show(refit=refit)

    def _show(self, *, refit: bool = False, drawn=None, drawn_override=None) -> None:
        """Draw the selected junction and fill the side panel (``drawn``: a drag's draft and its override)."""
        junction = self.junction
        context = self._context()
        analyses = context.analyses
        pair = (None, None) if junction is None else (analyses.get(junction.outgoing.track_id),
                                                     analyses.get(junction.incoming.track_id))
        self.timeline.korean = self.korean
        self.timeline.titles = {track.id: track.title or track.filename for track in self._tracks}
        self.timeline.analyses = pair
        self.timeline.draft = drawn is not None
        self.timeline.set_junction(drawn or junction, refit=refit)
        self.previous_button.setEnabled(self._index > 0)
        self.next_button.setEnabled(0 <= self._index < len(self._junctions) - 1)
        self.properties.setVisible(junction is not None)
        if junction is None:
            self.properties.set_state(None, manual=False, pair_text="", songs_html="")
            self._set_audition_text()
            return
        if drawn is None:
            self.audition.load_peaks([track for track in self._tracks
                                      if track.id in (junction.outgoing.track_id, junction.incoming.track_id)])
            self._request_audition()
        self._fill_properties(drawn or junction, pair, drawn_override)
        self._refresh_actions()

    def _fill_properties(self, junction, analyses, override=None) -> None:
        key = self._key()
        manual = key in self._overrides or override is not None
        override = override or self._base()
        titles = {track.id: track.title or track.filename for track in self._tracks}
        durations = tuple(next((t.duration_seconds for t in self._tracks if t.id == clip.track_id), clip.source_out)
                          for clip in (junction.outgoing, junction.incoming))
        transition = junction.transition
        style = transition_dsp_style(transition) if transition is not None else None
        details = dict(transition.details) if transition is not None else {}
        korean = self.korean
        songs = "<br>".join(
            f"<span style='color:{color.name()}'>■</span> <b>{letter}</b>&nbsp; {html.escape(titles.get(clip.track_id, ''))}"
            for color, letter, clip in ((OUTGOING_COLOR, "A", junction.outgoing), (INCOMING_COLOR, "B", junction.incoming)))
        rate = details.get("outgoing_rate")
        tempo = self._text("템포 변화 없음", "No tempo change")
        if analyses[0] is not None and analyses[1] is not None and analyses[0].bpm and analyses[1].bpm:
            tempo = f"A {analyses[0].bpm:.1f} · B {analyses[1].bpm:.1f} BPM"
            if isinstance(rate, (int, float)) and abs(rate - 1.0) > 1e-9:
                ramp = float(details.get("tempo_ramp_seconds") or 0.0)
                tempo += self._text(f" · A를 {analyses[0].bpm * rate:.1f} BPM으로 {ramp:.1f}초 램프",
                                    f" · A ramps to {analyses[0].bpm * rate:.1f} BPM over {ramp:.1f} s")
        length = junction.end - junction.start
        facts = [
            (self._text("믹스 시작", "Mix starts"), _clock(junction.start, precise=True)),
            (self._text("믹스 끝", "Mix ends"), _clock(junction.end, precise=True)),
            (self._text("겹침", "Overlap"), f"{length:.2f} s" + (f" · {b}" if (b := bars_text(length, analyses[1], korean)) else "")
             if length > 0 else self._text("없음 (컷)", "none (cut)")),
            (self._text("A 큐 (원본)", "A cue (source)"), _clock(junction.outgoing.source_at(junction.start), precise=True)),
            (self._text("B 시작 (원본)", "B starts (source)"), _clock(junction.incoming.source_in, precise=True)),
            (self._text("화면 전환", "Canvas switch"), _clock(junction.handover, precise=True)),
        ]
        self.properties.set_state(
            override, manual=manual,
            pair_text=self._text(f"전환 {self._index + 1} / {len(self._junctions)}",
                                 f"Transition {self._index + 1} of {len(self._junctions)}"),
            songs_html=songs, analyses=analyses, durations=durations,
            band_style=style in BAND_ENVELOPES, auto_style=self._style_name(junction), tempo_text=tempo, facts=facts,
        )

    def _style_name(self, junction) -> str:
        transition = junction.transition
        if transition is None:
            return style_label("cut", self.korean)
        if transition.band_windows is not None:
            return style_label("eq", self.korean)
        style = transition_dsp_style(transition)
        return style_label(style.value if style is not None and style.value in STYLE_CHOICES else "legacy",
                           self.korean)

    # -- editing -------------------------------------------------------------------------

    def _base(self):
        return self._overrides.get(self._key()) or override_from_junction(self.junction)

    def _commit(self, override, text: str) -> None:
        key = self._key()
        before = self._overrides.get(key)
        if not key or override is None or override == before:
            return
        self.compare_button.setChecked(False)  # an edit is heard as edited
        self.undo_stack.push(_OverrideCommand(self, key, before, override, text))

    def _request_audition(self) -> None:
        """Audition the selected window: as edited, or -- comparing -- as analysis would mix it."""
        plan = self._plan
        key = self._key()
        comparing = self.compare_button.isChecked() and key in self._overrides
        if comparing:
            context = self._context()
            automatic = {name: value for name, value in self._overrides.items() if name != key}
            plan = compile_automix(self._tracks, context.analyses, self._settings.with_overrides(automatic),
                                   context.structures, log_diagnostics=False)
        self.audition.request(plan, self._index, self._tracks)
        self._set_audition_text()

    def _compare_toggled(self, _checked: bool) -> None:
        if self.junction is not None:
            self._request_audition()

    # -- user presets: how a transition mixes, saved by name -------------------------------

    def _load_presets(self) -> dict:
        import json

        from app.automix.overrides import parse_overrides

        try:
            data = json.loads(str(QSettings().value(_PRESETS_KEY, "{}") or "{}"))
        except ValueError:
            return {}
        # Stored as "preset>name" so parse_overrides' key check and validation apply unchanged.
        return {key[len("preset>"):]: value for key, value in parse_overrides(data).items()
                if key.startswith("preset>")}

    def _save_presets(self, presets: dict) -> None:
        import json

        QSettings().setValue(_PRESETS_KEY, json.dumps(
            {f"preset>{name}": override.to_dict() for name, override in presets.items()}, ensure_ascii=False))

    def _fill_presets_menu(self) -> None:
        menu = self.presets_menu
        menu.clear()
        presets = self._load_presets()
        save = menu.addAction(self._text("현재 설정을 프리셋으로 저장…", "Save these settings as a preset…"))
        save.setEnabled(self.junction is not None)
        save.triggered.connect(self._save_preset)
        if not presets:
            menu.addAction(self._text("저장된 프리셋 없음", "No saved presets")).setEnabled(False)
            return
        menu.addSeparator()
        for name, preset in sorted(presets.items()):
            action = menu.addAction(f"{name}  ·  {style_label(preset.style, self.korean)} {preset.duration:g}s")
            action.setEnabled(self.junction is not None)
            action.triggered.connect(lambda _checked=False, name=name: self._apply_preset(name))
        remove = menu.addMenu(self._text("프리셋 삭제", "Delete a preset"))
        for name in sorted(presets):
            remove.addAction(name).triggered.connect(lambda _checked=False, name=name: self._delete_preset(name))

    def _save_preset(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, accepted = QInputDialog.getText(self, self._text("프리셋 저장", "Save preset"),
                                              self._text("프리셋 이름", "Preset name"))
        name = name.strip()
        if accepted and name:
            self.properties.flush()
            presets = self._load_presets()
            presets[name] = replace(self._base(), outgoing_cue=0.0, incoming_cue=0.0)
            self._save_presets(presets)

    def _apply_preset(self, name: str) -> None:
        preset = self._load_presets().get(name)
        if preset is not None and self.junction is not None:
            self._apply_settings(preset, self._text(f"프리셋 적용: {name}", f"Apply preset: {name}"))

    def _delete_preset(self, name: str) -> None:
        presets = self._load_presets()
        if presets.pop(name, None) is not None:
            self._save_presets(presets)

    def _store(self, key: str, override) -> None:
        """Apply one undo step: the project, the whole plan, the view (selecting what changed)."""
        if override is None:
            self._overrides.pop(key, None)
        else:
            self._overrides[key] = override
        self._changed_keys.add(key)
        self.override_changed.emit(key, override)
        current = self._key()
        self._replan()
        index = next((i for i in range(len(self._junctions)) if self._key(i) == key), None)
        if index is None:
            index = next((i for i in range(len(self._junctions)) if self._key(i) == current), self._index)
        if self._key(index) != current:  # undo/redo of another transition: go and show it
            self._select(index, refit=True)
            return
        self._index = index
        self.transition_combo.setCurrentIndex(self._index)
        self._show()

    def _edit(self, changes: dict, text: str) -> None:
        junction = self.junction
        if junction is None:
            return
        bands = band_windows_of(junction.transition) if junction.transition is not None else None
        try:
            override = edited_override(self._base(), changes, bands)
        except ValueError:
            self._show()
            return
        self._commit(override, text)

    def _bands_changed(self, bands) -> None:
        self.properties.flush()
        self._edit({"eq_bands": bands}, self._text("대역 타이밍", "Band timing"))

    def _properties_changed(self, changes: dict) -> None:
        names = {"style": ("스타일", "Style"), "duration": ("겹침 길이", "Overlap length"),
                 "outgoing_cue": ("A 큐", "A cue"), "incoming_cue": ("B 시작", "B start"),
                 "tempo_match": ("템포 맞춤", "Match tempo"), "vocal_handoff": ("보컬 교대", "Vocal handoff"),
                 "eq_bands": ("대역 타이밍", "Band timing")}
        name = names.get(next(iter(changes), ""), ("편집", "Edit"))
        self._edit(changes, name[0 if self.korean else 1])

    def _reset(self) -> None:
        self.properties.flush()
        key = self._key()
        if key in self._overrides:
            self.undo_stack.push(_OverrideCommand(self, key, self._overrides[key], None,
                                                  self._text("자동으로 되돌리기", "Back to automatic")))

    def _undo(self) -> None:
        self.properties.flush()
        self.undo_stack.undo()

    def _redo(self) -> None:
        self.properties.flush()
        self.undo_stack.redo()

    def _copy(self) -> None:
        if self.junction is not None:
            self._copied = self._base()
            self._refresh_actions()

    def _paste(self) -> None:
        if self._copied is not None and self.junction is not None:
            self._apply_settings(self._copied, self._text("전환 설정 붙여넣기", "Paste transition settings"))

    def _apply_settings(self, source, text: str) -> None:
        """How ``source`` mixes (style, length, tempo, handoff, bands) on the selected transition, keeping its cues."""
        self.properties.flush()
        values = {name: getattr(source, name) for name in _COPIED_FIELDS}
        try:
            override = replace(self._base(), **values)
        except ValueError:
            return
        self._commit(override, text)

    def _nudge(self, part: str, direction: int, fine: bool) -> None:
        """Move one part by a beat of its song (0.05 s with Shift), as one undo step."""
        junction = self.junction
        if junction is None:
            return
        self.properties.flush()
        analyses = self._context().analyses
        analysis = analyses.get((junction.incoming if part in ("end", "incoming") else junction.outgoing).track_id)
        step = 0.05 if fine or analysis is None or not analysis.bpm else 60.0 / analysis.bpm
        override, _hint, _guide = drag_override(
            self._junctions, self._index, junction, self._base(), part, direction * step, self._context(),
            snapping=False, tolerance=0.0, korean=self.korean)
        self._commit(override, self._text("큐 미세 이동", "Nudge"))

    def _drag_started(self, _part: str) -> None:
        self.properties.flush()
        self._drag_base = (self._base(), self.timeline.junction)
        self._drag_override = None

    def _drag_moved(self, part: str, delta: float, free: bool) -> None:
        if self._drag_base is None:
            return
        base, drawn = self._drag_base
        override, hint, guide = drag_override(
            self._junctions, self._index, drawn, base, part, delta, self._context(),
            snapping=self.snap_button.isChecked() != free,
            tolerance=12.0 * self.timeline.seconds_per_pixel(), korean=self.korean)
        self.timeline.drag_hint = hint
        self.timeline.snap_guide = guide
        if override is None or override == self._drag_override:
            self.timeline.update()
            return
        self._drag_override = override
        from app.widgets.transition_editor import draft_junction

        draft = draft_junction(self._junctions, self._index, override, self._context())
        if draft is not None:
            self._show(drawn=draft, drawn_override=override)

    def _drag_finished(self) -> None:
        override, self._drag_override, self._drag_base = self._drag_override, None, None
        if override is None:
            return
        texts = self._text("타임라인 편집", "Timeline edit")
        if override == self._overrides.get(self._key()):
            self._show()
        else:
            self._commit(override, texts)

    # -- audition --------------------------------------------------------------------------

    def _on_audition_state(self, state: str, detail: str) -> None:
        self._audition_state = (state, detail)
        self._set_audition_text()

    def _set_audition_text(self) -> None:
        state, detail = self._audition_state
        if self.junction is None:
            text, tip = self._text("전환 없음", "No transitions"), ""
        elif state == UNAVAILABLE:
            text = self._text("! 미리듣기 불가 · FFmpeg 없음", "! No audition · FFmpeg not found")
            tip = self._text("설정에서 FFmpeg 경로를 지정하면 전환 구간을 들을 수 있습니다.",
                             "Set the FFmpeg path in Settings to hear transitions.")
        elif state == WAITING:
            text = self._text("◐ 변경됨 · 미리듣기 갱신 대기", "◐ Changed · audition update pending")
            tip = self._text("잠시 뒤 이 전환 구간만 다시 렌더합니다. 그동안 이전 버전이 재생됩니다.",
                             "Only this transition's window re-renders shortly; the previous version keeps playing.")
        elif state == RENDERING:
            text = self._text("◐ 구간 렌더 중…", "◐ Rendering window…")
            tip = self._text("선택한 전환의 앞뒤 구간만 렌더합니다. 전체 믹스는 다시 만들지 않습니다.",
                             "Rendering just this transition's window, never the whole mix.")
        elif state == READY and self._audio is not None:
            _path, _origin, start, end = self._audio
            text = self._text(f"✓ 준비됨 · {_clock(start, precise=True)}–{_clock(end, precise=True)}",
                              f"✓ Ready · {_clock(start, precise=True)}–{_clock(end, precise=True)}")
            tip = self._text("최종 내보내기와 같은 계획·DSP로 렌더한 구간입니다.",
                             "Rendered with the same plan and DSP as the final export.")
        elif state == FAILED:
            reason = detail.splitlines()[0] if detail else ""
            reason = reason if len(reason) <= 60 else reason[:59] + "…"
            text = self._text("! 렌더 실패", "! Render failed") + (f" · {reason}" if reason else "")
            tip = detail
        else:
            text, tip = "", ""
        if text and self.compare_button.isChecked():
            text = self._text("자동 버전 재생 · ", "Automatic version · ") + text
        self.state_label.setText(text)
        self.state_label.setToolTip(tip)
        self.state_label.setProperty("state", state)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        self.retry_button.setVisible(state == FAILED)
        playable = self._audio is not None or state in (WAITING, RENDERING, READY)
        self.play_button.setEnabled(playable and state != UNAVAILABLE and self.junction is not None)

    def _on_peaks(self, track_id: str, peaks) -> None:
        self.timeline.peaks[track_id] = peaks
        self.timeline.update()

    def _ensure_player(self):
        if self._player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._audio_output = QAudioOutput(self)
            self._audio_output.setVolume(self.volume_slider.value() / 100.0)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio_output)
            self._player.positionChanged.connect(self._position_changed)
            self._player.mediaStatusChanged.connect(self._media_status)
        return self._player

    def _on_audio_ready(self, path: str, origin: float, start: float, end: float) -> None:
        """Swap in the new window at the same mix position (the old one played until now).

        File second t plays mix second ``origin + t``; ``start..end`` is the window heard and looped.
        """
        playing = self.play_button.isChecked()
        self._audio = (path, origin, start, end)
        self.timeline.audition = (start, end)
        position = self._playhead if self._playhead is not None and start <= self._playhead < end else start
        self._pending_position = position - origin
        self.position_slider.setRange(0, round((end - start) * 1000))
        player = self._ensure_player()
        player.setSource(QUrl.fromLocalFile(path))
        if playing or self._play_when_ready:
            self._play_when_ready = False
            self.play_button.setChecked(True)
            player.play()
        self._set_playhead(position)
        self._set_audition_text()

    def _media_status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) \
                and self._pending_position is not None:
            self._player.setPosition(round(self._pending_position * 1000))
            self._pending_position = None
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.loop_button.isChecked():
                self._seek_local(0.0)
                self._player.play()
            else:
                self.play_button.setChecked(False)
                self._seek_local(0.0)

    def _position_changed(self, milliseconds: int) -> None:
        if self._audio is None:
            return
        _path, origin, start, end = self._audio
        seconds = origin + milliseconds / 1000.0
        if self.loop_button.isChecked() and self.play_button.isChecked() and seconds >= end - 0.03:
            self._seek_local(0.0)
            return
        self._set_playhead(seconds)

    def _set_playhead(self, seconds: float | None) -> None:
        self._playhead = seconds
        self.timeline.playhead = seconds
        self.timeline.update()
        if self._audio is None or seconds is None:
            self.time_label.setText("–:––.– / –:––.–")
            return
        _path, _origin, start, end = self._audio
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(round((seconds - start) * 1000))
        self.time_label.setText(f"{_clock(seconds, precise=True)} / {_clock(end, precise=True)}")

    def _set_play_text(self) -> None:
        self.play_button.setText(("Ⅱ  " + self._text("일시정지", "Pause")) if self.play_button.isChecked()
                                 else ("▶  " + self._text("재생", "Play")))

    def _toggle_play(self, playing: bool) -> None:
        self._set_play_text()
        if self._audio is None:
            if playing:
                self._play_when_ready = True
                self.audition.flush()
            return
        player = self._ensure_player()
        if playing:
            player.play()
        else:
            player.pause()

    def _stop(self) -> None:
        self._play_when_ready = False
        if self.play_button.isChecked():
            self.play_button.setChecked(False)
        if self._player is not None:
            self._player.pause()
        self._seek_local(0.0)

    def _seek(self, seconds: float) -> None:
        """A mix second, held inside the window heard."""
        if self._audio is None:
            return
        _path, _origin, start, end = self._audio
        self._seek_local(min(max(seconds, start), end) - start)

    def _seek_local(self, seconds: float) -> None:
        """Seconds from the window's start."""
        if self._audio is None:
            return
        _path, origin, start, end = self._audio
        seconds = min(max(0.0, seconds), end - start)
        if self._player is not None:
            self._player.setPosition(round((start - origin + seconds) * 1000))
        self._set_playhead(start + seconds)

    def _loop_toggled(self, looping: bool) -> None:
        self.timeline.loop = looping
        self.timeline.update()

    def _volume_changed(self, value: int) -> None:
        if self._player is not None:
            self._audio_output.setVolume(value / 100.0)

    # -- outside changes -------------------------------------------------------------------

    def _queue_analysis_arrived(self, _results=None) -> None:
        # Queued behind MainWindow's own slot, which merges the new results first.
        QTimer.singleShot(0, self, self._analysis_arrived)

    def _analysis_arrived(self) -> None:
        """New analysis re-plans in place: the selected transition and the view stay put."""
        if self._finished:
            return
        key = self._key()
        self._replan()
        self._index = next((i for i in range(len(self._junctions)) if self._key(i) == key),
                           min(self._index, len(self._junctions) - 1))
        self.transition_combo.setCurrentIndex(self._index)
        self._show()

    def _playlist_changed(self) -> None:
        if self._finished:
            return
        self._tracks = [track for track in self.host.playlist_service.tracks if track.enabled]
        self._analysis_arrived()

    # -- chrome -------------------------------------------------------------------------------

    def _text(self, korean: str, english: str) -> str:
        return korean if self.korean else english

    def _set_advanced(self, advanced: bool, *, save: bool = True) -> None:
        """Simple and advanced show the same edit; switching never changes a value."""
        self.properties.flush()
        self.advanced = advanced
        (self.advanced_button if advanced else self.simple_button).setChecked(True)
        self.properties.set_advanced(advanced)
        self.timeline.set_advanced(advanced)
        if save:
            QSettings().setValue(_SETTINGS_KEY, advanced)

    def _toggle_properties(self, visible: bool) -> None:
        self.properties_scroll.setVisible(visible)

    def _fit(self) -> None:
        self.timeline.fit()

    def _refresh_actions(self, _index: int = 0) -> None:
        self.undo_button.setEnabled(self.undo_stack.canUndo())
        self.redo_button.setEnabled(self.undo_stack.canRedo())
        self.undo_button.setToolTip(self._text("실행 취소", "Undo") + (
            f": {self.undo_stack.undoText()}" if self.undo_stack.canUndo() else "") + " (Ctrl+Z)")
        self.redo_button.setToolTip(self._text("다시 실행", "Redo") + (
            f": {self.undo_stack.redoText()}" if self.undo_stack.canRedo() else "") + " (Ctrl+Shift+Z)")
        self.paste_button.setEnabled(self._copied is not None and self.junction is not None)
        self.copy_button.setEnabled(self.junction is not None)
        manual = self._key() in self._overrides
        self.compare_button.setEnabled(manual and self.audition.available)
        if not manual and self.compare_button.isChecked():
            self.compare_button.blockSignals(True)
            self.compare_button.setChecked(False)
            self.compare_button.blockSignals(False)

    def _retranslate(self) -> None:
        korean = self.korean
        self.setWindowTitle(self._text("AutoMix 편집기", "AutoMix editor"))
        self.simple_button.setText(self._text("기본 편집", "Basic"))
        self.advanced_button.setText(self._text("고급", "Advanced"))
        self.simple_button.setToolTip(self._text("스타일·길이·핵심 큐만 (Ctrl+1)", "Style, length and key cues (Ctrl+1)"))
        self.advanced_button.setToolTip(self._text("정밀 큐·템포·대역 레인까지 (Ctrl+2)",
                                                   "Precise cues, tempo and band lanes (Ctrl+2)"))
        self.previous_button.setToolTip(self._text("이전 전환 (Alt+←)", "Previous transition (Alt+Left)"))
        self.next_button.setToolTip(self._text("다음 전환 (Alt+→)", "Next transition (Alt+Right)"))
        self.transition_combo.setToolTip(self._text("편집할 전환 · ✎ 직접 설정한 전환", "Transition to edit · ✎ set by hand"))
        self.snap_button.setText(self._text("박자 스냅", "Snap"))
        self.snap_button.setToolTip(self._text("끌 때 마디·박자에 붙습니다. Shift를 누르면 잠시 반대로 (S)",
                                               "Drags click onto bars and beats; Shift inverts it while held (S)"))
        self.fit_button.setText(self._text("맞춤", "Fit"))
        self.fit_button.setToolTip(self._text("전환 전체가 보이게 (Ctrl+0)", "Show the whole transition (Ctrl+0)"))
        self.zoom_in_button.setToolTip(self._text("확대 (Ctrl+= · Ctrl+휠)", "Zoom in (Ctrl+= · Ctrl+wheel)"))
        self.zoom_out_button.setToolTip(self._text("축소 (Ctrl+-)", "Zoom out (Ctrl+-)"))
        self.copy_button.setText(self._text("복사", "Copy"))
        self.copy_button.setToolTip(self._text("이 전환의 스타일·길이·템포·대역 설정 복사 (Ctrl+C)",
                                               "Copy this transition's style, length, tempo and bands (Ctrl+C)"))
        self.paste_button.setText(self._text("붙여넣기", "Paste"))
        self.paste_button.setToolTip(self._text("복사한 설정을 이 전환에 적용 · 큐 지점은 유지 (Ctrl+V)",
                                                "Apply the copied settings here, keeping this transition's cues (Ctrl+V)"))
        self.presets_button.setText(self._text("프리셋", "Presets"))
        self.presets_button.setToolTip(self._text(
            "스타일·길이·템포·대역 설정을 이름으로 저장하고 다른 전환에 적용합니다 (큐 지점은 유지)",
            "Save style, length, tempo and band settings by name and apply them elsewhere (cues stay)"))
        self.compare_button.setText(self._text("A/B 자동과 비교", "A/B vs automatic"))
        self.compare_button.setToolTip(self._text(
            "켜면 같은 위치에서 분석이 정한 자동 버전을 들려줍니다. 끄면 직접 설정한 버전으로 돌아옵니다 (B)",
            "On: hear what analysis would do here, from the same spot. Off: back to your version (B)"))
        self.properties_button.setText(self._text("속성", "Properties"))
        self.properties_button.setToolTip(self._text("속성 패널 보이기/숨기기", "Show or hide the properties panel"))
        self.help_button.setToolTip(self._text("단축키 (F1)", "Shortcuts (F1)"))
        self.stop_button.setToolTip(self._text("정지 · 구간 처음으로", "Stop · back to the window start"))
        self.loop_button.setText(self._text("구간 반복", "Loop"))
        self.loop_button.setToolTip(self._text("전환 앞뒤 구간을 반복 재생 (L)", "Repeat the transition window (L)"))
        self.play_button.setToolTip(self._text("선택한 전환 구간 재생 / 일시정지 (Space)",
                                               "Play / pause the selected transition window (Space)"))
        self.retry_button.setText(self._text("다시 시도", "Retry"))
        self.volume_label.setText(self._text("음량", "Volume"))
        self.save_label.setText(self._text("변경 사항은 프로젝트에 자동 반영", "Edits apply to the project automatically"))
        self.previous_button.setAccessibleName(self._text("이전 전환", "Previous transition"))
        self.next_button.setAccessibleName(self._text("다음 전환", "Next transition"))
        self.help_button.setAccessibleName(self._text("단축키 도움말", "Keyboard shortcuts"))
        self.volume_slider.setAccessibleName(self._text("미리듣기 음량", "Audition volume"))
        self.position_slider.setAccessibleName(self._text("미리듣기 위치", "Audition position"))
        self.volume_slider.setToolTip(self._text("미리듣기 볼륨", "Audition volume"))
        self.position_slider.setToolTip(self._text("구간 안에서 이동", "Seek within the window"))
        self._set_play_text()
        self.properties.retranslate(korean)
        self.timeline.korean = korean
        self._refresh_actions()
        self._set_audition_text()
        self._set_playhead(self._playhead)

    def _show_shortcuts(self) -> None:
        rows = (
            ("Space", "재생 / 일시정지", "Play / pause"), ("L", "구간 반복", "Loop the window"),
            ("B", "직접 설정 ↔ 자동 버전 비교", "Compare your version with the automatic one"),
            ("Home", "구간 처음으로", "Window start"),
            ("Ctrl+Z · Ctrl+Shift+Z", "실행 취소 · 다시 실행", "Undo · redo"),
            ("Alt+← / →", "이전 / 다음 전환", "Previous / next transition"),
            ("Ctrl+1 / Ctrl+2", "단순 / 고급", "Simple / advanced"),
            ("S · Shift+끌기", "박자 스냅 켜기/끄기 · 잠시 반대로", "Snap on/off · invert while dragging"),
            ("↑ / ↓", "타임라인에서 핸들 선택", "Select a handle on the timeline"),
            ("← / →", "선택한 핸들 한 박자 이동 (Shift: 0.05초)", "Nudge the selected handle a beat (Shift: 0.05 s)"),
            ("Ctrl+휠 · Ctrl+= / - · Ctrl+0", "확대 · 축소 · 맞춤", "Zoom · fit"),
            ("휠 · 가운데 버튼 끌기", "타임라인 좌우 이동", "Scroll the timeline"),
            ("Ctrl+C / Ctrl+V", "전환 설정 복사 / 붙여넣기", "Copy / paste transition settings"),
            ("Ctrl+Backspace", "자동으로 되돌리기", "Back to automatic"),
        )
        text = "<table cellspacing='6'>" + "".join(
            f"<tr><td><b>{html.escape(keys)}</b></td><td>{html.escape(korean if self.korean else english)}</td></tr>"
            for keys, korean, english in rows) + "</table>" + self._text(
            "<p>숫자 입력칸에 커서가 있으면 입력이 먼저입니다.</p>",
            "<p>While a number field has the cursor, typing goes to it.</p>")
        box = QMessageBox(self)
        box.setWindowTitle(self._text("AutoMix 편집기 단축키", "AutoMix editor shortcuts"))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(text)
        box.open()

    # -- closing ------------------------------------------------------------------------------

    def _finish(self) -> None:
        """Commit a pending typed value, stop audio work, and bring an open Preview up to date once."""
        if self._finished:
            return
        self.properties.flush()
        self._finished = True
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        self.audition.shutdown()
        preview = getattr(self.host, "_inline_preview", None)
        if self._changed_keys and preview is not None and getattr(preview, "_transition_mode", "") == "automix":
            # Preview plays what it rendered before this editor opened: re-mix it once, not per edit.
            preview._automix_settings = automix_settings_for(self.host.project_settings)
            preview.remix_automix()

    def done(self, result: int) -> None:
        self._finish()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._finish()
        super().closeEvent(event)
