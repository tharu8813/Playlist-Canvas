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
        locked=True,
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


def spotify() -> list[Source]:
    """Album-led dark layout with left cover and right metadata stack."""
    return [
        _background("#121212", "#1B2530"),
        _panel("Metadata card", 520, 128, 650, 460, "#1F1F1F", 0, 28),
        _cover(105, 150, 370, "#1DB954", 2),
        _text("Playlist label", "PLAYLIST · %track% / %track_total%", 570, 175, 500, 36, "#1DB954", 16, 3),
        _text("Song title", "%title%", 570, 238, 530, 94, "#FFFFFF", 42, 4, weight=700, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist", "%artist%", 570, 348, 480, 42, "#D1D5DB", 21, 5, animation_in="slide_right", animation_out="slide_left"),
        _text("Album", "%album%", 570, 396, 480, 34, "#8E9AA6", 16, 6, animation_in="slide_right", animation_out="slide_left"),
        _visualizer(570, 456, 500, 54, "#1DB954", 7, style="bars", bars=36),
        _progress(570, 544, 500, "#1DB954", 8, style="spotify", track_color="#2C3741"),
        _text("Time", "%current_time% / %total_time%", 570, 566, 500, 28, "#9CA3AF", 13, 9, alignment="right"),
    ]


def apple_music() -> list[Source]:
    """Bright, centered editorial cover layout."""
    return [
        _background("#F6F7FB", "#E5E9F5", mode="album_art", ambient=True),
        _panel("Center card", 340, 55, 600, 610, "#FFFFFF", 0, 34),
        _cover(490, 105, 300, "#FA2D55", 2, radius=34),
        _text("Playlist label", "NOW PLAYING", 420, 430, 440, 30, "#FA2D55", 14, 3, alignment="center"),
        _text("Song title", "%title%", 390, 470, 500, 72, "#19191C", 36, 4, alignment="center", weight=700, animation_in="zoom", animation_out="fade"),
        _text("Artist", "%artist%", 400, 547, 480, 34, "#555764", 19, 5, alignment="center", animation_in="fade", animation_out="fade"),
        _text("Album", "%album%", 400, 583, 480, 28, "#858794", 14, 6, alignment="center"),
        _progress(425, 625, 430, "#FA2D55", 7, style="apple", track_color="#D9DCE5"),
        _text("Playback time", "%current_time% / %total_time%  ·  %track% of %track_total%", 425, 646, 430, 23, "#858794", 12, 8, alignment="center"),
    ]


def k_pop() -> list[Source]:
    """Poster-like diagonal composition with bold visualizer."""
    return [
        _background("#250D50", "#ED3D9A"),
        _panel("Title plate", 62, 72, 650, 210, "#16062E", 0, 30, 0.72),
        _cover(790, 95, 345, "#6E42FF", 2, radius=16, animation_in="slide_left", animation_out="slide_right"),
        _text("Playlist label", "K-POP // %track%", 105, 108, 530, 32, "#FFD53D", 17, 3),
        _text("Song title", "%title%", 105, 150, 555, 95, "#FFFFFF", 47, 4, weight=800, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist album", "%artist% — %album%", 105, 248, 555, 38, "#F7C8E2", 18, 5, animation_in="slide_right", animation_out="slide_left"),
        _visualizer(100, 378, 1030, 126, "#FFD53D", 6, style="mirror", bars=58),
        _progress(100, 565, 1030, "#FFFFFF", 7, style="rounded", track_color="#633080"),
        _text("Time", "%current_time%", 100, 588, 500, 28, "#F7C8E2", 13, 8),
        _text("Total time", "%total_time%", 630, 588, 500, 28, "#F7C8E2", 13, 8, alignment="right"),
    ]


def lofi() -> list[Source]:
    """Warm asymmetrical late-night layout with the cover on the right."""
    return [
        _background("#273041", "#705543"),
        _panel("Text area", 90, 125, 590, 450, "#182232", 0, 26, 0.7),
        _cover(760, 145, 350, "#B6825B", 2, radius=48, animation_in="slide_left", animation_out="slide_right"),
        _text("Playlist", "late night beats", 135, 175, 430, 42, "#D9B58E", 24, 3, surface=TRANSPARENT),
        _text("Song title", "%title%", 135, 260, 485, 92, "#FFF5E9", 40, 4, weight=600, animation_in="slide_up", animation_out="slide_down"),
        _text("Artist", "%artist%", 135, 365, 420, 38, "#E9D7C3", 19, 5, animation_in="slide_up", animation_out="slide_down"),
        _text("Album", "%album%", 135, 410, 420, 30, "#BFC2C8", 15, 6),
        _visualizer(135, 468, 420, 45, "#D5A16D", 7, style="wave", bars=30),
        _progress(135, 535, 420, "#D5A16D", 8, style="rounded", track_color="#4E5967"),
        _text("Track", "track %track% · %current_time% / %total_time%", 135, 555, 420, 28, "#BFC2C8", 13, 9),
    ]


def minimal() -> list[Source]:
    """Cover-free typography-first layout for a clean playlist video."""
    return [
        _background("#F8FAFC"),
        _panel("Accent line", 104, 125, 12, 425, "#1E293B", 0, 6),
        _text("Playlist", "PLAYLIST / %track%", 150, 130, 680, 36, "#64748B", 15, 1),
        _text("Song title", "%title%", 150, 205, 880, 120, "#0F172A", 56, 2, weight=700, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist", "%artist%", 150, 350, 650, 42, "#334155", 23, 3, animation_in="fade", animation_out="fade"),
        _text("Album", "%album%", 150, 403, 650, 32, "#64748B", 16, 4),
        _progress(150, 535, 830, "#0F172A", 5, style="youtube", track_color="#CBD5E1"),
        _text("Time", "%current_time%", 150, 558, 360, 26, "#64748B", 13, 6),
        _text("Total", "%total_time%", 620, 558, 360, 26, "#64748B", 13, 6, alignment="right"),
        _text("Counter", "%track% / %track_total%", 1030, 535, 130, 45, "#0F172A", 18, 7, alignment="right"),
    ]


def neon() -> list[Source]:
    """High-energy visualizer-first layout with small cover art."""
    return [
        _background("#070711", "#1B1038"),
        _panel("Neon frame", 65, 62, 1150, 595, "#0C0C1D", 0, 30, 0.82),
        _cover(110, 110, 210, "#0DCAF0", 2, radius=20),
        _text("Playlist", "NEON FREQUENCY · %track% / %track_total%", 365, 115, 650, 34, "#0DCAF0", 17, 3),
        _text("Song title", "%title%", 365, 165, 700, 76, "#FFFFFF", 39, 4, weight=700, animation_in="zoom", animation_out="zoom"),
        _text("Artist album", "%artist%  //  %album%", 365, 252, 650, 34, "#F72585", 17, 5, animation_in="fade", animation_out="fade"),
        _visualizer(135, 360, 1010, 125, "#F72585", 6, style="spectrum", bars=64),
        _progress(135, 555, 1010, "#0DCAF0", 7, style="spotify", track_color="#25203E"),
        _text("Time", "%current_time% / %total_time%", 135, 580, 1010, 26, "#938CB4", 13, 8, alignment="center"),
    ]


def dark_modern() -> list[Source]:
    """Magazine-like split screen, tuned for long metadata names."""
    return [
        _background("#10151E"),
        _panel("Cover zone", 770, 0, 510, 720, "#182231", 0, 0),
        _cover(845, 145, 310, "#3F72AF", 2, radius=10, animation_in="slide_left", animation_out="slide_right"),
        _text("Playlist", "CURATED PLAYLIST", 105, 120, 560, 34, "#77A5D3", 16, 3),
        _text("Song title", "%title%", 105, 205, 570, 135, "#F8FAFC", 48, 4, weight=700, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist", "%artist%", 105, 365, 520, 38, "#C6D3E0", 20, 5, animation_in="slide_right", animation_out="slide_left"),
        _text("Album", "%album%", 105, 415, 520, 32, "#7F93A7", 16, 6),
        _visualizer(105, 482, 540, 48, "#77A5D3", 7, style="line", bars=38),
        _progress(105, 560, 540, "#3F72AF", 8, style="rounded", track_color="#2A3746"),
        _text("Counter", "%track% / %track_total%", 105, 584, 540, 28, "#7F93A7", 13, 9),
    ]


def glassmorphism() -> list[Source]:
    """Layered translucent cards with centered metadata."""
    return [
        _background("#2B5876", "#4E4376"),
        _panel("Glass card", 160, 85, 960, 550, "#EEF6FF", 0, 32, 0.15),
        _cover(235, 170, 300, "#8DA8D8", 2, radius=30),
        _text("Playlist", "GLASS PLAYLIST · %track%", 600, 170, 390, 34, "#E9F6FF", 16, 3),
        _text("Song title", "%title%", 600, 235, 405, 96, "#FFFFFF", 38, 4, weight=700, animation_in="slide_right", animation_out="slide_left"),
        _text("Artist", "%artist%", 600, 350, 385, 36, "#E1EBFF", 19, 5, animation_in="slide_right", animation_out="slide_left"),
        _text("Album", "%album%", 600, 393, 385, 30, "#C5D4EE", 15, 6),
        _visualizer(600, 458, 385, 48, "#C5E7FF", 7, style="wave", bars=36),
        _progress(235, 550, 770, "#FFFFFF", 8, style="apple", track_color="#C5E7FF"),
        _text("Time", "%current_time%", 235, 572, 350, 26, "#E1EBFF", 13, 9),
        _text("Duration", "%total_time%", 655, 572, 350, 26, "#E1EBFF", 13, 9, alignment="right"),
    ]


def radio_wave() -> list[Source]:
    """Broadcast dashboard arrangement with a dominant waveform."""
    return [
        _background("#101E2F", "#1D3557"),
        _panel("Broadcast panel", 65, 65, 1150, 590, "#0B1522", 0, 24, 0.88),
        _text("Station", "ON AIR  •  PLAYLIST RADIO", 110, 108, 610, 32, "#FFB703", 16, 2),
        _text("Song title", "%title%", 110, 160, 800, 75, "#FFFFFF", 39, 3, weight=700, animation_in="slide_up", animation_out="slide_down"),
        _text("Artist album", "%artist% — %album%", 110, 248, 800, 35, "#B8C9DB", 18, 4, animation_in="slide_up", animation_out="slide_down"),
        _cover(955, 105, 180, "#FFB703", 5, radius=90),
        _visualizer(110, 350, 1000, 115, "#FFB703", 6, style="wave", bars=58),
        _progress(110, 535, 1000, "#FFB703", 7, style="youtube", track_color="#2D4862"),
        _text("Elapsed", "%current_time%", 110, 560, 300, 26, "#B8C9DB", 13, 8),
        _text("Remaining", "%total_time%  •  %track% / %track_total%", 670, 560, 440, 26, "#B8C9DB", 13, 8, alignment="right"),
    ]


def cinematic() -> list[Source]:
    """Cinematic lower-third layout which leaves ample room for background art."""
    return [
        _background("#0A0D12", "#27364B"),
        _panel("Lower third", 0, 470, 1280, 250, "#080B10", 0, 0, 0.83),
        _cover(90, 500, 160, "#D4A373", 2, radius=8, animation_in="slide_right", animation_out="slide_left"),
        _text("Playlist", "CINEMATIC PLAYLIST  /  TRACK %track%", 300, 510, 700, 28, "#D4A373", 14, 3),
        _text("Song title", "%title%", 300, 548, 780, 56, "#FFFFFF", 31, 4, weight=700, animation_in="slide_up", animation_out="slide_down"),
        _text("Artist album", "%artist%  ·  %album%", 300, 610, 700, 30, "#CBD5E1", 16, 5, animation_in="fade", animation_out="fade"),
        _progress(300, 662, 790, "#D4A373", 6, style="rounded", track_color="#3A4758"),
        _text("Time", "%current_time% / %total_time%", 860, 510, 230, 26, "#CBD5E1", 12, 7, alignment="right"),
    ]


def vinyl_room() -> list[Source]:
    """Vinyl-inspired arrangement with a circular cover and compact details."""
    return [
        _background("#211D1A", "#493D32"),
        _panel("Info panel", 655, 125, 470, 470, "#312820", 0, 28),
        _cover(130, 160, 390, "#D8A65D", 2, radius=195, animation_in="zoom", animation_out="zoom"),
        _text("Playlist", "VINYL SESSION", 710, 180, 340, 30, "#D8A65D", 16, 3),
        _text("Song title", "%title%", 710, 245, 350, 95, "#FFF7EB", 36, 4, weight=700, animation_in="slide_left", animation_out="slide_right"),
        _text("Artist", "%artist%", 710, 355, 350, 34, "#E6CCAA", 19, 5, animation_in="slide_left", animation_out="slide_right"),
        _text("Album", "%album%", 710, 400, 350, 30, "#AFA091", 15, 6),
        _visualizer(710, 458, 340, 45, "#D8A65D", 7, style="dots", bars=28),
        _progress(710, 535, 340, "#D8A65D", 8, style="rounded", track_color="#59483A"),
        _text("Track", "%track% / %track_total%", 710, 557, 340, 25, "#AFA091", 13, 9, alignment="right"),
    ]


def _enhance_phase5c(sources: list[Source], profile: str) -> list[Source]:
    """Apply the Phase 5 source set to a legacy preset with a distinct composition."""
    cover = next((source for source in sources if source.source_type is SourceType.ALBUM_COVER), None)
    if profile in {"apple_music", "glassmorphism"} and cover:
        cover.album_frame_style = "glass"
    elif profile in {"vinyl_room", "radio_wave"} and cover:
        cover.album_frame_style = "circle"
    elif profile in {"kpop", "dark_modern"} and cover:
        cover.album_frame_style = "polaroid"
    visualizer_style = {
        "spotify": "capsule", "kpop": "led", "neon": "arc",
        "dark_modern": "center", "vinyl_room": "capsule",
    }.get(profile)
    if visualizer_style:
        for source in sources:
            if source.source_type is SourceType.AUDIO_VISUALIZER:
                source.visualizer_style = visualizer_style

    additions: dict[str, list[Source]] = {
        "spotify": [
            _track_list(105, 545, 370, 105, "#B8C6BE", 11, count=3, style="scroll"),
            _now_playing(570, 136, 500, 90, "#176B43", 12, style="glass", seconds=2.6, exit_style="slide_up"),
        ],
        "apple_music": [
            _particles("#FA2D55", 1, style="dust", density=28, speed=0.45, opacity=0.22),
            _now_playing(420, 290, 440, 105, "#FA2D55", 12, style="glass", seconds=2.5, exit_style="zoom"),
            _lyrics(380, 210, 520, 190, "#232632", 13, style="minimal"),
        ],
        "kpop": [
            _particles("#FF7BC6", 1, style="neon", density=72, speed=1.45, opacity=0.38),
            _now_playing(760, 515, 370, 72, "#6E42FF", 12, style="card", seconds=2.0, exit_style="slide_down"),
        ],
        "lofi": [
            _particles("#F4D6AC", 1, style="noise", density=95, speed=0.32, opacity=0.18),
            _track_list(760, 520, 350, 108, "#E9D7C3", 12, count=3, style="scroll"),
            _lyrics(135, 210, 470, 150, "#FFF5E9", 13),
        ],
        "minimal": [
            _track_list(825, 185, 330, 260, "#334155", 10, count=5, style="compact"),
            _now_playing(150, 455, 410, 62, "#0F172A", 11, style="minimal", seconds=2.2, exit_style="fade"),
        ],
        "neon": [
            _particles("#0DCAF0", 1, style="neon", density=105, speed=1.8, opacity=0.45),
            _meter(1160, 365, 28, 120, "#0DCAF0", 12, mode="led"),
            _lyrics(250, 255, 780, 82, "#F4EFFF", 13, style="neon"),
        ],
        "dark_modern": [
            _meter(1165, 155, 22, 310, "#77A5D3", 11, mode="stereo"),
            _track_list(795, 510, 365, 110, "#C6D3E0", 12, count=3, style="compact"),
        ],
        "glassmorphism": [
            _particles("#E9F6FF", 1, style="dust", density=35, speed=0.5, opacity=0.28),
            _now_playing(600, 248, 385, 108, "#89A6D8", 12, style="glass", seconds=2.5, exit_style="slide_up"),
        ],
        "radio_wave": [
            _meter(1140, 350, 35, 115, "#FFB703", 12, mode="led"),
        ],
        "cinematic": [
            _now_playing(300, 340, 530, 98, "#172334", 10, style="glass", seconds=2.8, exit_style="fade"),
            _particles("#D4A373", 1, style="dust", density=24, speed=0.35, opacity=0.16),
            _lyrics(300, 355, 760, 95, "#F8FAFC", 11, style="minimal"),
        ],
        "vinyl_room": [
            _particles("#D8A65D", 1, style="dust", density=54, speed=0.38, opacity=0.28),
            _waveform(130, 585, 390, 48, "#D8A65D", 12, style="mirror", points=52),
        ],
    }
    return [*sources, *additions[profile]]


class PresetService:
    """Exposes the track-aware visual preset catalog."""

    _presets = [
        PresetDefinition("spotify", "Spotify", "Spotify", "파형과 현재/다음 곡 목록을 갖춘 다크 플레이어", "Dark player with waveform and track queue", lambda: _enhance_phase5c(spotify(), "spotify")),
        PresetDefinition("apple_music", "Apple Music", "Apple Music", "글래스 커버와 부드러운 파티클의 중앙 카드", "Centered card with glass cover and soft particles", lambda: _enhance_phase5c(apple_music(), "apple_music")),
        PresetDefinition("kpop", "K-POP", "K-POP", "네온 파티클과 트랙 전환 카드가 있는 포스터", "Poster layout with neon particles and transition card", lambda: _enhance_phase5c(k_pop(), "kpop")),
        PresetDefinition("lofi", "Lo-Fi", "Lo-Fi", "노이즈 질감과 스크롤 트랙 목록의 야간 무드", "Night mood with grain texture and scrolling track list", lambda: _enhance_phase5c(lofi(), "lofi")),
        PresetDefinition("minimal", "Minimal", "Minimal", "타이포그래피와 큐 목록을 강조한 미니멀 화면", "Typography-first screen with a clean queue", lambda: _enhance_phase5c(minimal(), "minimal")),
        PresetDefinition("neon", "Neon", "Neon", "고밀도 네온 파티클과 LED 레벨 미터", "High-energy particles with an LED level meter", lambda: _enhance_phase5c(neon(), "neon")),
        PresetDefinition("dark_modern", "Dark Modern", "Dark Modern", "폴라로이드 커버, 레벨 미터, 컴팩트 큐", "Editorial split screen with meter and compact queue", lambda: _enhance_phase5c(dark_modern(), "dark_modern")),
        PresetDefinition("glassmorphism", "Glassmorphism", "Glassmorphism", "글래스 프레임과 곡 시작 카드를 갖춘 투명 UI", "Glass UI with framed cover and track-start card", lambda: _enhance_phase5c(glassmorphism(), "glassmorphism")),
        PresetDefinition("radio_wave", "라디오 웨이브", "Radio Wave", "대형 가로 파형과 방송용 LED 레벨 미터", "Broadcast layout with waveform and LED level meter", lambda: _enhance_phase5c(radio_wave(), "radio_wave")),
        PresetDefinition("cinematic", "시네마틱", "Cinematic", "미세한 질감과 곡 전환 카드가 있는 시네마틱 화면", "Cinematic lower third with texture and transition card", lambda: _enhance_phase5c(cinematic(), "cinematic")),
        PresetDefinition("vinyl_room", "바이닐 룸", "Vinyl Room", "원형 커버, 먼지 질감, 아날로그 파형", "Analog room with circular cover, dust and waveform", lambda: _enhance_phase5c(vinyl_room(), "vinyl_room")),
    ]

    @classmethod
    def all(cls) -> list[PresetDefinition]:
        """Return all supported preset definitions."""
        return list(cls._presets)
