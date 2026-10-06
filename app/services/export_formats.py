"""Supported media containers and their final audio encoding settings."""

from pathlib import Path


VIDEO_FORMATS = ("mp4", "mov", "mkv")
AUDIO_FORMATS = ("mp3", "m4a", "wav", "flac", "ogg")
SUBTITLE_FORMATS = ("lrc", "srt")
AUDIO_ENCODERS = {
    "mp3": "libmp3lame", "m4a": "aac", "wav": "pcm_s16le",
    "flac": "flac", "ogg": "libvorbis",
}


def is_audio_export(path: str | Path) -> bool:
    return Path(path).suffix.lower().lstrip(".") in AUDIO_FORMATS


def is_subtitle_export(path: str | Path) -> bool:
    return Path(path).suffix.lower().lstrip(".") in SUBTITLE_FORMATS


def audio_encoding_arguments(format_name: str, bitrate: str) -> list[str]:
    arguments = ["-c:a", AUDIO_ENCODERS[format_name]]
    if format_name in {"mp3", "m4a", "ogg"}:
        arguments += ["-b:a", bitrate]
    if format_name == "m4a":
        arguments += ["-movflags", "+faststart"]
    return arguments


def video_container_arguments(path: str | Path) -> list[str]:
    return ["-movflags", "+faststart"] if Path(path).suffix.lower() in {".mp4", ".mov"} else []
