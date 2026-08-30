"""Ready-made, track-aware visual layouts for playlist videos."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.models.source import Gradient, Shadow, Source, SourceType


@dataclass(frozen=True, slots=True)
class PresetDefinition:
    """Describes a selectable, complete canvas layout."""

    identifier: str
    korean_name: str
    english_name: str
    korean_description: str
    english_description: str
    builder: Callable[[], list[Source]]
    # Coordinate space the builder authors in; the target canvas is adapted from
    # these dimensions. User presets record the canvas they were captured on.
    source_width: float = 1280.0
    source_height: float = 720.0
    # True for user-saved presets, which the picker lets you delete or export.
    editable: bool = False

    def name(self, language: str) -> str:
        return self.korean_name if language == "ko" else self.english_name

    def description(self, language: str) -> str:
        return self.korean_description if language == "ko" else self.english_description


TRANSPARENT = "#00000000"


def _background(color: str, end_color: str | None = None, *,
                mode: str = "color", ambient: bool = False) -> Source:
    source = Source(
        SourceType.BACKGROUND,
        "Background",
        width=1280,
        height=720,
        fill_color=color,
        locked=False,
        z_index=-20,
        text="",
        background_mode=mode,
        background_ambient=ambient,
    )
    if end_color:
        source.gradient = Gradient(True, color, end_color)
    return source


def _panel(
    name: str,
    x: float,
    y: float,
    width: float,
    height: float,
    color: str,
    z: int,
    radius: float = 20,
    opacity: float = 1.0,
) -> Source:
    return Source(
        SourceType.SHAPE,
        name,
        x=x,
        y=y,
        width=width,
        height=height,
        fill_color=color,
        opacity=opacity,
        border_radius=radius,
        z_index=z,
        locked=True,
    )


def _text(
    name: str,
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    text_color: str,
    size: float,
    z: int,
    *,
    surface: str = TRANSPARENT,
    alignment: str = "left",
    weight: int = 600,
    animation_in: str = "fade",
    animation_out: str = "fade",
    animation_duration: float = 0.45,
) -> Source:
    return Source(
        SourceType.TEXT,
        name,
        x=x,
        y=y,
        width=width,
        height=height,
        fill_color=surface,
        outline_color=text_color,
        text=text,
        font_size=size,
        font_weight=weight,
        text_alignment=alignment,
        z_index=z,
        animation_in=animation_in,
        animation_out=animation_out,
        animation_duration=animation_duration,
    )


def _cover(
    x: float,
    y: float,
    size: float,
    color: str,
    z: int,
    *,
    radius: float = 24,
    frame: str = "rounded",
    animation_in: str = "zoom",
    animation_out: str = "zoom",
) -> Source:
    return Source(
        SourceType.ALBUM_COVER,
        "Album Cover",
        x=x,
        y=y,
        width=size,
        height=size,
        fill_color=color,
        border_radius=radius,
        album_frame_style=frame,
        shadow=Shadow(True, "#000000", 20, 8, 10, 0.32),
        z_index=z,
        animation_in=animation_in,
        animation_out=animation_out,
        animation_duration=0.55,
    )


def _progress(
    x: float,
    y: float,
    width: float,
    color: str,
    z: int,
    *,
    style: str = "rounded",
    track_color: str = "#344050",
) -> Source:
    return Source(
        SourceType.PROGRESS_BAR,
        "Progress",
        x=x,
        y=y,
        width=width,
        height=12,
        fill_color=color,
        progress_track_color=track_color,
        border_radius=8,
        progress_style=style,
        z_index=z,
        animation_in="fade",
        animation_out="fade",
        animation_duration=0.35,
    )


def _visualizer(
    x: float,
    y: float,
    width: float,
    height: float,
    color: str,
    z: int,
    *,
    style: str = "bars",
    bars: int = 44,
) -> Source:
    return Source(
        SourceType.AUDIO_VISUALIZER,
        "Audio Visualizer",
        x=x,
        y=y,
        width=width,
        height=height,
        fill_color=color,
        visualizer_style=style,
        visualizer_bars=bars,
        visualizer_line_width=3.0,
        z_index=z,
        animation_in="fade",
        animation_out="fade",
        animation_duration=0.5,
    )


def _waveform(x: float, y: float, width: float, height: float, color: str, z: int, *,
              style: str = "line", points: int = 64) -> Source:
    """Create an audio-reactive horizontal waveform source."""
    return Source(
        SourceType.AUDIO_WAVEFORM, "Audio Waveform", x=x, y=y, width=width, height=height,
        fill_color=color, waveform_style=style, visualizer_bars=points,
        visualizer_line_width=3.0, z_index=z, animation_in="fade", animation_out="fade",
    )


def _meter(x: float, y: float, width: float, height: float, color: str, z: int, *,
           mode: str = "stereo") -> Source:
    """Create a compact audio level meter source."""
    return Source(
        SourceType.AUDIO_LEVEL_METER, "Audio Level Meter", x=x, y=y, width=width, height=height,
        fill_color=color, level_meter_mode=mode, z_index=z, animation_in="fade", animation_out="fade",
    )


def _particles(color: str, z: int, *, style: str = "dust", density: int = 38,
               speed: float = 0.75, opacity: float = 0.45) -> Source:
    """Create a full-artboard animated texture overlay."""
    return Source(
        SourceType.PARTICLE_OVERLAY, "Particles", width=1280, height=720, fill_color=color,
        opacity=opacity, particle_style=style, particle_density=density, particle_speed=speed,
        z_index=z, locked=True,
    )


def _track_list(x: float, y: float, width: float, height: float, color: str, z: int, *,
                count: int = 4, style: str = "compact") -> Source:
    """Create a dynamic previous/current/next track list."""
    card_style = style in {"cards", "glass", "pills"}
    return Source(
        SourceType.TRACK_LIST, "Track List", x=x, y=y, width=width, height=height,
        fill_color=TRANSPARENT, outline_color=color, font_size=15, text_alignment="left",
        text="▶ 01. Current track\n  02. Next track", track_list_count=count,
        track_list_style=style, track_list_current_color="#FFFFFF" if card_style else color,
        track_list_inactive_color=color,
        track_list_current_background=color if card_style else "#1685D1",
        track_list_inactive_opacity=0.58, track_list_row_spacing=5.0,
        z_index=z,
    )


def _now_playing(x: float, y: float, width: float, height: float, color: str, z: int, *,
                 style: str = "card", seconds: float = 3.0,
                 exit_style: str = "fade") -> Source:
    """Create a track-start announcement card."""
    return Source(
        SourceType.NOW_PLAYING, "Now Playing", x=x, y=y, width=width, height=height,
        fill_color=color, outline_color="#FFFFFF", border_radius=18, font_size=18,
        text="NOW PLAYING\nTrack title\nArtist", now_playing_style=style,
        now_playing_duration=seconds, now_playing_exit_animation=exit_style,
        now_playing_exit_duration=min(0.45, seconds * 0.35), z_index=z,
        animation_in="slide_right", animation_out="fade", animation_duration=0.35,
    )


def _lyrics(x: float, y: float, width: float, height: float, color: str, z: int, *,
            style: str = "karaoke") -> Source:
    """Create a streaming-style previous/current/next lyric source."""
    return Source(
        SourceType.LYRICS, "Lyrics", x=x, y=y, width=width, height=height,
        fill_color=TRANSPARENT, outline_color=color, text="Lyrics are not available for this track.",
        font_size=23, font_weight=600, text_alignment="center", subtitle_style=style,
        subtitle_animation="scroll_up", subtitle_animation_duration=0.28,
        subtitle_context_lines=1, subtitle_next_lines=1, subtitle_previous_opacity=0.34,
        subtitle_previous_blur=1.5, z_index=z,
    )


def aurora() -> list[Source]:
    """Teal-to-violet gradient with a large centred cover and mirrored waveform."""
    return [
        _background("#0B1F2A", "#3B1F55"),
        _particles("#59E0C7", -1, style="dust", density=30, speed=0.4, opacity=0.2),
        _cover(475, 54, 330, "#2FC6B6", 2, radius=28),
        _text("Playlist label", "AURORA MIX · %track% / %track_total%", 300, 400, 680, 28, "#7FE9D8", 15, 3, alignment="center"),
        _text("Song title", "%title%", 200, 436, 880, 78, "#FFFFFF", 38, 4, alignment="center", weight=700),
        _text("Artist", "%artist% — %album%", 220, 518, 840, 32, "#C9B6E6", 18, 5, alignment="center"),
        _waveform(140, 574, 1000, 66, "#59E0C7", 6, style="mirror", points=80),
        _progress(240, 652, 800, "#B98CE8", 7, style="apple", track_color="#2A3A4A"),
        _text("Time", "%current_time% / %total_time%", 240, 676, 800, 22, "#8FA6B8", 12, 8, alignment="center"),
    ]


def cassette() -> list[Source]:
    """Retro mixtape layout on a warm cream sleeve with a scrolling track list."""
    return [
        _background("#2B2016", "#4A3722"),
        _panel("Sleeve", 66, 88, 700, 512, "#F2E4CC", 0, 16),
        _text("Label", "SIDE A · MIXTAPE", 108, 126, 540, 34, "#8A6A3C", 17, 3),
        _text("Song title", "%title%", 108, 176, 620, 96, "#2B2016", 40, 4, weight=800, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist", "%artist%", 108, 282, 560, 34, "#6E5636", 19, 5, animation_in="slide_right", animation_out="slide_left"),
        _text("Album", "%album%", 108, 322, 560, 28, "#9A8055", 14, 6),
        _visualizer(108, 398, 616, 58, "#C98A3C", 7, style="bars", bars=32),
        _progress(108, 494, 616, "#8A6A3C", 8, style="rounded", track_color="#D8C4A2"),
        _text("Time", "%current_time% / %total_time%", 108, 520, 616, 26, "#9A8055", 13, 9),
        _cover(820, 132, 320, "#C98A3C", 2, radius=14, frame="polaroid", animation_in="slide_left", animation_out="slide_right"),
        _track_list(820, 496, 340, 132, "#E7C79A", 10, count=3, style="scroll"),
    ]


def midnight() -> list[Source]:
    """Deep-navy typographic layout with a hairline progress bar."""
    return [
        _background("#0A1120", "#111C33"),
        _text("Playlist", "PLAYLIST / %track%", 130, 128, 700, 32, "#5B7CB0", 15, 1),
        _text("Song title", "%title%", 128, 192, 940, 130, "#F4F7FF", 54, 2, weight=700, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist", "%artist%", 130, 336, 720, 40, "#AEBEDC", 22, 3, animation_in="fade", animation_out="fade"),
        _text("Album", "%album%", 130, 384, 720, 30, "#6E7F9E", 15, 4),
        _visualizer(130, 466, 880, 42, "#3E64A8", 5, style="line", bars=48),
        _progress(130, 544, 880, "#7FA8E6", 6, style="youtube", track_color="#1E2C46"),
        _text("Time", "%current_time%", 130, 566, 420, 24, "#6E7F9E", 12, 7),
        _text("Total", "%total_time% · %track% / %track_total%", 660, 566, 350, 24, "#6E7F9E", 12, 7, alignment="right"),
    ]


def bubblegum() -> list[Source]:
    """Pastel pink and mint card with a plump capsule visualizer."""
    return [
        _background("#FFE3F1", "#DDF6F0"),
        _panel("Card", 150, 88, 980, 544, "#FFFFFF", 0, 40, 0.92),
        _particles("#FFC1DE", 1, style="dust", density=26, speed=0.35, opacity=0.3),
        _cover(210, 162, 300, "#FF9CC9", 2, radius=40),
        _text("Playlist", "SWEET BEATS · %track% / %track_total%", 560, 165, 480, 32, "#FF6FB0", 15, 3),
        _text("Song title", "%title%", 560, 216, 500, 100, "#3A2E36", 36, 4, weight=800, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist", "%artist%", 560, 326, 480, 34, "#8A6E80", 19, 5, animation_in="slide_right", animation_out="slide_left"),
        _visualizer(560, 396, 480, 74, "#57D2C0", 6, style="capsule", bars=22),
        _progress(210, 548, 860, "#FF8FC4", 7, style="apple", track_color="#F0D7E5"),
        _text("Time", "%current_time% / %total_time%", 210, 574, 860, 26, "#8A6E80", 13, 8, alignment="center"),
    ]


def noir() -> list[Source]:
    """Black-and-white cinema layout with letterbox bars and a lower third."""
    return [
        _background("#050505", "#1E1E1E"),
        _particles("#FFFFFF", -1, style="dust", density=18, speed=0.22, opacity=0.10),
        _panel("Top bar", 0, 0, 1280, 66, "#000000", 0, 0),
        _panel("Bottom bar", 0, 654, 1280, 66, "#000000", 1, 0),
        _panel("Lower third", 0, 430, 1280, 224, "#0A0A0A", 2, 0, 0.72),
        _cover(90, 456, 168, "#8C8C8C", 3, radius=4, animation_in="slide_right", animation_out="slide_left"),
        _text("Playlist", "NOIR SESSION  ·  TRACK %track% / %track_total%", 300, 452, 780, 26, "#9C9C9C", 13, 4),
        _text("Song title", "%title%", 300, 486, 840, 66, "#FFFFFF", 34, 5, weight=700, animation_in="slide_up", animation_out="slide_down"),
        _text("Artist album", "%artist%  —  %album%", 300, 560, 780, 30, "#C8C8C8", 16, 6, animation_in="fade", animation_out="fade"),
        _progress(300, 608, 840, "#FFFFFF", 7, style="rounded", track_color="#333333"),
        _text("Time", "%current_time% / %total_time%", 300, 626, 840, 22, "#8C8C8C", 12, 8, alignment="right"),
    ]


def sunset() -> list[Source]:
    """Warm orange-to-purple scene with a circular cover and dotted visualizer."""
    return [
        _background("#FF7E3D", "#5B2A86"),
        _panel("Info", 636, 118, 500, 484, "#2A123F", 0, 30, 0.7),
        _cover(118, 160, 400, "#FFB067", 2, radius=200),
        _text("Playlist", "SUNSET DRIVE", 688, 172, 420, 30, "#FFC48A", 16, 3),
        _text("Song title", "%title%", 688, 228, 424, 96, "#FFFFFF", 31, 4, weight=700, animation_in="slide_left", animation_out="slide_right"),
        _text("Artist", "%artist%", 688, 336, 420, 32, "#F0CBE6", 18, 5, animation_in="slide_left", animation_out="slide_right"),
        _text("Album", "%album%", 688, 374, 420, 26, "#C79ECB", 14, 6),
        _visualizer(688, 436, 420, 58, "#FFB067", 7, style="dots", bars=26),
        _progress(688, 526, 420, "#FF9F5A", 8, style="rounded", track_color="#4A2A5E"),
        _text("Track", "%track% / %track_total% · %current_time%", 688, 554, 420, 26, "#C79ECB", 13, 9),
    ]


def terminal() -> list[Source]:
    """CRT-green monospace console with a spectrum readout and track list."""
    return [
        _background("#02110A", "#04240F"),
        _panel("Console", 68, 60, 1144, 600, "#031A0E", 0, 8, 0.9),
        _text("Prompt", "user@playlist:~$ now-playing --track %track%", 108, 92, 940, 28, "#3BE07A", 14, 2, animation_in="fade", animation_out="fade"),
        _text("Song title", "%title%", 108, 138, 940, 66, "#B6FFD1", 33, 3, weight=700, animation_in="fade", animation_out="fade"),
        _text("Artist album", "  artist: %artist%    album: %album%", 108, 214, 940, 26, "#59C98B", 15, 4, animation_in="fade", animation_out="fade"),
        _visualizer(108, 286, 1000, 150, "#3BE07A", 5, style="spectrum", bars=64),
        _track_list(108, 470, 1000, 96, "#59C98B", 6, count=3, style="compact"),
        _progress(108, 590, 1000, "#3BE07A", 7, style="youtube", track_color="#0C3A1E"),
        _text("Time", "[ %current_time% / %total_time% ]", 108, 614, 1000, 24, "#59C98B", 12, 8, alignment="right"),
    ]


def gallery() -> list[Source]:
    """Quiet white-gallery layout with a framed cover and museum caption."""
    return [
        _background("#F4F1EA"),
        _panel("Wall shadow", 456, 78, 372, 360, "#E4DFD4", 0, 4),
        _cover(474, 90, 336, "#C9BFA8", 2, radius=6, frame="polaroid", animation_in="fade", animation_out="fade"),
        _text("Index", "No. %track% / %track_total%", 340, 470, 600, 24, "#9A9484", 13, 5, alignment="center"),
        _text("Caption title", "%title%", 340, 500, 600, 44, "#20201C", 24, 3, alignment="center", weight=700, animation_in="fade", animation_out="fade"),
        _text("Caption meta", "%artist%,  %album%", 340, 546, 600, 28, "#6A665C", 15, 4, alignment="center", animation_in="fade", animation_out="fade"),
        _progress(430, 600, 420, "#20201C", 6, style="rounded", track_color="#D8D2C4"),
        _text("Time", "%current_time% / %total_time%", 430, 622, 420, 22, "#9A9484", 12, 7, alignment="center"),
    ]


def pulse() -> list[Source]:
    """Bold colour-block layout with a large mirrored visualizer."""
    return [
        _background("#12121A"),
        _panel("Color block", 0, 0, 468, 720, "#FF3366", 0, 0),
        _panel("Accent block", 0, 520, 468, 200, "#1F1F2E", 1, 0),
        _cover(70, 118, 328, "#FFFFFF", 2, radius=12, animation_in="slide_right", animation_out="slide_left"),
        _text("Playlist", "PULSE · %track% / %track_total%", 520, 108, 700, 32, "#FF3366", 16, 3),
        _text("Song title", "%title%", 520, 158, 700, 132, "#FFFFFF", 52, 4, weight=800, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist", "%artist%", 520, 320, 660, 40, "#B7B7C6", 21, 5, animation_in="fade", animation_out="fade"),
        _visualizer(520, 398, 700, 150, "#FF3366", 6, style="mirror", bars=52),
        _progress(520, 598, 700, "#FF3366", 7, style="spotify", track_color="#2A2A3A"),
        _text("Time", "%current_time% / %total_time%", 520, 624, 700, 26, "#8E8E9E", 13, 8),
    ]


def frost() -> list[Source]:
    """Frosted-glass blue layout with a stereo meter and track-start card."""
    return [
        _background("#1C3E5A", "#2E5A7A", mode="album_art", ambient=True),
        _panel("Glass", 150, 92, 980, 532, "#DDEEFF", 0, 30, 0.16),
        _cover(210, 162, 300, "#9FC4E0", 2, radius=26, frame="glass"),
        _text("Playlist", "FROST · %track% / %track_total%", 560, 162, 470, 32, "#E6F3FF", 15, 3),
        _text("Song title", "%title%", 560, 214, 480, 96, "#FFFFFF", 36, 4, weight=700, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist", "%artist%  ·  %album%", 560, 324, 470, 34, "#CFE3F5", 18, 5, animation_in="slide_right", animation_out="slide_left"),
        _meter(1058, 162, 34, 300, "#BFE0F5", 6, mode="stereo"),
        _now_playing(560, 388, 470, 96, "#2E5A7A", 7, style="glass", seconds=2.6, exit_style="slide_up"),
        _progress(210, 544, 820, "#FFFFFF", 8, style="apple", track_color="#BFE0F5"),
        _text("Time", "%current_time% / %total_time%", 210, 570, 820, 26, "#CFE3F5", 13, 9, alignment="center"),
    ]


class PresetService:
    """Exposes the track-aware visual preset catalog."""

    _presets = [
        PresetDefinition("aurora", "오로라", "Aurora", "청록–보라 그라데이션과 중앙 대형 커버, 미러 파형", "Teal-violet gradient with a centred cover and mirrored waveform", aurora),
        PresetDefinition("cassette", "카세트", "Cassette", "크림·브라운 레트로 카세트와 스크롤 트랙 목록", "Retro cream-and-brown mixtape with a scrolling track list", cassette),
        PresetDefinition("midnight", "미드나잇", "Midnight", "딥네이비 타이포 중심 화면과 얇은 진행 표시줄", "Deep-navy type-first screen with a hairline progress bar", midnight),
        PresetDefinition("bubblegum", "버블검", "Bubblegum", "파스텔 핑크·민트 라운드 카드와 캡슐 비주얼라이저", "Pastel pink-and-mint card with a capsule visualizer", bubblegum),
        PresetDefinition("noir", "느와르", "Noir", "흑백 시네마 레터박스와 로우어서드 타이포", "Black-and-white cinema letterbox with a lower third", noir),
        PresetDefinition("sunset", "선셋", "Sunset", "오렌지→퍼플 하늘과 원형 커버, 도트 비주얼라이저", "Orange-to-purple sky with a circular cover and dotted visualizer", sunset),
        PresetDefinition("terminal", "터미널", "Terminal", "CRT 그린 모노스페이스 콘솔과 스펙트럼, 트랙 목록", "CRT-green monospace console with a spectrum and track list", terminal),
        PresetDefinition("gallery", "갤러리", "Gallery", "화이트 갤러리 벽의 액자형 커버와 캡션 메타데이터", "Framed cover on a white gallery wall with a museum caption", gallery),
        PresetDefinition("pulse", "펄스", "Pulse", "볼드 컬러블록과 대형 미러 비주얼라이저", "Bold colour-block layout with a large mirrored visualizer", pulse),
        PresetDefinition("frost", "프로스트", "Frost", "프로스트 글래스 블루 톤과 스테레오 레벨미터, 곡 시작 카드", "Frosted-glass blue tones with a stereo meter and track-start card", frost),
    ]

    @classmethod
    def all(cls) -> list[PresetDefinition]:
        """Return all supported preset definitions."""
        return list(cls._presets)
