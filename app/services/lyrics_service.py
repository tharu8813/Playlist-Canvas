"""Timed LRC and SRT lyric parsing utilities."""

from __future__ import annotations

import re
from pathlib import Path


class LyricsError(ValueError):
    """Raised when a lyric file cannot be read as LRC or SRT."""


class LyricsService:
    """Load and resolve timed lyric cues for the active playlist track."""

    _lrc_time = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
    _srt_time = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")

    @classmethod
    def load(cls, path: str | Path) -> list[dict[str, float | str]]:
        lyric_path = Path(path)
        try:
            content = lyric_path.read_text(encoding="utf-8-sig")
        except OSError as error:
            raise LyricsError(str(error)) from error
        suffix = lyric_path.suffix.lower()
        if suffix == ".lrc":
            return cls._parse_lrc(content)
        if suffix in {".srt", ".vtt"}:
            return cls._parse_srt(content)
        raise LyricsError("Only .lrc, .srt, and .vtt lyric files are supported.")

    @classmethod
    def current_text(cls, cues: list[dict[str, object]], elapsed: float) -> str:
        """Return the cue active at elapsed seconds, or an empty string."""
        cue = cls.current_cue(cues, elapsed)
        return str(cue.get("text", "")) if cue else ""

    @classmethod
    def current_cue(cls, cues: list[dict[str, object]], elapsed: float) -> dict[str, object] | None:
        """Return the exact active cue, with a tiny tolerance for frame rounding."""
        index = cls.current_cue_index(cues, elapsed)
        return cues[index] if index is not None else None

    @classmethod
    def current_cue_index(cls, cues: list[dict[str, object]], elapsed: float) -> int | None:
        """Return the active cue index in O(log n) with safe frame boundaries."""
        if not cues:
            return None
        precision = 0.0005
        # Cues are normalized into start-time order by parsers and project
        # validation. Binary search avoids scanning a long subtitle file for
        # every preview/export frame.
        low = 0
        high = len(cues)
        target = elapsed + precision
        while low < high:
            middle = (low + high) // 2
            if float(cues[middle].get("start", 0.0)) <= target:
                low = middle + 1
            else:
                high = middle
        index = low - 1
        if index >= 0:
            cue = cues[index]
            start = float(cue.get("start", 0.0))
            end = float(cue.get("end", start + 8.0))
            if start - precision <= elapsed < end - precision:
                return index
        return None

    @classmethod
    def display_cue_index(cls, cues: list[dict[str, object]], elapsed: float) -> int | None:
        """Return a lyric to display continuously whenever the track has lyrics.

        Timed subtitle files can leave silence before the first cue or gaps between
        cues.  A Lyrics source should not fall back to its editor placeholder during
        those gaps: show the first cue from track start, then retain the most recent
        cue until the next one begins.
        """
        if not cues:
            return None
        active_index = cls.current_cue_index(cues, elapsed)
        if active_index is not None:
            return active_index
        precision = 0.0005
        low = 0
        high = len(cues)
        target = elapsed + precision
        while low < high:
            middle = (low + high) // 2
            if float(cues[middle].get("start", 0.0)) <= target:
                low = middle + 1
            else:
                high = middle
        return max(0, low - 1)

    @classmethod
    def _parse_lrc(cls, content: str) -> list[dict[str, float | str]]:
        cues: list[dict[str, float | str]] = []
        for line in content.splitlines():
            text = cls._lrc_time.sub("", line).strip()
            for minute, second in cls._lrc_time.findall(line):
                cues.append({"start": int(minute) * 60 + float(second), "end": 0.0, "text": text})
        cues.sort(key=lambda cue: float(cue["start"]))
        for index, cue in enumerate(cues):
            cue["end"] = float(cues[index + 1]["start"]) if index + 1 < len(cues) else float(cue["start"]) + 8.0
        return cues

    @classmethod
    def _parse_srt(cls, content: str) -> list[dict[str, float | str]]:
        cues: list[dict[str, float | str]] = []
        for block in re.split(r"\r?\n\s*\r?\n", content.strip()):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if len(lines) < 2:
                continue
            timing_index = 1 if lines[0].isdigit() else 0
            if timing_index >= len(lines) or "-->" not in lines[timing_index]:
                continue
            start_raw, end_raw = (part.strip() for part in lines[timing_index].split("-->", 1))
            cues.append({"start": cls._srt_seconds(start_raw), "end": cls._srt_seconds(end_raw), "text": "\n".join(lines[timing_index + 1:])})
        cues.sort(key=lambda cue: (float(cue["start"]), float(cue["end"])))
        return cues

    @classmethod
    def _srt_seconds(cls, value: str) -> float:
        match = cls._srt_time.search(value)
        if not match:
            raise LyricsError(f"Invalid subtitle timestamp: {value}")
        hours, minutes, seconds, milliseconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000
