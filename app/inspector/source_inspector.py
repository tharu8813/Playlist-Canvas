"""Inspector panel for editing the active Source."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models.source import Source, SourceType
from app.services.source_store import SourceStore
from app.utils.font_loader import load_application_font
from app.utils.i18n import Translator


class SourceInspector(QScrollArea):
    """Editable property panel with guarded, two-way SourceStore binding."""

    animation_preview_requested = Signal(str)

    IMAGE_BACKED_TYPES = {
        SourceType.IMAGE,
        SourceType.BACKGROUND,
        SourceType.ALBUM_COVER,
        SourceType.LOGO,
        SourceType.WATERMARK,
    }

    def __init__(self, store: SourceStore, translator: Translator,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.translator = translator
        self._updating = False
        self._applying_batch = False
        self._source_id: str | None = None
        self._source_ids: tuple[str, ...] = ()
        self._mixed_fields: set[str] = set()
        self._dirty_line_fields: set[str] = set()
        self._form_labels: dict[str, QLabel] = {}
        self._field_widgets: dict[str, QWidget] = {}
        self.setMinimumWidth(290)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("sourceInspector")
        self._content = QWidget()
        self._content.setObjectName("inspectorContent")
        self._content.setMinimumWidth(0)
        self._content.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred,
        )
        self.setWidget(self._content)
        self.empty_state = QLabel(self.viewport())
        self.empty_state.setObjectName("inspectorEmptyState")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setWordWrap(True)
        self.empty_state.setContentsMargins(28, 28, 28, 28)
        self.empty_state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        self.title = QLabel("Inspector")
        self.title.setObjectName("panelTitle")
        self.subtitle = QLabel("Select an object to edit its properties.")
        self.subtitle.setObjectName("mutedLabel")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)

        self.content_group = QGroupBox()
        content_form = QFormLayout(self.content_group)
        self.name_edit = QLineEdit()
        self.text_edit = QLineEdit()
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setReadOnly(True)
        self.file_button = QPushButton()
        self.clear_file_button = QPushButton()
        file_row = QWidget()
        file_layout = QHBoxLayout(file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(5)
        file_layout.addWidget(self.file_path_edit, 1)
        file_layout.addWidget(self.file_button)
        file_layout.addWidget(self.clear_file_button)
        self._add_labeled_row(content_form, "name", self.name_edit)
        self._add_labeled_row(content_form, "text", self.text_edit)
        self._add_labeled_row(content_form, "file", file_row)

        self.shape_kind_combo = QComboBox()
        for label, value in (("Rectangle", "rectangle"), ("Circle", "circle"), ("Line", "line")):
            self.shape_kind_combo.addItem(label, value)
        self.progress_style_combo = QComboBox()
        for label, value in (
            ("Rounded", "rounded"), ("Spotify", "spotify"), ("Apple Music", "apple"),
            ("YouTube", "youtube"), ("Gradient", "gradient"),
        ):
            self.progress_style_combo.addItem(label, value)
        self.visualizer_style_combo = QComboBox()
        for label, value in (
            ("Bars", "bars"), ("Wave", "wave"), ("Dots", "dots"),
            ("Line", "line"), ("Mirror", "mirror"), ("Spectrum", "spectrum"),
            ("LED bars", "led"), ("Center bars", "center"), ("Capsules", "capsule"),
            ("Arc", "arc"),
        ):
            self.visualizer_style_combo.addItem(label, value)
        self.visualizer_bars_spin = QSpinBox()
        self.visualizer_bars_spin.setRange(4, 96)
        self.text_alignment_combo = QComboBox()
        for label, value in (("Left", "left"), ("Center", "center"), ("Right", "right")):
            self.text_alignment_combo.addItem(label, value)
        self.text_overflow_combo = QComboBox()
        for label, value in (
            ("Automatic wrap", "wrap"),
            ("Ellipsis (…)", "ellipsis"),
            ("Clip", "clip"),
        ):
            self.text_overflow_combo.addItem(label, value)
        self.image_fit_combo = QComboBox()
        for label, value in (("Cover", "cover"), ("Contain", "contain"), ("Stretch", "stretch")):
            self.image_fit_combo.addItem(label, value)
        self.background_mode_combo = QComboBox()
        for label, value in (("Color / gradient", "color"), ("Image", "image"),
                             ("Current album cover", "album_art")):
            self.background_mode_combo.addItem(label, value)
        self.background_ambient_check = QCheckBox()
        self.progress_value_spin = self._spin(0, 1, 0.01)
        self.progress_track_color_button = self._color_button()
        self.progress_mode_combo = QComboBox()
        for label, value in (("Current track", "track"), ("Whole video", "video")):
            self.progress_mode_combo.addItem(label, value)
        self.visualizer_line_width_spin = self._spin(1, 30, 0.5)
        self.visualizer_sensitivity_spin = self._spin(0.25, 3.0, 0.05)
        self.visualizer_reactivity_spin = self._spin(0.05, 0.8, 0.05)
        self.visualizer_noise_gate_spin = self._spin(0.0, 0.1, 0.001)
        self.visualizer_noise_gate_spin.setDecimals(3)
        self.visualizer_min_level_spin = self._spin(0.0, 0.5, 0.01)
        self.visualizer_max_level_spin = self._spin(0.1, 1.0, 0.01)
        self.visualizer_attack_spin = self._spin(0.01, 1.0, 0.05)
        self.visualizer_release_spin = self._spin(0.01, 1.0, 0.05)
        self.visualizer_smoothing_spin = self._spin(0.0, 1.0, 0.05)
        self.visualizer_curve_spin = self._spin(0.25, 3.0, 0.05)
        self.album_frame_combo = QComboBox()
        for label, value in (("Rounded", "rounded"), ("Circle", "circle"), ("Polaroid", "polaroid"), ("Glass", "glass")):
            self.album_frame_combo.addItem(label, value)
        self.track_list_count_spin = QSpinBox()
        self.track_list_count_spin.setRange(1, 15)
        self.track_list_style_combo = QComboBox()
        for label, value in (
            ("Compact", "compact"), ("Cards", "cards"), ("Queue", "queue"),
            ("Minimal", "minimal"), ("Scroll / fade", "scroll"),
            ("Glass", "glass"), ("Pills", "pills"),
        ):
            self.track_list_style_combo.addItem(label, value)
        self.track_list_window_combo = QComboBox()
        for label, value in (
            ("Previous + current + next", "centered"),
            ("Current + upcoming", "upcoming"),
            ("History + current", "history"),
        ):
            self.track_list_window_combo.addItem(label, value)
        self.track_list_show_number_check = QCheckBox()
        self.track_list_show_artist_check = QCheckBox()
        self.track_list_show_album_check = QCheckBox()
        self.track_list_marker_combo = QComboBox()
        for label, value in (
            ("Play ▶", "play"), ("Dot ●", "dot"),
            ("Accent ▌", "line"), ("None", "none"),
        ):
            self.track_list_marker_combo.addItem(label, value)
        self.track_list_row_spacing_spin = self._spin(0, 40, 1)
        self.track_list_item_padding_spin = self._spin(0, 40, 1)
        self.track_list_current_color_button = self._color_button()
        self.track_list_inactive_color_button = self._color_button()
        self.track_list_current_background_button = self._color_button()
        self.track_list_inactive_opacity_spin = self._spin(0.05, 1.0, 0.05)
        self.track_list_current_scale_spin = self._spin(0.8, 1.5, 0.05)
        self.track_list_show_dividers_check = QCheckBox()
        self.now_playing_style_combo = QComboBox()
        for label, value in (("Card", "card"), ("Minimal", "minimal"), ("Glass", "glass")):
            self.now_playing_style_combo.addItem(label, value)
        self.now_playing_duration_spin = self._spin(0.5, 15, 0.25)
        self.now_playing_exit_combo = QComboBox()
        for label, value in (("Fade", "fade"), ("Slide up", "slide_up"), ("Slide down", "slide_down"), ("Zoom", "zoom")):
            self.now_playing_exit_combo.addItem(label, value)
        self.now_playing_exit_duration_spin = self._spin(0.05, 3.0, 0.05)
        self.subtitle_style_combo = QComboBox()
        for label, value in (("Karaoke", "karaoke"), ("Minimal", "minimal"), ("Neon", "neon")):
            self.subtitle_style_combo.addItem(label, value)
        self.subtitle_animation_combo = QComboBox()
        for label, value in (
            ("Soft focus", "apple_music"), ("Smooth slide", "spotify"),
            ("Blur reveal", "blur_reveal"), ("Fade", "fade"),
            ("Scroll up", "scroll_up"), ("Scroll down", "scroll_down"),
            ("Pop", "pop"), ("None", "none"),
        ):
            self.subtitle_animation_combo.addItem(label, value)
        self.subtitle_animation_duration_spin = self._spin(0.05, 1.5, 0.05)
        self.subtitle_context_lines_spin = QSpinBox()
        self.subtitle_context_lines_spin.setRange(0, 6)
        self.subtitle_next_lines_spin = QSpinBox()
        self.subtitle_next_lines_spin.setRange(0, 6)
        self.subtitle_line_spacing_spin = self._spin(0, 120, 1)
        self.subtitle_previous_opacity_spin = self._spin(0.05, 0.9, 0.05)
        self.subtitle_previous_blur_spin = self._spin(0, 8, 0.5)
        self.subtitle_timing_offset_spin = self._spin(-5.0, 5.0, 0.01)
        self.waveform_style_combo = QComboBox()
        for label, value in (("Line", "line"), ("Filled", "filled"), ("Mirror", "mirror")):
            self.waveform_style_combo.addItem(label, value)
        self.level_meter_mode_combo = QComboBox()
        for label, value in (("Stereo", "stereo"), ("Mono", "mono")):
            self.level_meter_mode_combo.addItem(label, value)
        self.level_meter_style_combo = QComboBox()
        for label, value in (
            ("Gradient", "gradient"), ("Solid", "solid"), ("LED", "led"),
            ("Segments", "segments"),
        ):
            self.level_meter_style_combo.addItem(label, value)
        self.level_meter_orientation_combo = QComboBox()
        for label, value in (("Vertical", "vertical"), ("Horizontal", "horizontal")):
            self.level_meter_orientation_combo.addItem(label, value)
        self.level_meter_sensitivity_spin = self._spin(0.25, 4.0, 0.05)
        self.level_meter_attack_spin = self._spin(0.01, 1.0, 0.05)
        self.level_meter_release_spin = self._spin(0.01, 1.0, 0.05)
        self.level_meter_min_level_spin = self._spin(0.0, 0.5, 0.01)
        self.level_meter_max_level_spin = self._spin(0.1, 1.0, 0.01)
        self.level_meter_segments_spin = QSpinBox()
        self.level_meter_segments_spin.setRange(3, 64)
        self.level_meter_gap_spin = self._spin(0.0, 30.0, 0.5)
        self.level_meter_show_peak_check = QCheckBox()
        self.level_meter_peak_hold_spin = self._spin(0.0, 3.0, 0.05)
        self.level_meter_peak_decay_spin = self._spin(0.05, 3.0, 0.05)
        self.level_meter_track_color_button = self._color_button()
        self.level_meter_low_color_button = self._color_button()
        self.level_meter_mid_color_button = self._color_button()
        self.level_meter_high_color_button = self._color_button()
        self.particle_style_combo = QComboBox()
        for label, value in (
            ("Dust", "dust"), ("Neon", "neon"), ("Noise", "noise"),
            ("Snow", "snow"), ("Stars", "stars"), ("Bokeh", "bokeh"),
            ("Confetti", "confetti"),
        ):
            self.particle_style_combo.addItem(label, value)
        self.particle_density_spin = QSpinBox()
        self.particle_density_spin.setRange(4, 500)
        self.particle_speed_spin = self._spin(0.0, 5.0, 0.1)
        self.particle_min_size_spin = self._spin(0.5, 40.0, 0.5)
        self.particle_max_size_spin = self._spin(0.5, 80.0, 0.5)
        self.particle_opacity_spin = self._spin(0.0, 1.0, 0.05)
        self.particle_direction_spin = self._spin(-180.0, 180.0, 5.0)
        self.particle_drift_spin = self._spin(0.0, 2.0, 0.05)
        self.particle_twinkle_spin = self._spin(0.0, 1.0, 0.05)
        self.particle_glow_spin = self._spin(0.0, 1.0, 0.05)
        self.particle_secondary_color_button = self._color_button()
        self.particle_seed_spin = QSpinBox()
        self.particle_seed_spin.setRange(0, 999_999)
        self._add_labeled_row(content_form, "shape", self.shape_kind_combo)
        self._add_labeled_row(content_form, "progress_style", self.progress_style_combo)
        self._add_labeled_row(content_form, "visualizer_style", self.visualizer_style_combo)
        self._add_labeled_row(content_form, "visualizer_bars", self.visualizer_bars_spin)
        self._add_labeled_row(content_form, "text_alignment", self.text_alignment_combo)
        self._add_labeled_row(content_form, "text_overflow", self.text_overflow_combo)
        self._add_labeled_row(content_form, "image_fit", self.image_fit_combo)
        self._add_labeled_row(content_form, "background_mode", self.background_mode_combo)
        self._add_labeled_row(content_form, "background_ambient", self.background_ambient_check)
        self._add_labeled_row(content_form, "progress_value", self.progress_value_spin)
        self._add_labeled_row(content_form, "progress_track_color", self.progress_track_color_button)
        self._add_labeled_row(content_form, "progress_mode", self.progress_mode_combo)
        self._add_labeled_row(content_form, "visualizer_line_width", self.visualizer_line_width_spin)
        self._add_labeled_row(content_form, "visualizer_sensitivity", self.visualizer_sensitivity_spin)
        self._add_labeled_row(content_form, "visualizer_reactivity", self.visualizer_reactivity_spin)
        self._add_labeled_row(content_form, "visualizer_noise_gate", self.visualizer_noise_gate_spin)
        self._add_labeled_row(content_form, "visualizer_min_level", self.visualizer_min_level_spin)
        self._add_labeled_row(content_form, "visualizer_max_level", self.visualizer_max_level_spin)
        self._add_labeled_row(content_form, "visualizer_attack", self.visualizer_attack_spin)
        self._add_labeled_row(content_form, "visualizer_release", self.visualizer_release_spin)
        self._add_labeled_row(content_form, "visualizer_smoothing", self.visualizer_smoothing_spin)
        self._add_labeled_row(content_form, "visualizer_curve", self.visualizer_curve_spin)
        self._add_labeled_row(content_form, "album_frame", self.album_frame_combo)
        self._add_labeled_row(content_form, "track_list_count", self.track_list_count_spin)
        self._add_labeled_row(content_form, "track_list_style", self.track_list_style_combo)
        self._add_labeled_row(content_form, "track_list_window", self.track_list_window_combo)
        self._add_labeled_row(content_form, "track_list_show_number", self.track_list_show_number_check)
        self._add_labeled_row(content_form, "track_list_show_artist", self.track_list_show_artist_check)
        self._add_labeled_row(content_form, "track_list_show_album", self.track_list_show_album_check)
        self._add_labeled_row(content_form, "track_list_marker", self.track_list_marker_combo)
        self._add_labeled_row(content_form, "track_list_row_spacing", self.track_list_row_spacing_spin)
        self._add_labeled_row(content_form, "track_list_item_padding", self.track_list_item_padding_spin)
        self._add_labeled_row(content_form, "track_list_current_color", self.track_list_current_color_button)
        self._add_labeled_row(content_form, "track_list_inactive_color", self.track_list_inactive_color_button)
        self._add_labeled_row(content_form, "track_list_current_background", self.track_list_current_background_button)
        self._add_labeled_row(content_form, "track_list_inactive_opacity", self.track_list_inactive_opacity_spin)
        self._add_labeled_row(content_form, "track_list_current_scale", self.track_list_current_scale_spin)
        self._add_labeled_row(content_form, "track_list_show_dividers", self.track_list_show_dividers_check)
        self._add_labeled_row(content_form, "now_playing_style", self.now_playing_style_combo)
        self._add_labeled_row(content_form, "now_playing_duration", self.now_playing_duration_spin)
        self._add_labeled_row(content_form, "now_playing_exit", self.now_playing_exit_combo)
        self._add_labeled_row(content_form, "now_playing_exit_duration", self.now_playing_exit_duration_spin)
        self._add_labeled_row(content_form, "subtitle_style", self.subtitle_style_combo)
        self._add_labeled_row(content_form, "subtitle_animation", self.subtitle_animation_combo)
        self._add_labeled_row(content_form, "subtitle_animation_duration", self.subtitle_animation_duration_spin)
        self._add_labeled_row(content_form, "subtitle_context_lines", self.subtitle_context_lines_spin)
        self._add_labeled_row(content_form, "subtitle_next_lines", self.subtitle_next_lines_spin)
        self._add_labeled_row(content_form, "subtitle_line_spacing", self.subtitle_line_spacing_spin)
        self._add_labeled_row(content_form, "subtitle_previous_opacity", self.subtitle_previous_opacity_spin)
        self._add_labeled_row(content_form, "subtitle_previous_blur", self.subtitle_previous_blur_spin)
        self._add_labeled_row(content_form, "subtitle_timing_offset", self.subtitle_timing_offset_spin)
        self._add_labeled_row(content_form, "waveform_style", self.waveform_style_combo)
        self._add_labeled_row(content_form, "level_meter_mode", self.level_meter_mode_combo)
        self._add_labeled_row(content_form, "level_meter_style", self.level_meter_style_combo)
        self._add_labeled_row(content_form, "level_meter_orientation", self.level_meter_orientation_combo)
        self._add_labeled_row(content_form, "level_meter_sensitivity", self.level_meter_sensitivity_spin)
        self._add_labeled_row(content_form, "level_meter_attack", self.level_meter_attack_spin)
        self._add_labeled_row(content_form, "level_meter_release", self.level_meter_release_spin)
        self._add_labeled_row(content_form, "level_meter_min_level", self.level_meter_min_level_spin)
        self._add_labeled_row(content_form, "level_meter_max_level", self.level_meter_max_level_spin)
        self._add_labeled_row(content_form, "level_meter_segments", self.level_meter_segments_spin)
        self._add_labeled_row(content_form, "level_meter_gap", self.level_meter_gap_spin)
        self._add_labeled_row(content_form, "level_meter_show_peak", self.level_meter_show_peak_check)
        self._add_labeled_row(content_form, "level_meter_peak_hold", self.level_meter_peak_hold_spin)
        self._add_labeled_row(content_form, "level_meter_peak_decay", self.level_meter_peak_decay_spin)
        self._add_labeled_row(content_form, "level_meter_track_color", self.level_meter_track_color_button)
        self._add_labeled_row(content_form, "level_meter_low_color", self.level_meter_low_color_button)
        self._add_labeled_row(content_form, "level_meter_mid_color", self.level_meter_mid_color_button)
        self._add_labeled_row(content_form, "level_meter_high_color", self.level_meter_high_color_button)
        self._add_labeled_row(content_form, "particle_style", self.particle_style_combo)
        self._add_labeled_row(content_form, "particle_density", self.particle_density_spin)
        self._add_labeled_row(content_form, "particle_speed", self.particle_speed_spin)
        self._add_labeled_row(content_form, "particle_min_size", self.particle_min_size_spin)
        self._add_labeled_row(content_form, "particle_max_size", self.particle_max_size_spin)
        self._add_labeled_row(content_form, "particle_opacity", self.particle_opacity_spin)
        self._add_labeled_row(content_form, "particle_direction", self.particle_direction_spin)
        self._add_labeled_row(content_form, "particle_drift", self.particle_drift_spin)
        self._add_labeled_row(content_form, "particle_twinkle", self.particle_twinkle_spin)
        self._add_labeled_row(content_form, "particle_glow", self.particle_glow_spin)
        self._add_labeled_row(
            content_form, "particle_secondary_color", self.particle_secondary_color_button,
        )
        self._add_labeled_row(content_form, "particle_seed", self.particle_seed_spin)
        layout.addWidget(self.content_group)

        self.transform_group = QGroupBox()
        transform = QFormLayout(self.transform_group)
        self.x_spin = self._spin(-5000, 5000, 1)
        self.y_spin = self._spin(-5000, 5000, 1)
        self.width_spin = self._spin(32, 5000, 1)
        self.height_spin = self._spin(24, 5000, 1)
        self.rotation_spin = self._spin(-360, 360, 1)
        self.scale_spin = self._spin(0.1, 10, 0.05)
        for key, widget in (
            ("x", self.x_spin), ("y", self.y_spin), ("width", self.width_spin),
            ("height", self.height_spin), ("rotation", self.rotation_spin),
            ("scale", self.scale_spin),
        ):
            self._add_labeled_row(transform, key, widget)
        layout.addWidget(self.transform_group)

        self.appearance_group = QGroupBox()
        appearance = QFormLayout(self.appearance_group)
        self.opacity_spin = self._spin(0, 1, 0.05)
        self.radius_spin = self._spin(0, 300, 1)
        self.outline_spin = self._spin(0, 40, 1)
        self.font_size_spin = self._spin(8, 120, 1)
        self.font_family_combo = QComboBox()
        self.font_family_combo.setEditable(True)
        self.font_family_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.font_family_combo.addItems(sorted(QFontDatabase.families(), key=str.casefold))
        self.font_add_button = QPushButton()
        font_row = QWidget()
        font_layout = QHBoxLayout(font_row)
        font_layout.setContentsMargins(0, 0, 0, 0)
        font_layout.setSpacing(5)
        font_layout.addWidget(self.font_family_combo, 1)
        font_layout.addWidget(self.font_add_button)
        self.fill_color_button = self._color_button()
        self.outline_color_button = self._color_button()
        self.gradient_check = QCheckBox()
        self.gradient_start_button = self._color_button()
        self.gradient_end_button = self._color_button()
        self.blur_spin = self._spin(0, 40, 1)
        self.brightness_spin = self._spin(-100, 100, 1)
        self.contrast_spin = self._spin(-100, 100, 1)
        self.shadow_check = QCheckBox()
        self.shadow_color_button = self._color_button()
        self.shadow_opacity_spin = self._spin(0, 1, 0.05)
        self.shadow_blur_spin = self._spin(0, 50, 1)
        self.shadow_x_spin = self._spin(-100, 100, 1)
        self.shadow_y_spin = self._spin(-100, 100, 1)
        self.animation_in_combo = QComboBox()
        self.animation_out_combo = QComboBox()
        for combo in (self.animation_in_combo, self.animation_out_combo):
            for label, value in (("None", "none"), ("Fade", "fade"), ("Slide left", "slide_left"),
                                 ("Slide right", "slide_right"), ("Slide up", "slide_up"),
                                 ("Slide down", "slide_down"), ("Zoom", "zoom")):
                combo.addItem(label, value)
        self.animation_in_duration_spin = self._spin(0.1, 3, 0.05)
        self.animation_out_duration_spin = self._spin(0.1, 3, 0.05)
        self.animation_preview_button = QPushButton()
        self.animation_preview_button.setObjectName("primaryButton")
        self.z_spin = QSpinBox()
        self.z_spin.setRange(-100, 100)
        self.visible_check = QCheckBox()
        self.locked_check = QCheckBox()
        for key, widget in (
            ("opacity", self.opacity_spin), ("border_radius", self.radius_spin),
            ("outline", self.outline_spin), ("font_size", self.font_size_spin),
            ("font_family", font_row),
            ("fill_color", self.fill_color_button), ("outline_color", self.outline_color_button),
            ("gradient", self.gradient_check), ("gradient_start", self.gradient_start_button),
            ("gradient_end", self.gradient_end_button), ("blur", self.blur_spin),
            ("brightness", self.brightness_spin), ("contrast", self.contrast_spin),
            ("shadow", self.shadow_check), ("shadow_color", self.shadow_color_button),
            ("shadow_opacity", self.shadow_opacity_spin), ("shadow_blur", self.shadow_blur_spin),
            ("shadow_x", self.shadow_x_spin), ("shadow_y", self.shadow_y_spin),
            ("animation_in", self.animation_in_combo),
            ("animation_in_duration", self.animation_in_duration_spin),
            ("animation_out", self.animation_out_combo),
            ("animation_out_duration", self.animation_out_duration_spin),
            ("layer", self.z_spin),
        ):
            self._add_labeled_row(appearance, key, widget)
        appearance.addRow(self.visible_check)
        appearance.addRow(self.locked_check)
        appearance.addRow("", self.animation_preview_button)
        layout.addWidget(self.appearance_group)
        layout.addStretch()

        self._editors = [
            self.name_edit, self.text_edit, self.file_path_edit, self.file_button,
            self.clear_file_button, self.shape_kind_combo, self.progress_style_combo,
            self.visualizer_style_combo, self.visualizer_bars_spin, self.x_spin, self.y_spin,
            self.text_alignment_combo, self.text_overflow_combo,
            self.image_fit_combo, self.progress_value_spin,
            self.progress_mode_combo,
            self.background_mode_combo, self.background_ambient_check,
            self.progress_track_color_button, self.visualizer_line_width_spin,
            self.visualizer_sensitivity_spin, self.visualizer_reactivity_spin,
            self.visualizer_noise_gate_spin, self.visualizer_min_level_spin,
            self.visualizer_max_level_spin, self.visualizer_attack_spin,
            self.visualizer_release_spin, self.visualizer_smoothing_spin,
            self.visualizer_curve_spin,
            self.album_frame_combo, self.track_list_count_spin, self.track_list_style_combo,
            self.track_list_window_combo, self.track_list_show_number_check,
            self.track_list_show_artist_check, self.track_list_show_album_check,
            self.track_list_marker_combo, self.track_list_row_spacing_spin,
            self.track_list_item_padding_spin, self.track_list_current_color_button,
            self.track_list_inactive_color_button, self.track_list_current_background_button,
            self.track_list_inactive_opacity_spin, self.track_list_current_scale_spin,
            self.track_list_show_dividers_check,
            self.now_playing_style_combo, self.now_playing_duration_spin,
            self.now_playing_exit_combo, self.now_playing_exit_duration_spin,
            self.subtitle_style_combo, self.subtitle_animation_combo, self.subtitle_animation_duration_spin,
            self.subtitle_context_lines_spin, self.subtitle_next_lines_spin, self.subtitle_line_spacing_spin,
            self.subtitle_previous_opacity_spin, self.subtitle_previous_blur_spin,
            self.subtitle_timing_offset_spin,
            self.waveform_style_combo, self.level_meter_mode_combo,
            self.level_meter_style_combo, self.level_meter_orientation_combo,
            self.level_meter_sensitivity_spin, self.level_meter_attack_spin,
            self.level_meter_release_spin, self.level_meter_min_level_spin,
            self.level_meter_max_level_spin, self.level_meter_segments_spin,
            self.level_meter_gap_spin, self.level_meter_show_peak_check,
            self.level_meter_peak_hold_spin, self.level_meter_peak_decay_spin,
            self.level_meter_track_color_button, self.level_meter_low_color_button,
            self.level_meter_mid_color_button, self.level_meter_high_color_button,
            self.particle_style_combo,
            self.particle_density_spin, self.particle_speed_spin,
            self.particle_min_size_spin, self.particle_max_size_spin,
            self.particle_opacity_spin, self.particle_direction_spin,
            self.particle_drift_spin, self.particle_twinkle_spin,
            self.particle_glow_spin, self.particle_secondary_color_button,
            self.particle_seed_spin,
            self.width_spin, self.height_spin, self.rotation_spin, self.scale_spin,
            self.opacity_spin, self.radius_spin, self.outline_spin, self.font_size_spin,
            self.font_family_combo, self.font_add_button,
            self.fill_color_button, self.outline_color_button, self.gradient_check,
            self.gradient_start_button, self.gradient_end_button, self.z_spin,
            self.blur_spin, self.brightness_spin, self.contrast_spin, self.shadow_check,
            self.shadow_color_button, self.shadow_opacity_spin, self.shadow_blur_spin,
            self.shadow_x_spin, self.shadow_y_spin,
            self.animation_in_combo, self.animation_in_duration_spin,
            self.animation_out_combo, self.animation_out_duration_spin,
            self.animation_preview_button,
            self.visible_check, self.locked_check,
        ]
        for form in self._content.findChildren(QFormLayout):
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setLabelAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        for widget in self._field_widgets.values():
            widget.setMinimumWidth(0)
            if widget.sizePolicy().horizontalPolicy() in {
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum,
            }:
                widget.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    widget.sizePolicy().verticalPolicy(),
                )
        self._connect_fields()
        self._set_enabled(False)
        self._update_source_specific_fields(None)
        self._show_empty_state(True)
        store.selection_set_changed.connect(self.set_sources)
        store.source_changed.connect(self.refresh_source)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()

    def _add_labeled_row(self, layout: QFormLayout, key: str, widget: QWidget) -> None:
        label = QLabel()
        self._form_labels[key] = label
        self._field_widgets[key] = widget
        layout.addRow(label, widget)

    def _property_help_text(self, key: str) -> str:
        """Return localized, user-facing guidance for one Inspector property."""
        korean = self.translator.language.value == "ko"
        common = {
            "name": ("레이어와 캔버스에서 이 요소를 구분하는 이름입니다. 영상에는 직접 표시되지 않습니다.", "Identifies this source in Layers and on the Canvas. It is not rendered into the video."),
            "text": ("표시할 문구입니다. %title%, %artist%, %album% 같은 토큰은 재생 중인 곡 정보로 자동 교체됩니다.", "Text to display. Tokens such as %title%, %artist%, and %album% are replaced with current-track data."),
            "file": ("이 요소에 사용할 이미지 파일입니다. 프로젝트를 다른 PC로 옮길 때는 포함 저장을 권장합니다.", "Image used by this source. Embedded project storage is recommended when moving the project to another PC."),
            "shape": ("사각형, 원, 선 중 캔버스에 그릴 도형의 기본 형태를 선택합니다.", "Chooses whether this source is drawn as a rectangle, circle, or line."),
            "progress_style": ("진행 바의 모서리와 채움 형태를 미리 정의된 디자인으로 변경합니다.", "Changes the progress bar's corners and fill treatment using a preset design."),
            "text_alignment": ("요소 영역 안에서 텍스트를 왼쪽, 가운데 또는 오른쪽으로 정렬합니다.", "Aligns text left, center, or right inside the source box."),
            "text_overflow": ("영역보다 긴 문장을 줄바꿈할지, 말줄임표로 줄일지, 영역 밖을 자를지 정합니다.", "Chooses whether long text wraps, ends with an ellipsis, or is clipped to the source box."),
            "image_fit": ("원본 비율을 유지하며 채우기, 전체 이미지 맞추기 또는 영역에 늘이기 중 하나를 선택합니다.", "Chooses cover, contain, or stretch behavior for the image inside its source box."),
            "background_mode": ("단색·그라데이션, 지정 이미지 또는 현재 앨범 커버를 배경으로 사용합니다.", "Uses a color/gradient, selected image, or current album artwork as the background."),
            "background_ambient": ("앨범 커버를 확대하고 흐리게 처리해 캔버스를 채우는 앰비언트 배경을 만듭니다.", "Expands and blurs album artwork to create an ambient full-Canvas background."),
            "progress_value": ("편집 화면에서 확인할 진행 비율입니다. 실제 미리보기와 내보내기에서는 재생 시간으로 자동 계산됩니다.", "Preview progress used while editing. Playback and export calculate it automatically from time."),
            "progress_track_color": ("아직 재생되지 않은 진행 바 뒷부분의 색상입니다.", "Color of the unplayed track behind the filled progress portion."),
            "progress_mode": ("현재 곡의 진행 시간 또는 전체 영상의 진행 시간을 기준으로 채웁니다.", "Fills according to either current-track time or complete-video time."),
            "album_frame": ("앨범 커버의 잘림 형태와 테두리 느낌을 선택합니다.", "Chooses the crop shape and frame treatment for album artwork."),
            "waveform_style": ("파형을 선, 채운 면 또는 위아래 대칭 형태로 표시합니다.", "Displays the waveform as a line, filled area, or mirrored shape."),
            "x": ("캔버스 왼쪽에서 요소 왼쪽 가장자리까지의 가로 위치입니다. 값이 커지면 오른쪽으로 이동합니다.", "Horizontal position from the Canvas left edge. Larger values move the source right."),
            "y": ("캔버스 위쪽에서 요소 위쪽 가장자리까지의 세로 위치입니다. 값이 커지면 아래로 이동합니다.", "Vertical position from the Canvas top edge. Larger values move the source down."),
            "width": ("배율 적용 전 요소 영역의 너비입니다. 이미지와 텍스트의 배치 영역에도 영향을 줍니다.", "Source-box width before scale is applied. It also affects image and text layout."),
            "height": ("배율 적용 전 요소 영역의 높이입니다. 이미지와 텍스트의 배치 영역에도 영향을 줍니다.", "Source-box height before scale is applied. It also affects image and text layout."),
            "rotation": ("요소를 시계 방향으로 회전하는 각도입니다. 음수 값은 반시계 방향입니다.", "Clockwise rotation in degrees. Negative values rotate counter-clockwise."),
            "scale": ("너비와 높이를 함께 확대하거나 축소하는 배율입니다. 1은 원래 크기입니다.", "Uniformly enlarges or shrinks width and height. A value of 1 is the original size."),
            "opacity": ("요소 전체의 불투명도입니다. 0은 완전히 투명하고 1은 완전히 보입니다.", "Overall source opacity. 0 is fully transparent and 1 is fully visible."),
            "border_radius": ("사각형 모서리를 둥글게 만드는 반경입니다. 값이 클수록 더 둥글어집니다.", "Rounds rectangular corners. Larger values produce rounder corners."),
            "outline": ("요소 가장자리에 그리는 윤곽선의 두께입니다. 0이면 표시하지 않습니다.", "Width of the outline drawn around the source. Set to 0 to hide it."),
            "font_size": ("텍스트의 기준 글꼴 크기입니다. 요소 크기와 배율은 별도로 적용됩니다.", "Base text size. Source dimensions and scale are applied separately."),
            "font_family": ("텍스트에 사용할 글꼴입니다. 글꼴 추가 버튼으로 TTF 또는 OTF 파일을 등록할 수 있습니다.", "Font used for text. Add Font can register a TTF or OTF file."),
            "fill_color": ("도형, 텍스트 또는 효과의 주 색상입니다. 색상 창에서 알파를 0으로 설정하면 요소 전체 투명도는 유지하면서 배경만 완전히 투명하게 만들 수 있습니다.", "Primary fill or background color. Set alpha to 0 in the color dialog to make the background fully transparent without changing overall source opacity."),
            "outline_color": ("윤곽선에 사용할 색상입니다. 윤곽선 두께가 0보다 클 때 보입니다.", "Outline color, visible when outline width is greater than zero."),
            "gradient": ("단색 대신 시작 색과 끝 색이 이어지는 그라데이션 채우기를 사용합니다.", "Uses a blend between start and end colors instead of a solid fill."),
            "gradient_start": ("그라데이션이 시작되는 쪽의 색상입니다.", "Color at the start of the gradient."),
            "gradient_end": ("그라데이션이 끝나는 쪽의 색상입니다.", "Color at the end of the gradient."),
            "blur": ("이미지를 부드럽게 흐립니다. 높은 값은 미리보기와 렌더링 부하를 늘릴 수 있습니다.", "Softens the image. High values can increase preview and rendering cost."),
            "brightness": ("이미지를 어둡게 또는 밝게 보정합니다. 0은 원본 밝기입니다.", "Darkens or brightens the image. 0 preserves original brightness."),
            "contrast": ("밝고 어두운 영역의 차이를 줄이거나 강조합니다. 0은 원본 대비입니다.", "Reduces or emphasizes differences between light and dark areas. 0 preserves the original."),
            "shadow": ("요소 뒤에 그림자를 표시해 배경과 분리된 깊이감을 만듭니다.", "Draws a shadow behind the source to separate it from the background."),
            "shadow_color": ("그림자에 사용할 색상입니다.", "Color used for the shadow."),
            "shadow_opacity": ("그림자의 불투명도입니다. 값이 작을수록 은은해집니다.", "Shadow opacity. Smaller values make it subtler."),
            "shadow_blur": ("그림자 가장자리의 퍼짐 정도입니다. 값이 클수록 부드럽고 넓게 퍼집니다.", "Softness and spread of shadow edges. Larger values make a wider, softer shadow."),
            "shadow_x": ("그림자를 가로로 이동합니다. 양수는 오른쪽, 음수는 왼쪽입니다.", "Horizontal shadow offset. Positive moves right; negative moves left."),
            "shadow_y": ("그림자를 세로로 이동합니다. 양수는 아래, 음수는 위입니다.", "Vertical shadow offset. Positive moves down; negative moves up."),
            "animation_in": ("곡에서 이 요소가 나타날 때 재생할 등장 효과입니다.", "Entrance effect played when this source appears during a track."),
            "animation_out": ("곡에서 이 요소가 사라질 때 재생할 종료 효과입니다.", "Exit effect played when this source disappears during a track."),
            "animation_in_duration": ("곡 시작 애니메이션이 재생되는 시간입니다.", "Duration of the track-start animation."),
            "animation_out_duration": ("곡 종료 애니메이션이 재생되는 시간입니다.", "Duration of the track-end animation."),
            "layer": ("요소의 쌓임 순서입니다. 값이 큰 요소가 값이 작은 요소 위에 표시됩니다.", "Stacking order. Sources with larger values are drawn above sources with smaller values."),
        }
        if key in common:
            return common[key][0 if korean else 1]

        families = {
            "visualizer_": ("오디오 비주얼라이저", "audio visualizer"),
            "track_list_": ("트랙 목록", "track list"),
            "now_playing_": ("현재 재생 카드", "now-playing card"),
            "subtitle_": ("가사", "lyrics"),
            "level_meter_": ("오디오 레벨 미터", "audio level meter"),
            "particle_": ("파티클 효과", "particle effect"),
        }
        prefix = next((entry for entry in families if key.startswith(entry)), "")
        suffix = key[len(prefix):] if prefix else key
        details = {
            "style": ("표현 디자인을 선택합니다. 데이터와 타이밍은 유지되고 모양만 바뀝니다.", "Chooses the visual design while preserving data and timing."),
            "bars": ("표시할 막대 또는 점의 개수입니다. 많을수록 세밀하지만 렌더링 부하가 늘어납니다.", "Number of bars or dots. More detail can increase rendering cost."),
            "line_width": ("선을 그리는 두께입니다. 값이 클수록 효과가 굵고 강하게 보입니다.", "Stroke width. Larger values make the effect heavier and stronger."),
            "sensitivity": ("오디오 입력을 증폭하는 정도입니다. 값이 크면 작은 소리에도 크게 반응합니다.", "Audio-input gain. Larger values react more strongly to quiet sound."),
            "reactivity": ("오디오 변화에 따라 움직이는 민감도입니다. 높을수록 움직임이 빠르고 역동적입니다.", "Movement response to audio changes. Higher values feel faster and more dynamic."),
            "noise_gate": ("이 값보다 작은 입력은 무음으로 처리합니다. 0이면 게이트를 사용하지 않습니다.", "Treats input below this value as silence. Set to 0 to disable the gate."),
            "min_level": ("입력이 작거나 무음일 때 유지할 최소 표시 높이입니다.", "Minimum displayed level for quiet input or silence."),
            "max_level": ("가장 큰 입력에서 사용할 최대 표시 높이입니다.", "Maximum displayed level at the loudest input."),
            "attack": ("소리가 커질 때 표시가 상승하는 속도입니다. 높을수록 피크를 빠르게 따라갑니다.", "How quickly the display rises with louder sound. Higher values follow peaks faster."),
            "release": ("소리가 작아질 때 표시가 내려오는 속도입니다. 낮추면 움직임이 더 오래 남습니다.", "How quickly the display falls as sound gets quieter. Lower values linger longer."),
            "smoothing": ("인접한 주파수 구간의 높이 차이를 평균화해 움직임을 부드럽게 합니다.", "Averages neighboring frequency bands for smoother movement."),
            "curve": ("작은 소리와 큰 소리 중 어느 영역의 움직임을 더 강조할지 조정합니다.", "Balances emphasis between quiet detail and loud peaks."),
            "count": ("화면에 동시에 표시할 항목 수입니다.", "Number of entries displayed at the same time."),
            "window": ("현재 항목을 기준으로 이전 항목과 다음 항목을 어떤 비율로 보여줄지 정합니다.", "Chooses how previous and upcoming entries are arranged around the current item."),
            "show_number": ("각 곡 앞에 플레이리스트 순번을 표시합니다.", "Shows the playlist position before each track."),
            "show_artist": ("트랙 목록에 아티스트 이름을 함께 표시합니다.", "Shows artist names in the track list."),
            "show_album": ("트랙 목록에 앨범 이름을 함께 표시합니다.", "Shows album names in the track list."),
            "marker": ("재생 중인 곡을 알아보기 위한 아이콘 또는 강조선을 선택합니다.", "Chooses an icon or accent that identifies the current track."),
            "row_spacing": ("목록의 각 행 사이 간격입니다. 값이 크면 목록이 더 넓게 펼쳐집니다.", "Space between rows. Larger values spread the list farther apart."),
            "item_padding": ("각 목록 항목의 글자와 배경 사이 안쪽 여백입니다.", "Inner space between each row's text and background."),
            "current_color": ("현재 재생 중인 곡의 글자색입니다.", "Text color for the currently playing track."),
            "inactive_color": ("현재 곡을 제외한 다른 곡의 글자색입니다.", "Text color for tracks other than the current one."),
            "current_background": ("현재 곡 뒤에 표시할 강조 배경색입니다.", "Highlight background behind the current track."),
            "inactive_opacity": ("현재 곡이 아닌 항목을 흐리게 표시하는 정도입니다.", "Controls how faint non-current entries appear."),
            "current_scale": ("현재 곡만 확대하거나 축소하는 배율입니다. 1은 원래 크기입니다.", "Scale applied only to the current track. A value of 1 is original size."),
            "show_dividers": ("목록의 각 행 사이에 구분선을 표시합니다.", "Shows divider lines between list rows."),
            "duration": ("카드 또는 전환이 화면에 유지되는 시간입니다.", "How long the card or transition remains on screen."),
            "exit": ("카드가 사라질 때 사용할 전환 효과입니다.", "Transition used when the card disappears."),
            "exit_duration": ("사라짐 효과가 완료되는 데 걸리는 시간입니다.", "Time required for the exit effect to complete."),
            "animation": ("현재 가사 줄이 바뀔 때 사용할 전환 효과입니다.", "Transition used when the active lyric line changes."),
            "animation_duration": ("가사 줄 전환 효과가 재생되는 시간입니다.", "Duration of the lyric-line transition."),
            "context_lines": ("현재 줄 위에 함께 표시할 이전 가사 줄 수입니다.", "Number of previous lyric lines shown above the current line."),
            "next_lines": ("현재 줄 아래에 미리 표시할 다음 가사 줄 수입니다.", "Number of upcoming lyric lines shown below the current line."),
            "line_spacing": ("가사 줄과 줄 사이의 세로 간격입니다.", "Vertical spacing between lyric lines."),
            "previous_opacity": ("지나간 가사 줄을 얼마나 흐리게 표시할지 정합니다.", "Controls how faint previous lyric lines appear."),
            "previous_blur": ("지나간 가사 줄에 적용할 흐림 정도입니다.", "Blur applied to previous lyric lines."),
            "timing_offset": ("모든 곡의 가사를 초 단위로 앞당기거나 늦추는 공통 보정입니다. 곡별 보정값과 합산됩니다.", "Global timing adjustment for lyrics on every track. It is added to each track's individual offset."),
            "mode": ("스테레오 채널을 나눠 표시하거나 하나의 모노 신호로 합칠지 선택합니다.", "Chooses separate stereo channels or one combined mono signal."),
            "orientation": ("미터가 세로로 상승할지 가로로 진행할지 선택합니다.", "Chooses whether the meter rises vertically or progresses horizontally."),
            "segments": ("분할형 미터에 표시할 칸의 개수입니다. 많을수록 변화가 세밀합니다.", "Number of blocks in a segmented meter. More blocks show finer changes."),
            "gap": ("스테레오 두 채널 사이의 간격입니다.", "Space between the two stereo channels."),
            "show_peak": ("최근 가장 큰 레벨 위치를 피크 표시선으로 유지합니다.", "Keeps a marker at the most recent maximum level."),
            "peak_hold": ("피크 표시선이 내려가기 전에 현재 위치를 유지하는 시간입니다.", "Time the peak marker stays in place before falling."),
            "peak_decay": ("유지 시간이 끝난 뒤 피크 표시선이 내려오는 속도입니다.", "Speed at which the peak marker falls after its hold time."),
            "track_color": ("신호가 없는 미터 배경 영역의 색상입니다.", "Color of the inactive meter track."),
            "low_color": ("낮은 음량 구간에 사용할 색상입니다.", "Color used for low audio levels."),
            "mid_color": ("중간 음량 구간에 사용할 색상입니다.", "Color used for medium audio levels."),
            "high_color": ("높은 음량과 피크 구간에 사용할 색상입니다.", "Color used for high levels and peaks."),
            "density": ("화면에 동시에 나타나는 파티클 수입니다. 높은 값은 렌더링 부하를 늘릴 수 있습니다.", "Number of particles on screen. High values can increase rendering cost."),
            "speed": ("파티클이 이동하는 기본 속도입니다. 0이면 위치 변화가 멈춥니다.", "Base particle movement speed. Set to 0 to stop positional movement."),
            "min_size": ("무작위로 생성되는 파티클의 최소 크기입니다.", "Minimum size of randomly generated particles."),
            "max_size": ("무작위로 생성되는 파티클의 최대 크기입니다. 최소 크기보다 작게 설정되지 않습니다.", "Maximum random particle size; it cannot be smaller than the minimum."),
            "opacity": ("효과 전체가 보이는 정도입니다. 0은 완전히 투명하고 1은 완전히 보입니다.", "Overall effect opacity. 0 is fully transparent and 1 is fully visible."),
            "direction": ("파티클이 이동하는 기준 각도입니다. 0°는 오른쪽, 90°는 아래쪽입니다.", "Base movement angle. 0° is right and 90° is down."),
            "drift": ("기본 이동 방향에서 좌우로 흔들리는 무작위 움직임의 강도입니다.", "Strength of random side-to-side movement away from the base direction."),
            "twinkle": ("파티클 밝기가 시간에 따라 반짝이는 정도입니다.", "Amount of brightness variation over time."),
            "glow": ("파티클 주변의 빛 번짐 강도입니다. 높은 값은 렌더링 부하를 늘릴 수 있습니다.", "Glow around particles. High values can increase rendering cost."),
            "secondary_color": ("주 채우기 색과 섞어서 사용할 두 번째 파티클 색상입니다.", "Second particle color mixed with the primary fill color."),
            "seed": ("파티클의 초기 배치를 결정합니다. 값을 바꾸면 같은 설정으로 새 배치를 만듭니다.", "Determines initial particle placement. Change it for a new layout with the same settings."),
        }
        detail = details.get(suffix)
        if detail is None:
            return (
                "이 요소 전용 속성입니다. 값을 변경하면 캔버스와 미리보기에 즉시 반영됩니다."
                if korean else
                "This is a source-specific property. Changes appear immediately on the Canvas and in Preview."
            )
        family = families[prefix][0 if korean else 1] if prefix else ""
        explanation = detail[0 if korean else 1]
        return f"{family} 설정입니다. {explanation}" if korean else f"{family.capitalize()} setting. {explanation}"

    def _install_property_tooltips(self) -> None:
        """Apply rich, localized hover help to labels and their editor controls."""
        korean = self.translator.language.value == "ko"
        for key, label in self._form_labels.items():
            widget = self._field_widgets[key]
            description = self._property_help_text(key)
            range_text = ""
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                minimum = f"{widget.minimum():g}"
                maximum = f"{widget.maximum():g}"
                step = f"{widget.singleStep():g}"
                range_text = (
                    f"<br><br><b>범위</b> {minimum}–{maximum} &nbsp;·&nbsp; <b>조절 단위</b> {step}"
                    if korean else
                    f"<br><br><b>Range</b> {minimum}–{maximum} &nbsp;·&nbsp; <b>Step</b> {step}"
                )
            tooltip = (
                f"<div style='width: 340px'><b>{label.text()}</b><br>"
                f"{description}{range_text}</div>"
            )
            label.setToolTip(tooltip)
            label.setToolTipDuration(15_000)
            widget.setToolTip(tooltip)
            widget.setToolTipDuration(15_000)
            widget.setAccessibleDescription(description)
            for child in widget.findChildren(QWidget):
                child.setToolTip(tooltip)
                child.setToolTipDuration(15_000)

        standalone = {
            self.visible_check: (
                "요소를 삭제하지 않고 캔버스와 최종 영상에서 숨깁니다."
                if korean else "Hides the source from the Canvas and final video without deleting it."
            ),
            self.locked_check: (
                "실수로 이동하거나 크기를 바꾸지 않도록 캔버스 편집을 잠급니다. 속성에서는 잠금을 해제할 수 있습니다."
                if korean else "Prevents accidental Canvas movement or resizing. It can still be unlocked here."
            ),
        }
        for widget, description in standalone.items():
            widget.setToolTip(f"<div style='width: 340px'><b>{widget.text()}</b><br>{description}</div>")
            widget.setToolTipDuration(15_000)
            widget.setAccessibleDescription(description)

    def _set_field_visible(self, key: str, visible: bool) -> None:
        self._form_labels[key].setVisible(visible)
        self._field_widgets[key].setVisible(visible)

    def _update_source_specific_fields(self, source: Source | None) -> None:
        source_type = source.source_type if source else None
        is_background = source_type is SourceType.BACKGROUND
        text_types = {
            SourceType.TEXT, SourceType.TIME, SourceType.LYRICS,
            SourceType.TRACK_LIST, SourceType.NOW_PLAYING,
        }
        self._set_field_visible("text", source_type in text_types)
        self._set_field_visible("font_size", source_type in text_types)
        self._set_field_visible(
            "font_family",
            source_type in text_types,
        )
        self._set_field_visible(
            "file", source_type in self.IMAGE_BACKED_TYPES
            and (not is_background or (source is not None and source.background_mode == "image"))
        )
        self._set_field_visible("shape", source_type is SourceType.SHAPE)
        self._set_field_visible("progress_style", source_type is SourceType.PROGRESS_BAR)
        self._set_field_visible("visualizer_style", source_type is SourceType.AUDIO_VISUALIZER)
        self._set_field_visible("visualizer_bars", source_type is SourceType.AUDIO_VISUALIZER)
        self._set_field_visible("visualizer_line_width", source_type is SourceType.AUDIO_VISUALIZER)
        self._set_field_visible("visualizer_sensitivity", source_type is SourceType.AUDIO_VISUALIZER)
        self._set_field_visible("visualizer_reactivity", source_type is SourceType.AUDIO_VISUALIZER)
        for key in (
            "visualizer_noise_gate", "visualizer_min_level", "visualizer_max_level",
            "visualizer_attack", "visualizer_release", "visualizer_smoothing",
            "visualizer_curve",
        ):
            self._set_field_visible(key, source_type is SourceType.AUDIO_VISUALIZER)
        self._set_field_visible("text_alignment", source_type in {SourceType.TEXT, SourceType.TIME, SourceType.LYRICS, SourceType.TRACK_LIST, SourceType.NOW_PLAYING})
        self._set_field_visible(
            "text_overflow", source_type in {SourceType.TEXT, SourceType.TRACK_LIST}
        )
        self._set_field_visible("image_fit", source_type in self.IMAGE_BACKED_TYPES)
        self._set_field_visible("background_mode", is_background)
        self._set_field_visible(
            "background_ambient", is_background and source is not None
            and source.background_mode == "album_art"
        )
        self._set_field_visible("progress_value", source_type is SourceType.PROGRESS_BAR)
        self._set_field_visible("progress_track_color", source_type is SourceType.PROGRESS_BAR)
        self._set_field_visible("progress_mode", source_type is SourceType.PROGRESS_BAR)
        self._set_field_visible("album_frame", source_type is SourceType.ALBUM_COVER)
        for key in (
            "track_list_count", "track_list_style", "track_list_window",
            "track_list_show_number", "track_list_show_artist",
            "track_list_show_album", "track_list_marker", "track_list_row_spacing",
            "track_list_item_padding", "track_list_current_color",
            "track_list_inactive_color", "track_list_current_background",
            "track_list_inactive_opacity", "track_list_current_scale",
            "track_list_show_dividers",
        ):
            self._set_field_visible(key, source_type is SourceType.TRACK_LIST)
        self._set_field_visible("now_playing_style", source_type is SourceType.NOW_PLAYING)
        self._set_field_visible("now_playing_duration", source_type is SourceType.NOW_PLAYING)
        self._set_field_visible("now_playing_exit", source_type is SourceType.NOW_PLAYING)
        self._set_field_visible("now_playing_exit_duration", source_type is SourceType.NOW_PLAYING)
        self._set_field_visible("subtitle_style", source_type is SourceType.LYRICS)
        self._set_field_visible("subtitle_animation", source_type is SourceType.LYRICS)
        self._set_field_visible("subtitle_animation_duration", source_type is SourceType.LYRICS)
        self._set_field_visible("subtitle_context_lines", source_type is SourceType.LYRICS)
        self._set_field_visible("subtitle_next_lines", source_type is SourceType.LYRICS)
        self._set_field_visible("subtitle_line_spacing", source_type is SourceType.LYRICS)
        self._set_field_visible("subtitle_previous_opacity", source_type is SourceType.LYRICS)
        self._set_field_visible("subtitle_previous_blur", source_type is SourceType.LYRICS)
        self._set_field_visible("subtitle_timing_offset", source_type is SourceType.LYRICS)
        self._set_field_visible("waveform_style", source_type is SourceType.AUDIO_WAVEFORM)
        for key in (
            "level_meter_mode", "level_meter_style", "level_meter_orientation",
            "level_meter_sensitivity", "level_meter_attack", "level_meter_release",
            "level_meter_min_level", "level_meter_max_level", "level_meter_segments",
            "level_meter_gap", "level_meter_show_peak", "level_meter_peak_hold",
            "level_meter_peak_decay", "level_meter_track_color", "level_meter_low_color",
            "level_meter_mid_color", "level_meter_high_color",
        ):
            self._set_field_visible(key, source_type is SourceType.AUDIO_LEVEL_METER)
        for key in (
            "particle_style", "particle_density", "particle_speed",
            "particle_min_size", "particle_max_size", "particle_opacity",
            "particle_direction", "particle_drift", "particle_twinkle",
            "particle_glow", "particle_secondary_color", "particle_seed",
        ):
            self._set_field_visible(key, source_type is SourceType.PARTICLE_OVERLAY)
        for key in ("blur", "brightness", "contrast", "shadow", "shadow_color", "shadow_opacity", "shadow_blur", "shadow_x", "shadow_y"):
            self._set_field_visible(key, source_type in self.IMAGE_BACKED_TYPES)

    @staticmethod
    def _spin(minimum: float, maximum: float, step: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(2)
        spin.setKeyboardTracking(False)
        return spin

    @staticmethod
    def _color_button() -> QPushButton:
        button = QPushButton()
        button.setMinimumWidth(112)
        return button

    def _connect_fields(self) -> None:
        self.name_edit.textEdited.connect(lambda _value: self._dirty_line_fields.add("name"))
        self.text_edit.textEdited.connect(lambda _value: self._dirty_line_fields.add("text"))
        self.name_edit.editingFinished.connect(
            lambda: self._update_line("name", self.name_edit)
        )
        self.text_edit.editingFinished.connect(
            lambda: self._update_line("text", self.text_edit)
        )
        self.file_button.clicked.connect(self._choose_content_file)
        self.clear_file_button.clicked.connect(lambda: self._update("content_path", ""))
        self.shape_kind_combo.currentIndexChanged.connect(
            lambda _index: self._update("shape_kind", self.shape_kind_combo.currentData())
        )
        self.progress_style_combo.currentIndexChanged.connect(
            lambda _index: self._update("progress_style", self.progress_style_combo.currentData())
        )
        self.visualizer_style_combo.currentIndexChanged.connect(
            lambda _index: self._update("visualizer_style", self.visualizer_style_combo.currentData())
        )
        self.visualizer_bars_spin.valueChanged.connect(
            lambda _value: self._update("visualizer_bars", self.visualizer_bars_spin.value())
        )
        self.text_alignment_combo.currentIndexChanged.connect(lambda _index: self._update("text_alignment", self.text_alignment_combo.currentData()))
        self.text_overflow_combo.currentIndexChanged.connect(
            lambda _index: self._update(
                "text_overflow", self.text_overflow_combo.currentData()
            )
        )
        self.image_fit_combo.currentIndexChanged.connect(lambda _index: self._update("image_fit_mode", self.image_fit_combo.currentData()))
        self.background_mode_combo.currentIndexChanged.connect(self._update_background_mode)
        self.background_ambient_check.toggled.connect(lambda value: self._update("background_ambient", value))
        self.progress_value_spin.valueChanged.connect(lambda _value: self._update("progress_value", self.progress_value_spin.value()))
        self.progress_mode_combo.currentIndexChanged.connect(
            lambda _index: self._update("progress_mode", self.progress_mode_combo.currentData())
        )
        self.visualizer_line_width_spin.valueChanged.connect(lambda _value: self._update("visualizer_line_width", self.visualizer_line_width_spin.value()))
        self.visualizer_sensitivity_spin.valueChanged.connect(lambda _value: self._update("visualizer_sensitivity", self.visualizer_sensitivity_spin.value()))
        self.visualizer_reactivity_spin.valueChanged.connect(lambda _value: self._update("visualizer_reactivity", self.visualizer_reactivity_spin.value()))
        for field, widget in (
            ("visualizer_noise_gate", self.visualizer_noise_gate_spin),
            ("visualizer_min_level", self.visualizer_min_level_spin),
            ("visualizer_max_level", self.visualizer_max_level_spin),
            ("visualizer_attack", self.visualizer_attack_spin),
            ("visualizer_release", self.visualizer_release_spin),
            ("visualizer_smoothing", self.visualizer_smoothing_spin),
            ("visualizer_curve", self.visualizer_curve_spin),
        ):
            widget.valueChanged.connect(
                lambda _value, key=field, control=widget: self._update(key, control.value())
            )
        self.album_frame_combo.currentIndexChanged.connect(lambda _index: self._update("album_frame_style", self.album_frame_combo.currentData()))
        self.track_list_count_spin.valueChanged.connect(lambda value: self._update("track_list_count", value))
        self.track_list_style_combo.currentIndexChanged.connect(lambda _index: self._update("track_list_style", self.track_list_style_combo.currentData()))
        self.track_list_window_combo.currentIndexChanged.connect(
            lambda _index: self._update("track_list_window", self.track_list_window_combo.currentData())
        )
        self.track_list_show_number_check.toggled.connect(
            lambda value: self._update("track_list_show_number", value)
        )
        self.track_list_show_artist_check.toggled.connect(
            lambda value: self._update("track_list_show_artist", value)
        )
        self.track_list_show_album_check.toggled.connect(
            lambda value: self._update("track_list_show_album", value)
        )
        self.track_list_marker_combo.currentIndexChanged.connect(
            lambda _index: self._update("track_list_marker", self.track_list_marker_combo.currentData())
        )
        for field, widget in (
            ("track_list_row_spacing", self.track_list_row_spacing_spin),
            ("track_list_item_padding", self.track_list_item_padding_spin),
            ("track_list_inactive_opacity", self.track_list_inactive_opacity_spin),
            ("track_list_current_scale", self.track_list_current_scale_spin),
        ):
            widget.valueChanged.connect(
                lambda _value, key=field, control=widget: self._update(key, control.value())
            )
        self.track_list_show_dividers_check.toggled.connect(
            lambda value: self._update("track_list_show_dividers", value)
        )
        for field, button in (
            ("track_list_current_color", self.track_list_current_color_button),
            ("track_list_inactive_color", self.track_list_inactive_color_button),
            ("track_list_current_background", self.track_list_current_background_button),
        ):
            button.clicked.connect(
                lambda _checked=False, key=field, control=button: self._choose_color(key, control)
            )
        self.now_playing_style_combo.currentIndexChanged.connect(lambda _index: self._update("now_playing_style", self.now_playing_style_combo.currentData()))
        self.now_playing_duration_spin.valueChanged.connect(lambda value: self._update("now_playing_duration", value))
        self.now_playing_exit_combo.currentIndexChanged.connect(lambda _index: self._update("now_playing_exit_animation", self.now_playing_exit_combo.currentData()))
        self.now_playing_exit_duration_spin.valueChanged.connect(lambda value: self._update("now_playing_exit_duration", value))
        self.subtitle_style_combo.currentIndexChanged.connect(lambda _index: self._update("subtitle_style", self.subtitle_style_combo.currentData()))
        self.subtitle_animation_combo.currentIndexChanged.connect(lambda _index: self._update("subtitle_animation", self.subtitle_animation_combo.currentData()))
        self.subtitle_animation_duration_spin.valueChanged.connect(lambda value: self._update("subtitle_animation_duration", value))
        self.subtitle_context_lines_spin.valueChanged.connect(lambda value: self._update("subtitle_context_lines", value))
        self.subtitle_next_lines_spin.valueChanged.connect(lambda value: self._update("subtitle_next_lines", value))
        self.subtitle_line_spacing_spin.valueChanged.connect(lambda value: self._update("subtitle_line_spacing", value))
        self.subtitle_previous_opacity_spin.valueChanged.connect(lambda value: self._update("subtitle_previous_opacity", value))
        self.subtitle_previous_blur_spin.valueChanged.connect(lambda value: self._update("subtitle_previous_blur", value))
        self.subtitle_timing_offset_spin.valueChanged.connect(lambda value: self._update("subtitle_timing_offset", value))
        self.waveform_style_combo.currentIndexChanged.connect(lambda _index: self._update("waveform_style", self.waveform_style_combo.currentData()))
        self.level_meter_mode_combo.currentIndexChanged.connect(lambda _index: self._update("level_meter_mode", self.level_meter_mode_combo.currentData()))
        self.level_meter_style_combo.currentIndexChanged.connect(
            lambda _index: self._update(
                "level_meter_style", self.level_meter_style_combo.currentData()
            )
        )
        self.level_meter_orientation_combo.currentIndexChanged.connect(
            lambda _index: self._update(
                "level_meter_orientation", self.level_meter_orientation_combo.currentData()
            )
        )
        for field, widget in (
            ("level_meter_sensitivity", self.level_meter_sensitivity_spin),
            ("level_meter_attack", self.level_meter_attack_spin),
            ("level_meter_release", self.level_meter_release_spin),
            ("level_meter_min_level", self.level_meter_min_level_spin),
            ("level_meter_max_level", self.level_meter_max_level_spin),
            ("level_meter_segments", self.level_meter_segments_spin),
            ("level_meter_gap", self.level_meter_gap_spin),
            ("level_meter_peak_hold", self.level_meter_peak_hold_spin),
            ("level_meter_peak_decay", self.level_meter_peak_decay_spin),
        ):
            widget.valueChanged.connect(
                lambda _value, key=field, control=widget: self._update(key, control.value())
            )
        self.level_meter_show_peak_check.toggled.connect(
            lambda value: self._update("level_meter_show_peak", value)
        )
        for field, button in (
            ("level_meter_track_color", self.level_meter_track_color_button),
            ("level_meter_low_color", self.level_meter_low_color_button),
            ("level_meter_mid_color", self.level_meter_mid_color_button),
            ("level_meter_high_color", self.level_meter_high_color_button),
        ):
            button.clicked.connect(
                lambda _checked=False, key=field, control=button: self._choose_color(key, control)
            )
        self.particle_style_combo.currentIndexChanged.connect(lambda _index: self._update("particle_style", self.particle_style_combo.currentData()))
        self.particle_density_spin.valueChanged.connect(lambda value: self._update("particle_density", value))
        self.particle_speed_spin.valueChanged.connect(lambda value: self._update("particle_speed", value))
        for field, widget in (
            ("particle_min_size", self.particle_min_size_spin),
            ("particle_max_size", self.particle_max_size_spin),
            ("particle_opacity", self.particle_opacity_spin),
            ("particle_direction", self.particle_direction_spin),
            ("particle_drift", self.particle_drift_spin),
            ("particle_twinkle", self.particle_twinkle_spin),
            ("particle_glow", self.particle_glow_spin),
            ("particle_seed", self.particle_seed_spin),
        ):
            widget.valueChanged.connect(
                lambda _value, key=field, control=widget: self._update(key, control.value())
            )
        self.particle_secondary_color_button.clicked.connect(
            lambda: self._choose_color(
                "particle_secondary_color", self.particle_secondary_color_button,
            )
        )
        self.progress_track_color_button.clicked.connect(lambda: self._choose_color("progress_track_color", self.progress_track_color_button))
        self.animation_in_combo.currentIndexChanged.connect(lambda _index: self._update("animation_in", self.animation_in_combo.currentData()))
        self.animation_out_combo.currentIndexChanged.connect(lambda _index: self._update("animation_out", self.animation_out_combo.currentData()))
        self.animation_in_combo.currentIndexChanged.connect(self._update_animation_preview_button)
        self.animation_out_combo.currentIndexChanged.connect(self._update_animation_preview_button)
        self.animation_in_duration_spin.valueChanged.connect(
            lambda value: self._update("animation_in_duration", value)
        )
        self.animation_out_duration_spin.valueChanged.connect(
            lambda value: self._update("animation_out_duration", value)
        )
        self.animation_preview_button.clicked.connect(self._request_animation_preview)
        for field, widget in (
            ("x", self.x_spin), ("y", self.y_spin), ("width", self.width_spin),
            ("height", self.height_spin), ("rotation", self.rotation_spin),
            ("scale", self.scale_spin), ("opacity", self.opacity_spin),
            ("border_radius", self.radius_spin), ("outline_width", self.outline_spin),
            ("font_size", self.font_size_spin),
        ):
            widget.valueChanged.connect(
                lambda _value, key=field, control=widget: self._update(key, control.value())
            )
        self.font_family_combo.currentTextChanged.connect(
            lambda value: self._update("font_family", value.strip() or "Segoe UI")
        )
        self.font_add_button.clicked.connect(self._add_font_file)
        self.z_spin.valueChanged.connect(lambda _value: self._update("z_index", self.z_spin.value()))
        self.visible_check.toggled.connect(lambda value: self._update("visible", value))
        self.locked_check.toggled.connect(lambda value: self._update("locked", value))
        self.fill_color_button.clicked.connect(
            lambda: self._choose_color("fill_color", self.fill_color_button)
        )
        self.outline_color_button.clicked.connect(
            lambda: self._choose_color("outline_color", self.outline_color_button)
        )
        self.gradient_check.toggled.connect(self._update_gradient_enabled)
        self.gradient_start_button.clicked.connect(
            lambda: self._choose_gradient_color("start_color", self.gradient_start_button)
        )
        self.gradient_end_button.clicked.connect(
            lambda: self._choose_gradient_color("end_color", self.gradient_end_button)
        )
        for field, widget in (("blur", self.blur_spin), ("brightness", self.brightness_spin), ("contrast", self.contrast_spin)):
            widget.valueChanged.connect(lambda _value, key=field, control=widget: self._update(key, control.value()))
        self.shadow_check.toggled.connect(lambda value: self._update_shadow("enabled", value))
        self.shadow_color_button.clicked.connect(self._choose_shadow_color)
        for field, widget in (("opacity", self.shadow_opacity_spin), ("blur_radius", self.shadow_blur_spin), ("offset_x", self.shadow_x_spin), ("offset_y", self.shadow_y_spin)):
            widget.valueChanged.connect(lambda _value, key=field, control=widget: self._update_shadow(key, control.value()))

        direct_checks = (
            ("background_ambient", self.background_ambient_check),
            ("track_list_show_number", self.track_list_show_number_check),
            ("track_list_show_artist", self.track_list_show_artist_check),
            ("track_list_show_album", self.track_list_show_album_check),
            ("track_list_show_dividers", self.track_list_show_dividers_check),
            ("level_meter_show_peak", self.level_meter_show_peak_check),
            ("visible", self.visible_check), ("locked", self.locked_check),
        )
        for field, checkbox in direct_checks:
            checkbox.clicked.connect(
                lambda checked=False, key=field: self._apply_mixed_checkbox(key, checked)
            )
        self.gradient_check.clicked.connect(
            lambda checked=False: self._apply_mixed_nested_checkbox(
                "gradient.enabled", "gradient", "enabled", checked,
            )
        )
        self.shadow_check.clicked.connect(
            lambda checked=False: self._apply_mixed_nested_checkbox(
                "shadow.enabled", "shadow", "enabled", checked,
            )
        )

    def _update(self, field: str, value: object) -> None:
        if self._updating or not self._source_ids:
            return
        self._apply_updates({field: value})

    def _update_line(self, field: str, editor: QLineEdit) -> None:
        if field in self._mixed_fields and field not in self._dirty_line_fields:
            return
        self._dirty_line_fields.discard(field)
        self._update(field, editor.text())

    def _apply_updates(self, changes: dict[str, object]) -> None:
        source_ids = tuple(self._source_ids)
        if not source_ids:
            return
        self._applying_batch = True
        try:
            for source_id in source_ids:
                self.store.update(source_id, **changes)
        finally:
            self._applying_batch = False
        self._refresh_current_selection()

    def _apply_mixed_checkbox(self, field: str, checked: bool) -> None:
        if field in self._mixed_fields:
            self._update(field, checked)

    def _apply_mixed_nested_checkbox(
        self, mixed_key: str, container: str, field: str, checked: bool,
    ) -> None:
        if mixed_key in self._mixed_fields:
            self._update_nested(container, field, checked)

    def _update_background_mode(self, _index: int) -> None:
        """Apply the selected background mode and refresh dependent fields."""
        if self._updating:
            return
        self._update("background_mode", self.background_mode_combo.currentData())
        self._update_common_visibility(self._selected_sources())

    def _add_font_file(self) -> None:
        """Register a user font and bind its primary family to the selected source."""
        if not self._source_id:
            return
        korean = self.translator.language.value == "ko"
        path, _ = QFileDialog.getOpenFileName(
            self,
            "글꼴 파일 추가" if korean else "Add font file",
            "",
            "Font files (*.ttf *.otf);;TrueType/OpenType (*.ttf *.otf)",
        )
        if not path:
            return
        families = load_application_font(path)
        if not families:
            self.subtitle.setText(
                "글꼴 파일을 등록할 수 없습니다." if korean else "The font file could not be registered."
            )
            return
        family = families[0]
        if self.font_family_combo.findText(family) < 0:
            self.font_family_combo.addItem(family)
        self._updating = True
        try:
            self.font_family_combo.setCurrentText(family)
        finally:
            self._updating = False
        self._apply_updates({"font_family": family, "font_path": path})

    def _choose_content_file(self) -> None:
        if not self._source_id:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "이미지 선택" if self.translator.language.value == "ko" else "Choose image",
            self.file_path_edit.text(),
            "Images (*.jpg *.jpeg *.png *.webp *.svg)",
        )
        if path:
            self._update("content_path", path)

    def _choose_color(self, field: str, _button: QPushButton) -> None:
        source = self.store.get(self._source_id)
        if source is None:
            return
        korean = self.translator.language.value == "ko"
        color = QColorDialog.getColor(
            QColor(str(getattr(source, field))),
            self,
            "색상 및 투명도" if korean else "Color and transparency",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            self._update(field, self._serialized_color(color))

    def _update_gradient_enabled(self, enabled: bool) -> None:
        self._update_nested("gradient", "enabled", enabled)

    def _choose_gradient_color(self, field: str, _button: QPushButton) -> None:
        source = self.store.get(self._source_id)
        if source is None:
            return
        korean = self.translator.language.value == "ko"
        color = QColorDialog.getColor(
            QColor(str(getattr(source.gradient, field))),
            self,
            "그라데이션 색상 및 투명도" if korean
            else "Gradient color and transparency",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            self._update_nested(
                "gradient", field, self._serialized_color(color)
            )

    def _update_shadow(self, field: str, value: object) -> None:
        if self._updating:
            return
        self._update_nested("shadow", field, value)

    def _update_nested(self, container: str, field: str, value: object) -> None:
        if self._updating or not self._source_ids:
            return
        self._applying_batch = True
        try:
            for source in self._selected_sources():
                setattr(getattr(source, container), field, value)
                self.store.source_changed.emit(source)
        finally:
            self._applying_batch = False
        self._refresh_current_selection()

    def _choose_shadow_color(self) -> None:
        source = self.store.get(self._source_id)
        if source is None:
            return
        color = QColorDialog.getColor(
            QColor(source.shadow.color),
            self,
            "그림자 색상 및 투명도"
            if self.translator.language.value == "ko"
            else "Shadow color and transparency",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            self._update_shadow("color", self._serialized_color(color))

    def _set_enabled(self, enabled: bool) -> None:
        for editor in self._editors:
            editor.setEnabled(enabled)

    def _update_animation_preview_button(self, _value: object = None) -> None:
        has_animation = (
            self.animation_in_combo.currentData() != "none"
            or self.animation_out_combo.currentData() != "none"
        )
        self.animation_preview_button.setEnabled(
            self._source_id is not None and has_animation
        )

    def _request_animation_preview(self) -> None:
        if self._source_id and self.animation_preview_button.isEnabled():
            self.animation_preview_requested.emit(self._source_id)

    def _show_empty_state(self, visible: bool) -> None:
        """Swap the editor for a viewport-centered selection hint."""
        self._content.setVisible(not visible)
        self.empty_state.setVisible(visible)
        if visible:
            self.empty_state.setGeometry(self.viewport().rect())
            self.empty_state.raise_()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.empty_state.setGeometry(self.viewport().rect())

    def retranslate(self) -> None:
        labels = {
            "name": ("이름", "Name"), "text": ("텍스트", "Text"), "file": ("파일", "File"),
            "shape": ("도형", "Shape"), "progress_style": ("진행 바 스타일", "Progress style"),
            "visualizer_style": ("비주얼라이저 스타일", "Visualizer style"),
            "visualizer_bars": ("막대 / 점 개수", "Bars / dots"),
            "text_alignment": ("텍스트 정렬", "Text alignment"),
            "text_overflow": ("긴 텍스트 처리", "Long text handling"),
            "image_fit": ("이미지 맞춤", "Image fit"),
            "progress_value": ("진행 값", "Progress value"), "progress_track_color": ("트랙 색", "Track color"),
            "visualizer_line_width": ("선 두께", "Line width"),
            "visualizer_noise_gate": ("노이즈 게이트", "Noise gate"),
            "visualizer_min_level": ("최소 높이", "Minimum level"),
            "visualizer_max_level": ("최대 높이", "Maximum level"),
            "visualizer_attack": ("상승 속도", "Attack speed"),
            "visualizer_release": ("하강 속도", "Release speed"),
            "visualizer_smoothing": ("밴드 평활화", "Band smoothing"),
            "visualizer_curve": ("다이내믹 커브", "Dynamic curve"),
            "x": ("X", "X"), "y": ("Y", "Y"), "width": ("너비", "Width"),
            "height": ("높이", "Height"), "rotation": ("회전", "Rotation"),
            "scale": ("크기", "Scale"), "opacity": ("투명도", "Opacity"),
            "border_radius": ("모서리 반경", "Border radius"), "outline": ("윤곽선", "Outline"),
            "font_size": ("글꼴 크기", "Font size"), "font_family": ("글꼴", "Font"),
            "fill_color": ("채우기 색", "Fill color"),
            "outline_color": ("윤곽선 색", "Outline color"), "gradient": ("그라데이션", "Gradient"),
            "gradient_start": ("시작 색", "Start color"), "gradient_end": ("끝 색", "End color"),
            "blur": ("블러", "Blur"), "brightness": ("밝기", "Brightness"), "contrast": ("대비", "Contrast"),
            "shadow": ("그림자", "Shadow"), "shadow_color": ("그림자 색", "Shadow color"),
            "shadow_opacity": ("그림자 투명도", "Shadow opacity"), "shadow_blur": ("그림자 흐림", "Shadow blur"),
            "shadow_x": ("그림자 X", "Shadow X"), "shadow_y": ("그림자 Y", "Shadow Y"),
            "animation_in": ("곡 시작 애니메이션", "Track-start animation"),
            "animation_out": ("곡 종료 애니메이션", "Track-end animation"),
            "animation_in_duration": ("시작 애니메이션 시간", "Entrance duration"),
            "animation_out_duration": ("종료 애니메이션 시간", "Exit duration"),
            "layer": ("레이어", "Layer"),
        }
        labels.update({
            "visualizer_sensitivity": ("비주얼라이저 감도", "Visualizer sensitivity"),
            "visualizer_reactivity": ("반응 속도", "Response speed"),
            "background_mode": ("배경 모드", "Background mode"),
            "background_ambient": ("앨범 커버 앰비언트 블러", "Album art ambient blur"),
            "progress_mode": ("진행 기준", "Progress timing"),
            "album_frame": ("앨범 커버 프레임", "Album cover frame"),
            "track_list_count": ("표시 곡 개수", "Visible tracks"),
            "track_list_style": ("목록 스타일", "List style"),
            "track_list_window": ("표시 범위", "Track range"),
            "track_list_show_number": ("트랙 번호", "Show track numbers"),
            "track_list_show_artist": ("아티스트", "Show artist"),
            "track_list_show_album": ("앨범", "Show album"),
            "track_list_marker": ("현재 곡 표시", "Current-track marker"),
            "track_list_row_spacing": ("행 간격", "Row spacing"),
            "track_list_item_padding": ("목록 안쪽 여백", "List padding"),
            "track_list_current_color": ("현재 곡 글자색", "Current text color"),
            "track_list_inactive_color": ("다른 곡 글자색", "Other-track color"),
            "track_list_current_background": ("현재 곡 강조색", "Current highlight"),
            "track_list_inactive_opacity": ("다른 곡 투명도", "Other-track opacity"),
            "track_list_current_scale": ("현재 곡 크기", "Current-track scale"),
            "track_list_show_dividers": ("행 구분선", "Row dividers"),
            "now_playing_style": ("카드 스타일", "Card style"),
            "now_playing_duration": ("표시 시간", "Display seconds"),
            "now_playing_exit": ("사라짐 효과", "Exit effect"),
            "now_playing_exit_duration": ("사라짐 시간", "Exit duration"),
            "subtitle_style": ("가사 스타일", "Lyrics style"),
            "subtitle_animation": ("가사 전환", "Lyrics transition"),
            "subtitle_animation_duration": ("전환 시간", "Transition duration"),
            "subtitle_context_lines": ("이전 가사 줄", "Previous lyric lines"),
            "subtitle_next_lines": ("다음 가사 줄", "Next lyric lines"),
            "subtitle_previous_opacity": ("이전 가사 투명도", "Previous lyric opacity"),
            "subtitle_previous_blur": ("이전 가사 블러", "Previous lyric blur"),
            "subtitle_timing_offset": ("가사 시간 보정 (초)", "Lyric timing offset (s)"),
            "waveform_style": ("파형 스타일", "Waveform style"),
            "level_meter_mode": ("레벨 미터", "Level meter"),
            "level_meter_style": ("미터 스타일", "Meter style"),
            "level_meter_orientation": ("방향", "Orientation"),
            "level_meter_sensitivity": ("입력 감도", "Input sensitivity"),
            "level_meter_attack": ("상승 속도", "Attack speed"),
            "level_meter_release": ("하강 속도", "Release speed"),
            "level_meter_min_level": ("최소 레벨", "Minimum level"),
            "level_meter_max_level": ("최대 레벨", "Maximum level"),
            "level_meter_segments": ("구간 수", "Segments"),
            "level_meter_gap": ("채널 간격", "Channel gap"),
            "level_meter_show_peak": ("피크 표시", "Show peak"),
            "level_meter_peak_hold": ("피크 유지 시간", "Peak hold"),
            "level_meter_peak_decay": ("피크 하강 속도", "Peak decay"),
            "level_meter_track_color": ("배경 트랙 색", "Track color"),
            "level_meter_low_color": ("낮은 레벨 색", "Low-level color"),
            "level_meter_mid_color": ("중간 레벨 색", "Mid-level color"),
            "level_meter_high_color": ("피크 색", "Peak color"),
            "particle_style": ("파티클 스타일", "Particle style"),
            "particle_density": ("파티클 밀도", "Particle density"),
            "particle_speed": ("파티클 속도", "Particle speed"),
            "particle_min_size": ("최소 크기", "Minimum size"),
            "particle_max_size": ("최대 크기", "Maximum size"),
            "particle_opacity": ("파티클 투명도", "Particle opacity"),
            "particle_direction": ("이동 방향", "Direction"),
            "particle_drift": ("흔들림", "Drift"),
            "particle_twinkle": ("반짝임", "Twinkle"),
            "particle_glow": ("글로우", "Glow"),
            "particle_secondary_color": ("보조 색상", "Secondary color"),
            "particle_seed": ("배치 시드", "Layout seed"),
        })
        labels["subtitle_line_spacing"] = ("가사 줄 간격", "Lyric line spacing")
        korean = self.translator.language.value == "ko"
        for key, label in self._form_labels.items():
            label.setText(labels[key][0 if korean else 1])
        self.content_group.setTitle(self.translator.text("content"))
        self.transform_group.setTitle(self.translator.text("transform"))
        self.appearance_group.setTitle(self.translator.text("appearance"))
        self.visible_check.setText("표시" if korean else "Visible")
        self.locked_check.setText("잠금" if korean else "Locked")
        self.file_button.setText("찾아보기" if korean else "Browse")
        self.clear_file_button.setText("제거" if korean else "Clear")
        self.font_add_button.setText("글꼴 추가" if korean else "Add font")
        overflow_labels = (
            ("자동 줄바꿈", "말줄임표 (…)", "영역에서 자르기")
            if korean else
            ("Automatic wrap", "Ellipsis (…)", "Clip to box")
        )
        for index, label in enumerate(overflow_labels):
            self.text_overflow_combo.setItemText(index, label)
        track_style_labels = (
            ("컴팩트", "카드", "재생 대기열", "미니멀", "스크롤 / 페이드", "글래스", "필")
            if korean else
            ("Compact", "Cards", "Queue", "Minimal", "Scroll / fade", "Glass", "Pills")
        )
        for index, label in enumerate(track_style_labels):
            self.track_list_style_combo.setItemText(index, label)
        track_window_labels = (
            ("이전 + 현재 + 다음", "현재 + 다음 곡", "이전 곡 + 현재")
            if korean else
            ("Previous + current + next", "Current + upcoming", "History + current")
        )
        for index, label in enumerate(track_window_labels):
            self.track_list_window_combo.setItemText(index, label)
        marker_labels = (
            ("재생 ▶", "점 ●", "강조선 ▌", "표시 없음")
            if korean else ("Play ▶", "Dot ●", "Accent ▌", "None")
        )
        for index, label in enumerate(marker_labels):
            self.track_list_marker_combo.setItemText(index, label)
        subtitle_animation_labels = (
            ("소프트 포커스", "스무스 슬라이드",
             "블러 리빌", "페이드", "위로 스크롤", "아래로 스크롤", "팝", "없음")
            if korean else
            ("Soft focus", "Smooth slide",
             "Blur reveal", "Fade", "Scroll up", "Scroll down", "Pop", "None")
        )
        for index, label in enumerate(subtitle_animation_labels):
            self.subtitle_animation_combo.setItemText(index, label)
        self.subtitle_animation_combo.setToolTip(
            "소프트 포커스는 부드러운 초점·상승 전환, 스무스 슬라이드는 짧고 선명한 이동 전환입니다."
            if korean else
            "Soft focus uses a gentle focused lift; Smooth slide uses a shorter, crisper motion."
        )
        self.font_add_button.setToolTip(
            "TTF 또는 OTF 글꼴 파일을 이 프로젝트의 텍스트에 추가합니다."
            if korean else "Add a TTF or OTF font file for this project's text sources."
        )
        self.track_list_window_combo.setToolTip(
            "현재 곡을 기준으로 목록에 이전 곡과 다음 곡을 어떻게 배치할지 정합니다."
            if korean else "Choose how previous and upcoming tracks are arranged around the current track."
        )
        self.track_list_inactive_opacity_spin.setToolTip(
            "현재 재생 중이 아닌 곡을 흐리게 표시하는 정도입니다."
            if korean else "Controls how faint non-current tracks appear."
        )
        self.track_list_current_scale_spin.setToolTip(
            "현재 곡 글자만 확대하여 목록에서 더 잘 보이게 합니다."
            if korean else "Enlarges only the current track to strengthen its emphasis."
        )
        visualizer_tips = {
            self.visualizer_noise_gate_spin: (
                "이 값보다 작은 입력을 무음으로 처리합니다. 0이면 비활성화됩니다.",
                "Treat input below this value as silence. Set to 0 to disable.",
            ),
            self.visualizer_min_level_spin: (
                "무음일 때의 기본 높이입니다. 완전히 평평하게 하려면 0으로 설정하세요.",
                "Baseline at silence. Set to 0 for a completely flat idle wave.",
            ),
            self.visualizer_max_level_spin: (
                "가장 큰 소리에서 사용할 최대 높이입니다.",
                "Maximum height used for the loudest signal.",
            ),
            self.visualizer_attack_spin: (
                "소리가 커질 때 따라가는 속도입니다.",
                "How quickly the visualizer follows rising audio.",
            ),
            self.visualizer_release_spin: (
                "소리가 작아질 때 내려오는 속도입니다.",
                "How quickly the visualizer falls after audio gets quieter.",
            ),
            self.visualizer_smoothing_spin: (
                "인접한 주파수 막대 사이의 높이 차이를 부드럽게 만듭니다.",
                "Smooth height differences between neighbouring frequency bands.",
            ),
            self.visualizer_curve_spin: (
                "1보다 작으면 작은 소리를 강조하고, 1보다 크면 큰 소리를 강조합니다.",
                "Below 1 emphasizes quiet detail; above 1 emphasizes strong peaks.",
            ),
        }
        for widget, tip in visualizer_tips.items():
            widget.setToolTip(tip[0 if korean else 1])
        particle_style_labels = (
            ("먼지", "네온", "노이즈", "눈", "별", "보케", "색종이")
            if korean else
            ("Dust", "Neon", "Noise", "Snow", "Stars", "Bokeh", "Confetti")
        )
        for index, label in enumerate(particle_style_labels):
            self.particle_style_combo.setItemText(index, label)
        self.particle_direction_spin.setToolTip(
            "각도 기준: 0° 오른쪽, 90° 아래, -90° 위"
            if korean else "Angle: 0° right, 90° down, -90° up"
        )
        self.particle_seed_spin.setToolTip(
            "값을 바꾸면 같은 설정으로 새로운 파티클 배치를 만듭니다."
            if korean else "Change this value to generate a new layout with the same settings."
        )
        self.particle_density_spin.setToolTip(
            "높은 밀도와 강한 글로우를 함께 사용하면 미리보기 성능이 낮아질 수 있습니다."
            if korean else
            "High density combined with strong glow can reduce preview performance."
        )
        self.level_meter_show_peak_check.setText("사용" if korean else "Enabled")
        meter_style_labels = (
            ("그라데이션", "단색", "LED", "분할 막대")
            if korean else ("Gradient", "Solid", "LED", "Segments")
        )
        for index, label in enumerate(meter_style_labels):
            self.level_meter_style_combo.setItemText(index, label)
        self.level_meter_mode_combo.setItemText(0, "스테레오" if korean else "Stereo")
        self.level_meter_mode_combo.setItemText(1, "모노" if korean else "Mono")
        self.level_meter_orientation_combo.setItemText(0, "세로" if korean else "Vertical")
        self.level_meter_orientation_combo.setItemText(1, "가로" if korean else "Horizontal")
        self.gradient_check.setText("사용" if korean else "Enabled")
        self.animation_preview_button.setText(
            "애니메이션 미리보기" if korean else "Preview animation"
        )
        self.animation_preview_button.setToolTip(
            "선택 요소의 등장 및 종료 애니메이션을 캔버스에서 재생합니다."
            if korean else
            "Play the selected source's entrance and exit animation on the Canvas."
        )
        self.text_edit.setPlaceholderText(
            "%title% · %artist% · %album%"
        )
        self.text_edit.setToolTip(
            "지원: %title%, %artist%, %album%, %track%, %track_total%, %filename%, %current_time%, %total_time%, %track_current_time%, %track_total_time%, %video_current_time%, %video_total_time%"
            if korean else
            "Supported: %title%, %artist%, %album%, %track%, %track_total%, %filename%, %current_time%, %total_time%, %track_current_time%, %track_total_time%, %video_current_time%, %video_total_time%"
        )
        self._install_property_tooltips()
        self.empty_state.setText(self.translator.text("select_object"))
        if self._source_id is None:
            self.title.setText(self.translator.text("inspector"))
            self.subtitle.setText(self.translator.text("select_object"))
        elif len(self._source_ids) > 1:
            count = len(self._source_ids)
            self.title.setText(
                f"요소 {count}개 선택" if korean else f"{count} sources selected"
            )
            self.subtitle.setText(
                "공통 속성을 한 번에 편집합니다. 다른 값은 공백으로 표시됩니다."
                if korean else
                "Edit shared properties together. Mixed values are shown blank."
            )
            self.text_edit.setPlaceholderText("")

    def set_source(self, source: Source | None) -> None:
        """Compatibility entry point for a single Inspector selection."""
        self.set_sources((source.id,) if source is not None else (), source)

    def set_sources(self, source_ids: object, active: Source | None) -> None:
        """Bind the Inspector to one source or a shared multi-selection."""
        identifiers = tuple(
            source_id for source_id in (
                source_ids if isinstance(source_ids, (tuple, list)) else ()
            )
            if isinstance(source_id, str) and self.store.get(source_id) is not None
        )
        sources = [
            source for source_id in identifiers
            if (source := self.store.get(source_id)) is not None
        ]
        self._source_ids = identifiers
        self._source_id = (
            active.id if active is not None and active.id in identifiers
            else identifiers[-1] if identifiers else None
        )
        self._mixed_fields.clear()
        self._dirty_line_fields.clear()
        self._set_enabled(bool(sources))
        self._show_empty_state(not sources)
        if not sources:
            self._update_source_specific_fields(None)
            self.title.setText(self.translator.text("inspector"))
            self.subtitle.setText(self.translator.text("select_object"))
            return
        if len(sources) == 1:
            source = sources[0]
            self._clear_mixed_visuals()
            self.title.setText(source.name)
            self.subtitle.setText(source.source_type.value.replace("_", " ").title())
            self.animation_preview_button.setVisible(True)
            self._fill(source)
            return
        korean = self.translator.language.value == "ko"
        self.title.setText(
            f"요소 {len(sources)}개 선택" if korean else f"{len(sources)} sources selected"
        )
        self.subtitle.setText(
            "공통 속성을 한 번에 편집합니다. 다른 값은 공백으로 표시됩니다."
            if korean else
            "Edit shared properties together. Mixed values are shown blank."
        )
        self.animation_preview_button.setVisible(False)
        self._fill_multi(sources)

    def refresh_source(self, source: Source) -> None:
        if self._applying_batch or source.id not in self._source_ids:
            return
        self._refresh_current_selection()

    def _selected_sources(self) -> list[Source]:
        return [
            source for source_id in self._source_ids
            if (source := self.store.get(source_id)) is not None
        ]

    def _refresh_current_selection(self) -> None:
        sources = self._selected_sources()
        if len(sources) > 1:
            self._fill_multi(sources)
        elif sources:
            self.title.setText(sources[0].name)
            self._fill(sources[0])

    def _update_common_visibility(self, sources: list[Source]) -> None:
        """Keep only property rows visible for every selected source."""
        if not sources:
            self._update_source_specific_fields(None)
            return
        visible_sets: list[set[str]] = []
        for source in sources:
            self._update_source_specific_fields(source)
            visible_sets.append({
                key for key, widget in self._field_widgets.items()
                if not widget.isHidden()
            })
        common = set.intersection(*visible_sets)
        for key in self._field_widgets:
            self._set_field_visible(key, key in common)

    def _fill_multi(self, sources: list[Source]) -> None:
        """Fill from the active source, then blank every non-uniform value."""
        active = self.store.get(self._source_id) or sources[-1]
        self._clear_mixed_visuals()
        self._fill(active)
        self._update_common_visibility(sources)
        self._mixed_fields.clear()
        self._dirty_line_fields.clear()
        self.text_edit.setPlaceholderText("")
        self._updating = True
        try:
            for field, (path, widget, kind) in self._multi_value_bindings().items():
                values = [self._read_path(source, path) for source in sources]
                if any(value != values[0] for value in values[1:]):
                    self._mixed_fields.add(field)
                    self._set_mixed_widget(widget, kind)
                elif isinstance(widget, QCheckBox):
                    widget.setTristate(False)
        finally:
            self._updating = False
        self._update_animation_preview_button()

    def _clear_mixed_visuals(self) -> None:
        """Restore normal control presentation before filling concrete values."""
        previous = self._updating
        self._updating = True
        try:
            for _field, (_path, widget, kind) in self._multi_value_bindings().items():
                if kind == "check" and isinstance(widget, QCheckBox):
                    widget.setTristate(False)
            self.text_edit.setPlaceholderText("%title% · %artist% · %album%")
        finally:
            self._updating = previous

    @staticmethod
    def _read_path(source: Source, path: str) -> object:
        value: object = source
        for part in path.split("."):
            value = getattr(value, part)
        return value

    @staticmethod
    def _set_mixed_widget(widget: QWidget, kind: str) -> None:
        if kind == "line" and isinstance(widget, QLineEdit):
            widget.clear()
        elif kind == "spin" and isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.lineEdit().clear()
        elif kind == "combo" and isinstance(widget, QComboBox):
            widget.setCurrentIndex(-1)
            if widget.isEditable() and widget.lineEdit() is not None:
                widget.lineEdit().clear()
        elif kind == "check" and isinstance(widget, QCheckBox):
            widget.setTristate(True)
            widget.setCheckState(Qt.CheckState.PartiallyChecked)
        elif kind == "color" and isinstance(widget, QPushButton):
            widget.setText("")
            widget.setStyleSheet("border: 1px dashed #7B8794;")

    def _multi_value_bindings(self) -> dict[str, tuple[str, QWidget, str]]:
        """Map editable model values to the controls that present them."""
        bindings: dict[str, tuple[str, QWidget, str]] = {}

        def add(kind: str, controls: dict[str, QWidget]) -> None:
            bindings.update({
                path: (path, widget, kind) for path, widget in controls.items()
            })

        add("line", {
            "name": self.name_edit, "text": self.text_edit,
            "content_path": self.file_path_edit,
        })
        add("combo", {
            "shape_kind": self.shape_kind_combo,
            "progress_style": self.progress_style_combo,
            "visualizer_style": self.visualizer_style_combo,
            "text_alignment": self.text_alignment_combo,
            "text_overflow": self.text_overflow_combo,
            "image_fit_mode": self.image_fit_combo,
            "background_mode": self.background_mode_combo,
            "progress_mode": self.progress_mode_combo,
            "album_frame_style": self.album_frame_combo,
            "track_list_style": self.track_list_style_combo,
            "track_list_window": self.track_list_window_combo,
            "track_list_marker": self.track_list_marker_combo,
            "now_playing_style": self.now_playing_style_combo,
            "now_playing_exit_animation": self.now_playing_exit_combo,
            "subtitle_style": self.subtitle_style_combo,
            "subtitle_animation": self.subtitle_animation_combo,
            "waveform_style": self.waveform_style_combo,
            "level_meter_mode": self.level_meter_mode_combo,
            "level_meter_style": self.level_meter_style_combo,
            "level_meter_orientation": self.level_meter_orientation_combo,
            "particle_style": self.particle_style_combo,
            "font_family": self.font_family_combo,
            "animation_in": self.animation_in_combo,
            "animation_out": self.animation_out_combo,
        })
        add("spin", {
            "x": self.x_spin, "y": self.y_spin, "width": self.width_spin,
            "height": self.height_spin, "rotation": self.rotation_spin,
            "scale": self.scale_spin, "opacity": self.opacity_spin,
            "border_radius": self.radius_spin, "outline_width": self.outline_spin,
            "font_size": self.font_size_spin, "z_index": self.z_spin,
            "visualizer_bars": self.visualizer_bars_spin,
            "progress_value": self.progress_value_spin,
            "visualizer_line_width": self.visualizer_line_width_spin,
            "visualizer_sensitivity": self.visualizer_sensitivity_spin,
            "visualizer_reactivity": self.visualizer_reactivity_spin,
            "visualizer_noise_gate": self.visualizer_noise_gate_spin,
            "visualizer_min_level": self.visualizer_min_level_spin,
            "visualizer_max_level": self.visualizer_max_level_spin,
            "visualizer_attack": self.visualizer_attack_spin,
            "visualizer_release": self.visualizer_release_spin,
            "visualizer_smoothing": self.visualizer_smoothing_spin,
            "visualizer_curve": self.visualizer_curve_spin,
            "track_list_count": self.track_list_count_spin,
            "track_list_row_spacing": self.track_list_row_spacing_spin,
            "track_list_item_padding": self.track_list_item_padding_spin,
            "track_list_inactive_opacity": self.track_list_inactive_opacity_spin,
            "track_list_current_scale": self.track_list_current_scale_spin,
            "now_playing_duration": self.now_playing_duration_spin,
            "now_playing_exit_duration": self.now_playing_exit_duration_spin,
            "subtitle_animation_duration": self.subtitle_animation_duration_spin,
            "subtitle_context_lines": self.subtitle_context_lines_spin,
            "subtitle_next_lines": self.subtitle_next_lines_spin,
            "subtitle_line_spacing": self.subtitle_line_spacing_spin,
            "subtitle_previous_opacity": self.subtitle_previous_opacity_spin,
            "subtitle_previous_blur": self.subtitle_previous_blur_spin,
            "subtitle_timing_offset": self.subtitle_timing_offset_spin,
            "level_meter_sensitivity": self.level_meter_sensitivity_spin,
            "level_meter_attack": self.level_meter_attack_spin,
            "level_meter_release": self.level_meter_release_spin,
            "level_meter_min_level": self.level_meter_min_level_spin,
            "level_meter_max_level": self.level_meter_max_level_spin,
            "level_meter_segments": self.level_meter_segments_spin,
            "level_meter_gap": self.level_meter_gap_spin,
            "level_meter_peak_hold": self.level_meter_peak_hold_spin,
            "level_meter_peak_decay": self.level_meter_peak_decay_spin,
            "particle_density": self.particle_density_spin,
            "particle_speed": self.particle_speed_spin,
            "particle_min_size": self.particle_min_size_spin,
            "particle_max_size": self.particle_max_size_spin,
            "particle_opacity": self.particle_opacity_spin,
            "particle_direction": self.particle_direction_spin,
            "particle_drift": self.particle_drift_spin,
            "particle_twinkle": self.particle_twinkle_spin,
            "particle_glow": self.particle_glow_spin,
            "particle_seed": self.particle_seed_spin,
            "blur": self.blur_spin, "brightness": self.brightness_spin,
            "contrast": self.contrast_spin,
            "animation_in_duration": self.animation_in_duration_spin,
            "animation_out_duration": self.animation_out_duration_spin,
            "shadow.opacity": self.shadow_opacity_spin,
            "shadow.blur_radius": self.shadow_blur_spin,
            "shadow.offset_x": self.shadow_x_spin,
            "shadow.offset_y": self.shadow_y_spin,
        })
        add("check", {
            "background_ambient": self.background_ambient_check,
            "track_list_show_number": self.track_list_show_number_check,
            "track_list_show_artist": self.track_list_show_artist_check,
            "track_list_show_album": self.track_list_show_album_check,
            "track_list_show_dividers": self.track_list_show_dividers_check,
            "level_meter_show_peak": self.level_meter_show_peak_check,
            "visible": self.visible_check, "locked": self.locked_check,
            "gradient.enabled": self.gradient_check,
            "shadow.enabled": self.shadow_check,
        })
        add("color", {
            "progress_track_color": self.progress_track_color_button,
            "track_list_current_color": self.track_list_current_color_button,
            "track_list_inactive_color": self.track_list_inactive_color_button,
            "track_list_current_background": self.track_list_current_background_button,
            "level_meter_track_color": self.level_meter_track_color_button,
            "level_meter_low_color": self.level_meter_low_color_button,
            "level_meter_mid_color": self.level_meter_mid_color_button,
            "level_meter_high_color": self.level_meter_high_color_button,
            "particle_secondary_color": self.particle_secondary_color_button,
            "fill_color": self.fill_color_button,
            "outline_color": self.outline_color_button,
            "gradient.start_color": self.gradient_start_button,
            "gradient.end_color": self.gradient_end_button,
            "shadow.color": self.shadow_color_button,
        })
        return bindings

    def _fill(self, source: Source) -> None:
        self._update_source_specific_fields(source)
        self._updating = True
        try:
            self.name_edit.setText(source.name)
            self.text_edit.setText(source.text)
            self.file_path_edit.setText(source.content_path)
            self.x_spin.setValue(source.x)
            self.y_spin.setValue(source.y)
            self.width_spin.setValue(source.width)
            self.height_spin.setValue(source.height)
            self.rotation_spin.setValue(source.rotation)
            self.scale_spin.setValue(source.scale)
            self.opacity_spin.setValue(source.opacity)
            self.radius_spin.setValue(source.border_radius)
            self.outline_spin.setValue(source.outline_width)
            self.font_size_spin.setValue(source.font_size)
            if source.font_path:
                load_application_font(source.font_path)
            if self.font_family_combo.findText(source.font_family) < 0:
                self.font_family_combo.addItem(source.font_family)
            self.font_family_combo.setCurrentText(source.font_family)
            self.shape_kind_combo.setCurrentIndex(max(0, self.shape_kind_combo.findData(source.shape_kind)))
            self.progress_style_combo.setCurrentIndex(max(0, self.progress_style_combo.findData(source.progress_style)))
            self.visualizer_style_combo.setCurrentIndex(max(0, self.visualizer_style_combo.findData(source.visualizer_style)))
            self.visualizer_bars_spin.setValue(source.visualizer_bars)
            self.text_alignment_combo.setCurrentIndex(max(0, self.text_alignment_combo.findData(source.text_alignment)))
            self.text_overflow_combo.setCurrentIndex(
                max(0, self.text_overflow_combo.findData(source.text_overflow))
            )
            self.image_fit_combo.setCurrentIndex(max(0, self.image_fit_combo.findData(source.image_fit_mode)))
            self.background_mode_combo.setCurrentIndex(max(0, self.background_mode_combo.findData(source.background_mode)))
            self.background_ambient_check.setChecked(source.background_ambient)
            self.progress_value_spin.setValue(source.progress_value)
            self.progress_mode_combo.setCurrentIndex(max(0, self.progress_mode_combo.findData(source.progress_mode)))
            self.visualizer_line_width_spin.setValue(source.visualizer_line_width)
            self.visualizer_sensitivity_spin.setValue(source.visualizer_sensitivity)
            self.visualizer_reactivity_spin.setValue(source.visualizer_reactivity)
            self.visualizer_noise_gate_spin.setValue(source.visualizer_noise_gate)
            self.visualizer_min_level_spin.setValue(source.visualizer_min_level)
            self.visualizer_max_level_spin.setValue(source.visualizer_max_level)
            self.visualizer_attack_spin.setValue(source.visualizer_attack)
            self.visualizer_release_spin.setValue(source.visualizer_release)
            self.visualizer_smoothing_spin.setValue(source.visualizer_smoothing)
            self.visualizer_curve_spin.setValue(source.visualizer_curve)
            self.album_frame_combo.setCurrentIndex(max(0, self.album_frame_combo.findData(source.album_frame_style)))
            self.track_list_count_spin.setValue(source.track_list_count)
            self.track_list_style_combo.setCurrentIndex(max(0, self.track_list_style_combo.findData(source.track_list_style)))
            self.track_list_window_combo.setCurrentIndex(max(0, self.track_list_window_combo.findData(source.track_list_window)))
            self.track_list_show_number_check.setChecked(source.track_list_show_number)
            self.track_list_show_artist_check.setChecked(source.track_list_show_artist)
            self.track_list_show_album_check.setChecked(source.track_list_show_album)
            self.track_list_marker_combo.setCurrentIndex(max(0, self.track_list_marker_combo.findData(source.track_list_marker)))
            self.track_list_row_spacing_spin.setValue(source.track_list_row_spacing)
            self.track_list_item_padding_spin.setValue(source.track_list_item_padding)
            self.track_list_inactive_opacity_spin.setValue(source.track_list_inactive_opacity)
            self.track_list_current_scale_spin.setValue(source.track_list_current_scale)
            self.track_list_show_dividers_check.setChecked(source.track_list_show_dividers)
            self._set_color_button(self.track_list_current_color_button, source.track_list_current_color)
            self._set_color_button(self.track_list_inactive_color_button, source.track_list_inactive_color)
            self._set_color_button(self.track_list_current_background_button, source.track_list_current_background)
            self.now_playing_style_combo.setCurrentIndex(max(0, self.now_playing_style_combo.findData(source.now_playing_style)))
            self.now_playing_duration_spin.setValue(source.now_playing_duration)
            self.now_playing_exit_combo.setCurrentIndex(max(0, self.now_playing_exit_combo.findData(source.now_playing_exit_animation)))
            self.now_playing_exit_duration_spin.setValue(source.now_playing_exit_duration)
            self.subtitle_style_combo.setCurrentIndex(max(0, self.subtitle_style_combo.findData(source.subtitle_style)))
            self.subtitle_animation_combo.setCurrentIndex(max(0, self.subtitle_animation_combo.findData(source.subtitle_animation)))
            self.subtitle_animation_duration_spin.setValue(source.subtitle_animation_duration)
            self.subtitle_context_lines_spin.setValue(source.subtitle_context_lines)
            self.subtitle_next_lines_spin.setValue(source.subtitle_next_lines)
            self.subtitle_line_spacing_spin.setValue(source.subtitle_line_spacing)
            self.subtitle_previous_opacity_spin.setValue(source.subtitle_previous_opacity)
            self.subtitle_previous_blur_spin.setValue(source.subtitle_previous_blur)
            self.subtitle_timing_offset_spin.setValue(source.subtitle_timing_offset)
            self.waveform_style_combo.setCurrentIndex(max(0, self.waveform_style_combo.findData(source.waveform_style)))
            legacy_led = source.level_meter_mode == "led"
            meter_mode = "stereo" if legacy_led else source.level_meter_mode
            meter_style = "led" if legacy_led else source.level_meter_style
            self.level_meter_mode_combo.setCurrentIndex(max(0, self.level_meter_mode_combo.findData(meter_mode)))
            self.level_meter_style_combo.setCurrentIndex(max(0, self.level_meter_style_combo.findData(meter_style)))
            self.level_meter_orientation_combo.setCurrentIndex(max(0, self.level_meter_orientation_combo.findData(source.level_meter_orientation)))
            self.level_meter_sensitivity_spin.setValue(source.level_meter_sensitivity)
            self.level_meter_attack_spin.setValue(source.level_meter_attack)
            self.level_meter_release_spin.setValue(source.level_meter_release)
            self.level_meter_min_level_spin.setValue(source.level_meter_min_level)
            self.level_meter_max_level_spin.setValue(source.level_meter_max_level)
            self.level_meter_segments_spin.setValue(source.level_meter_segments)
            self.level_meter_gap_spin.setValue(source.level_meter_gap)
            self.level_meter_show_peak_check.setChecked(source.level_meter_show_peak)
            self.level_meter_peak_hold_spin.setValue(source.level_meter_peak_hold)
            self.level_meter_peak_decay_spin.setValue(source.level_meter_peak_decay)
            self._set_color_button(self.level_meter_track_color_button, source.level_meter_track_color)
            self._set_color_button(self.level_meter_low_color_button, source.level_meter_low_color)
            self._set_color_button(self.level_meter_mid_color_button, source.level_meter_mid_color)
            self._set_color_button(self.level_meter_high_color_button, source.level_meter_high_color)
            self.particle_style_combo.setCurrentIndex(max(0, self.particle_style_combo.findData(source.particle_style)))
            self.particle_density_spin.setValue(source.particle_density)
            self.particle_speed_spin.setValue(source.particle_speed)
            self.particle_min_size_spin.setValue(source.particle_min_size)
            self.particle_max_size_spin.setValue(source.particle_max_size)
            self.particle_opacity_spin.setValue(source.particle_opacity)
            self.particle_direction_spin.setValue(source.particle_direction)
            self.particle_drift_spin.setValue(source.particle_drift)
            self.particle_twinkle_spin.setValue(source.particle_twinkle)
            self.particle_glow_spin.setValue(source.particle_glow)
            self._set_color_button(
                self.particle_secondary_color_button, source.particle_secondary_color,
            )
            self.particle_seed_spin.setValue(source.particle_seed)
            self._set_color_button(self.progress_track_color_button, source.progress_track_color)
            self._set_color_button(self.fill_color_button, source.fill_color)
            self._set_color_button(self.outline_color_button, source.outline_color)
            self.gradient_check.setChecked(source.gradient.enabled)
            self._set_color_button(self.gradient_start_button, source.gradient.start_color)
            self._set_color_button(self.gradient_end_button, source.gradient.end_color)
            self.blur_spin.setValue(source.blur)
            self.brightness_spin.setValue(source.brightness)
            self.contrast_spin.setValue(source.contrast)
            self.shadow_check.setChecked(source.shadow.enabled)
            self._set_color_button(self.shadow_color_button, source.shadow.color)
            self.shadow_opacity_spin.setValue(source.shadow.opacity)
            self.shadow_blur_spin.setValue(source.shadow.blur_radius)
            self.shadow_x_spin.setValue(source.shadow.offset_x)
            self.shadow_y_spin.setValue(source.shadow.offset_y)
            self.animation_in_combo.setCurrentIndex(max(0, self.animation_in_combo.findData(source.animation_in)))
            self.animation_out_combo.setCurrentIndex(max(0, self.animation_out_combo.findData(source.animation_out)))
            self.animation_in_duration_spin.setValue(source.animation_in_duration)
            self.animation_out_duration_spin.setValue(source.animation_out_duration)
            self.z_spin.setValue(source.z_index)
            self.visible_check.setChecked(source.visible)
            self.locked_check.setChecked(source.locked)
        finally:
            self._updating = False
        self._update_animation_preview_button()

    @staticmethod
    def _serialized_color(color: QColor) -> str:
        """Preserve alpha only when needed while keeping legacy RGB readable."""
        name_format = (
            QColor.NameFormat.HexRgb
            if color.alpha() == 255 else QColor.NameFormat.HexArgb
        )
        return color.name(name_format).upper()

    def _set_color_button(self, button: QPushButton, value: str) -> None:
        color = QColor(value)
        if not color.isValid():
            color = QColor("#FFFFFF")
        text_color = "#111111" if color.lightness() > 150 else "#FFFFFF"
        serialized = self._serialized_color(color)
        if color.alpha() == 0:
            button.setText(
                "투명" if self.translator.language.value == "ko" else "Transparent"
            )
        elif color.alpha() < 255:
            button.setText(f"{serialized} · {round(color.alphaF() * 100)}%")
        else:
            button.setText(serialized)
        button.setStyleSheet(
            f"background: rgba({color.red()}, {color.green()}, {color.blue()}, "
            f"{color.alpha()}); color: {text_color}; border: 1px solid #7B8794;"
        )
