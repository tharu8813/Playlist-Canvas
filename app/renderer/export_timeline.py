"""Build the ordered visual sample plan used by preview-frame export."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType


@dataclass(frozen=True, slots=True)
class ExportFrameSample:
    """One Canvas state and the exact duration it occupies on the timeline."""

    track: PlaylistTrack
    track_number: int
    track_start_seconds: float
    duration_seconds: float
    elapsed_seconds: float
    timeline_seconds: float
    animation_phase: str | None = None
    animation_progress: float = 1.0
    animation_phase_duration: float = 0.0


class ExportTimelinePlanner:
    """Reproduce the existing export sampling schedule without rendering pixels."""

    @staticmethod
    def build(
        tracks: Sequence[PlaylistTrack],
        sources: Sequence[Source],
        animation_fps: int,
    ) -> list[ExportFrameSample]:
        samples: list[ExportFrameSample] = []
        cursor = 0.0
        previous_track: PlaylistTrack | None = None
        previous_start = 0.0
        previous_number = 1

        for number, track in enumerate(tracks, start=1):
            requested = (
                track.start_time_seconds
                if track.start_time_seconds is not None else cursor
            )
            start = max(cursor, requested)
            intro, outro = ExportTimelinePlanner._animation_durations(track, sources)
            gap = max(0.0, start - cursor)
            if gap > 0.0:
                gap_track = previous_track or track
                gap_elapsed = (
                    gap_track.duration_seconds if previous_track is not None else 0.0
                )
                gap_points = {cursor, start}
                for source in sources:
                    for boundary in (
                        source.timeline_start,
                        source.timeline_start + source.timeline_duration,
                    ):
                        if cursor < boundary < start and (
                            boundary == source.timeline_start
                            or source.timeline_duration > 0.0
                        ):
                            gap_points.add(boundary)
                gap_phase = "out" if previous_track is not None else "in"
                if previous_track is not None:
                    previous_intro, gap_phase_duration = (
                        ExportTimelinePlanner._animation_durations(
                            previous_track, sources,
                        )
                    )
                    # Keep the old formula explicit: the previous intro bounds
                    # how much of that track remains available to its outro.
                    gap_phase_duration = min(
                        (previous_track.duration_seconds - previous_intro) / 2,
                        gap_phase_duration,
                    )
                else:
                    gap_phase_duration = intro
                ordered_gap_points = sorted(gap_points)
                for point, next_point in zip(
                    ordered_gap_points, ordered_gap_points[1:]
                ):
                    samples.append(ExportFrameSample(
                        track=gap_track,
                        track_number=(previous_number if previous_track else number),
                        track_start_seconds=(
                            previous_start if previous_track else start
                        ),
                        duration_seconds=next_point - point,
                        elapsed_seconds=gap_elapsed,
                        timeline_seconds=min(
                            next_point - 0.0005, point + 0.0005,
                        ),
                        animation_phase=(
                            gap_phase if gap_phase_duration > 0.0 else None
                        ),
                        animation_progress=(
                            1.0 if gap_phase == "out" else 0.0
                        ),
                        animation_phase_duration=max(0.0, gap_phase_duration),
                    ))

            samples.extend(ExportTimelinePlanner._animation_samples(
                track, number, start, intro, outro, "in", intro, animation_fps,
            ))

            stable = max(0.0, track.duration_seconds - intro - outro)
            sample_points = ExportTimelinePlanner._stable_sample_points(
                track, start, intro, stable, sources, animation_fps,
            )
            ordered_points = sorted(sample_points)
            for point, next_point in zip(ordered_points, ordered_points[1:]):
                elapsed = min(next_point - 0.0005, point + 0.0005)
                samples.append(ExportFrameSample(
                    track=track,
                    track_number=number,
                    track_start_seconds=start,
                    duration_seconds=next_point - point,
                    elapsed_seconds=elapsed,
                    timeline_seconds=start + elapsed,
                ))

            samples.extend(ExportTimelinePlanner._animation_samples(
                track, number, start, intro, outro, "out", outro, animation_fps,
            ))
            cursor = start + track.duration_seconds
            previous_track = track
            previous_start = start
            previous_number = number
        return samples

    @staticmethod
    def _animation_durations(
        track: PlaylistTrack, sources: Sequence[Source],
    ) -> tuple[float, float]:
        intro = min(
            track.duration_seconds / 2,
            max(
                (source.animation_in_duration for source in sources
                 if source.animation_in != "none"),
                default=0.0,
            ),
        )
        outro = min(
            (track.duration_seconds - intro) / 2,
            max(
                (source.animation_out_duration for source in sources
                 if source.animation_out != "none"),
                default=0.0,
            ),
        )
        return intro, outro

    @staticmethod
    def _animation_samples(
        track: PlaylistTrack,
        track_number: int,
        start: float,
        intro: float,
        outro: float,
        phase: str,
        duration: float,
        animation_fps: int,
    ) -> list[ExportFrameSample]:
        if duration <= 0:
            return []
        steps = max(2, round(duration * animation_fps))
        samples: list[ExportFrameSample] = []
        for step in range(steps):
            # Include both animation endpoints.  The old ``step / steps``
            # contract never captured progress 1.0, so a short exit could end
            # on a visibly opaque frame and then disappear at the next cut.
            progress = step / (steps - 1)
            elapsed = (
                duration * step / steps
                if phase == "in"
                else intro + max(0.0, track.duration_seconds - intro - outro)
                + duration * step / steps
            )
            samples.append(ExportFrameSample(
                track=track,
                track_number=track_number,
                track_start_seconds=start,
                duration_seconds=duration / steps,
                elapsed_seconds=elapsed,
                timeline_seconds=start + elapsed,
                animation_phase=phase,
                animation_progress=progress,
                animation_phase_duration=duration,
            ))
        return samples

    @staticmethod
    def _stable_sample_points(
        track: PlaylistTrack,
        start: float,
        intro: float,
        stable: float,
        sources: Sequence[Source],
        animation_fps: int,
    ) -> set[float]:
        sample_points = {intro, intro + stable}
        for source in sources:
            boundaries = [source.timeline_start]
            if source.timeline_duration > 0.0:
                boundaries.append(source.timeline_start + source.timeline_duration)
            for boundary in boundaries:
                local_point = boundary - start
                if intro < local_point < intro + stable:
                    sample_points.add(local_point)

        if any(source.source_type is SourceType.PROGRESS_BAR for source in sources):
            progress_steps = min(180, max(1, round(stable)))
            sample_points.update(
                intro + stable * step / progress_steps
                for step in range(progress_steps + 1)
            )

        has_time_text = any(
            source.source_type is SourceType.TIME
            or (
                source.source_type is SourceType.TEXT
                and any(
                    token in source.text.lower()
                    for token in (
                        "%current_time%", "%track_current_time%",
                        "%video_current_time%",
                    )
                )
            )
            for source in sources
        )
        if has_time_text:
            first_local_second = max(1, int(intro) + 1)
            last_local_second = int(intro + stable)
            sample_points.update(
                float(second)
                for second in range(first_local_second, last_local_second + 1)
                if intro < second < intro + stable
            )
            first_global_second = max(1, int(start + intro) + 1)
            last_global_second = int(start + intro + stable)
            sample_points.update(
                float(second) - start
                for second in range(first_global_second, last_global_second + 1)
                if intro < float(second) - start < intro + stable
            )

        lyric_sources = [
            source for source in sources
            if source.source_type is SourceType.LYRICS
        ]
        if lyric_sources:
            track_offset = track.lyrics_timing_offset_seconds
            for cue in track.lyrics:
                for point in (
                    float(cue.get("start", 0.0)),
                    float(cue.get("end", 0.0)),
                ):
                    for lyric_source in lyric_sources:
                        adjusted_point = (
                            point - track_offset
                            - lyric_source.subtitle_timing_offset
                        )
                        if intro < adjusted_point < intro + stable:
                            sample_points.add(adjusted_point)
                cue_start = float(cue.get("start", 0.0))
                for lyric_source in lyric_sources:
                    if lyric_source.subtitle_animation == "none":
                        continue
                    steps = max(
                        1,
                        round(
                            lyric_source.subtitle_animation_duration
                            * animation_fps
                        ),
                    )
                    for step in range(steps + 1):
                        point = (
                            cue_start - track_offset
                            - lyric_source.subtitle_timing_offset
                            + lyric_source.subtitle_animation_duration
                            * step / steps
                        )
                        if intro < point < intro + stable:
                            sample_points.add(point)

        for source in (
            item for item in sources
            if item.source_type is SourceType.NOW_PLAYING
        ):
            exit_start = max(
                0.0,
                source.now_playing_duration - source.now_playing_exit_duration,
            )
            steps = max(
                1, round(source.now_playing_exit_duration * animation_fps),
            )
            for step in range(steps + 1):
                point = (
                    exit_start
                    + source.now_playing_exit_duration * step / steps
                )
                if intro < point < intro + stable:
                    sample_points.add(point)
        return sample_points
