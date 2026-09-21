# AutoMix 준비: Compiled RenderPlan Architecture

## 1. 왜 RenderPlan이 필요한가

`Timeline`(`app/timeline/models.py`)은 사용자가 편집한 논리적 배치다. AutoMix가 들어오면
같은 Timeline이라도 겹치는 클립, 크로스페이드, beat-synced cue 같은 실행 세부사항이
필요해진다. 이 실행 세부사항을 Timeline 자체에 밀어넣으면 편집 모델이 실행 모델로
오염된다. `CompiledRenderPlan`은 그 경계를 명시적으로 긋는다: Timeline을 입력받아
Preview/Export가 그대로 재생할 수 있는 완전히 계산된 상태를 출력하는 순수 함수
(`compile_timeline`)의 결과물이다.

## 2. Timeline과 RenderPlan의 차이

| | Timeline | CompiledRenderPlan |
|---|---|---|
| 역할 | 편집 상태 | 실행 상태 |
| 겹침 표현 | 허용 (`AudioClip.timeline_start` 자유) | Audio Plan에 그대로 반영 |
| Canvas가 무엇을 보여주는지 | 모름 | `PresentationPlan`이 명시 |
| 변경 빈도 | 사용자 편집마다 | Timeline이 바뀔 때마다 재계산 |
| Qt/FFmpeg 의존 | 없음 | 없음 (둘 다 순수 데이터) |

## 3. Audio Plan

`AudioRenderPlan`(`app/timeline/render_plan.py`)은 실제로 믹스될 클립 배치
(`AudioRenderClip`)와 전환 구간(`AudioRenderTransition`)을 담는다. `compile_timeline()`은
`Timeline.transitions`를 그대로 `AudioRenderTransition`으로 변환해 정보를 보존한다 --
`CROSSFADE`/`EQUAL_POWER`/`BEAT_MATCH`/`AUTOMIX` 타입도 표현은 가능하지만 DSP 렌더링은
여전히 없다(그 구현은 AutoMix Core 단계의 몫). 컴파일된 clip 집합에 없는 clip을
참조하는 transition(비활성화된 clip 등)은 조용히 제외된다 -- 존재하지 않는 clip을
가리키는 transition을 실행 plan에 남기지 않기 위해서다
(`test_timeline_transitions_are_compiled_into_audio_render_plan`,
`test_transition_referencing_a_disabled_clip_is_excluded`).

## 4. Presentation Plan

가장 중요한 개념: **Audio active != Presentation owner**. AutoMix로 두 트랙이 동시에
재생되어도 Canvas(앨범아트, 가사, Now Playing, 배경)는 한 번에 한 트랙만 기준으로
그려져야 한다. `PresentationPlan.track_at(global_seconds)` /
`.local_time(global_seconds)`가 "글로벌 타임라인 초 -> 시각적 소유 트랙 -> 그 트랙
기준 로컬 초"라는 변환을 정의하는 **단 하나의 장소**다. Frame Evaluation Layer
(`resolve_lyrics_cue_state()` 등)는 이 local time을 입력으로 받도록 미래에 연결될 수
있다.

현재 Sequential 컴파일러는 오디오 클립 경계 = presentation 경계이므로 두 개념이
일치한다. AutoMix가 들어와 오디오가 겹치기 시작해도 이 API 모양은 바뀌지 않는다 --
Compiler 내부 구현만 바뀐다.

**Gap lookup.** `PresentationPlan._window_at()`는 window의 `timeline_start` 정렬
목록에 `bisect_right`를 적용해 "이 순간 이전에 시작한 가장 마지막 window"를 고른다.
그 결과 첫 window 이전은 첫 window, window 사이 gap은 **가장 최근에 끝난 window**
(몇 번째 window든), 마지막 window 이후는 마지막 window가 된다 -- Preview의
`ExportPreviewDialog._track_at()`(bisect 기반)이 이미 쓰던 것과 같은 semantics다.
이전 구현은 순차 스캔이 실패하면 `windows[0]`으로 폴백해, 세 번째 이상 window 뒤의
gap에서 첫 트랙을 잘못 돌려주는 버그가 있었다
(`tests/test_render_plan.py::test_presentation_gap_keeps_the_most_recently_ended_owner`).

**Source-time mapping.** `PresentationWindow`는 이제 `playback_rate`도 갖는다.
`local_time()`은 `source_time_at_start + (clamped_global - timeline_start) *
playback_rate`로 계산하며, `clamped_global`은 질의 시각을 window 범위
`[timeline_start, timeline_end]`로 잘라낸 값이다. 이 clamp 덕분에 window 시작 전이나
window가 끝난 뒤(같은 gap이든 마지막 이후든) local time이 그 트랙의 source 시작/끝에
고정되고, 글로벌 시간을 따라 계속 흐르지 않는다
(`test_presentation_local_time_reflects_source_offset`,
`test_presentation_local_time_reflects_playback_rate`). Sequential 컴파일러는
`source_in=0, playback_rate=1`만 만들므로 기존 결과는 완전히 동일하다.

## 5. Metadata Plan

`MetadataPlan.chapters`는 챕터/YouTube 타임스탬프가 최종적으로 공유해야 할 데이터
형태다. 챕터의 `start`는 항상 `PresentationWindow.timeline_start`와 같고, `end`는
**다음 window의 `timeline_start`**(마지막 챕터는 `CompiledRenderPlan.duration_seconds`)
로 gap을 건너뛰어 이어진다 -- FFmpeg가 항상 요구해 온 "챕터가 전체 타임라인을 빈틈없이
덮는다"는 규칙과, `PresentationWindow` 자신의 시작/끝만 쓰던 이전 정의를 하나로
통일한 것이다(gap이 없는 재생목록에서는 두 정의가 우연히 같았다). 이제 FFmpeg 챕터,
YouTube 타임스탬프, Presentation window가 전부 같은 `MetadataPlan`을 소스로 삼는다
(`test_metadata_chapter_bridges_gap_to_next_track_start`,
`test_metadata_chapter_start_matches_presentation_and_youtube_source`).

## 6. Preview 데이터 흐름 (현재)

```
PlaylistTrack[] -> compile_playlist() -> PresentationPlan
                                              |
                              ExportTimelinePlanner.build() -> ExportFrameSample[]
                                              |
                    ExportPreviewDialog._build_track_schedule() -> (index, track, start, end)
```

`ExportTimelinePlanner.build()`는 이제 `resolve_track_windows()`를 직접 부르지 않고
`compile_playlist(tracks).presentation.windows`를 순회한다 (`app/renderer/export_timeline.py`).
`PresentationWindow`는 `track_id`만 갖고 있어 애니메이션 계산에 필요한 `PlaylistTrack`
객체는 `id -> track` 매핑으로 되찾는다. `floor`(이전 트랙의 끝, 트랙 사이 gap 계산에
쓰임)는 `PresentationWindow`에 없는 필드라 루프에서 `cursor`로 직접 누적한다 -- 이
값은 원래 `resolve_track_windows()`가 내부적으로 추적하던 cursor와 정확히 같다.
호출부(`export_controller.py`, `export_plan.py`)는 이미 `track.enabled`로 필터링한
`active_tracks`만 넘기므로 `compile_playlist()`의 disabled-clip 제외 로직은 영향이
없다. `test_export_timeline.py`, `test_export_frame_equivalence.py` 전부 그대로
통과해 프레임 스케줄이 바뀌지 않았음을 증명한다.

`ExportPreviewDialog._build_track_schedule()`(`app/dialogs/export_preview_dialog.py`)도
같은 `compile_playlist(self.tracks).presentation.windows`를 소비하도록 바꿨다. 이
스케줄은 dialog 초기화 시 한 번 계산되어 `self._track_schedule`에 캐시되고,
`_track_at()`을 통해 재생 위치 -> (인덱스, 트랙, 시작, 끝) 조회에 쓰인다. `_skip_track()`은
스케줄을 다시 계산하던 것을 캐시된 `self._track_schedule`을 재사용하도록 정리했다 --
같은 플레이리스트에 대해 Preview 안에서조차 스케줄을 두 번 계산하던 걸 없앤 것.
`PlaylistTimeline`(재생바 눈금 위젯)의 `paintEvent()`도 이제 같은 `self._track_schedule`을
생성자에서 그대로 받아 그린다 -- `resolve_track_windows()`를 직접 부르지 않을 뿐 아니라,
매 `paintEvent`(리페인트마다 호출됨)마다 `compile_playlist()`를 다시 실행하지도 않는다.
스케줄이 dialog 생애주기 동안 한 번만 계산되어 재사용되기 때문이다.

## 7. Export 데이터 흐름 (현재)

```
PlaylistTrack[] -> compile_playlist() -> CompiledRenderPlan
                                              |
                    FFmpegRenderer._visual_sequence()        (Presentation)
                    FFmpegRenderer._insert_silence_for_gaps()(Presentation)
                    FFmpegRenderer._timeline_duration()       (duration_seconds)
                    FFmpegRenderer._write_export_ffmetadata() (Metadata)
```

`FFmpegRenderer`(`app/renderer/ffmpeg_renderer.py`)의 모든 타이밍 지점이
`compile_playlist(tracks)`를 거친다:

- `_visual_sequence()` / `_insert_silence_for_gaps()`는 `PresentationPlan.windows`를
  순회한다. `PresentationWindow`에는 legacy `TrackWindow.floor`(이전 트랙의 끝, gap
  크기 계산용)가 없어서 윈도우들을 한 번 훑어 `floor` 배열을 직접 만든다 -- 이 값은
  원래 `resolve_track_windows()`가 내부적으로 추적하던 cursor와 정확히 같다.
- `_timeline_duration()`은 `compile_playlist(tracks, enabled_only=True).duration_seconds`를
  그대로 반환한다 -- "실행 duration은 컴파일된 clip이 실제로 도달하는 가장 먼 지점"이라는
  4절/5절과 같은 정책(9절 참고)을 export 경로에도 적용한 것이다.
- `_write_export_ffmetadata()`의 챕터 계산은 이제 `_track_windows()`를 직접 재구현하지
  않고 `compile_playlist(tracks, enabled_only=True).metadata.chapters`를 그대로 읽는다
  (`_track_windows()`는 이 용도로만 쓰였고, 이제 삭제됐다). `MetadataPlan.chapters`가
  이미 "챕터 end = 다음 트랙 시작, 마지막은 전체 duration"으로 gap을 건너뛰도록
  통일됐으므로(5절), FFmpeg가 예전부터 요구해 온 "챕터가 타임라인을 빈틈없이 덮는다"는
  규칙(`test_container_tags_and_contiguous_chapters`)과 정확히 같은 결과를 만든다 --
  두 번 정의되던 chapter-end 규칙이 이제 한 곳(`compile_timeline()`)에만 있다.

`FFmpegRenderer`는 호출부(`export_controller.py`)가 이미 `track.enabled`로 걸러낸
`active_tracks`만 넘기므로 `compile_playlist()`의 disabled-clip 제외 로직은 영향이
없다. `test_ffmpeg_streaming_integration.py`, `test_bounded_export_pipeline.py`가
그대로 통과해 실제 렌더 출력(콘테이너 태그, 챕터, silence 삽입, concat 매니페스트)이
바뀌지 않았음을 증명한다.

`ExportOrchestrator.video_clips()`(`app/controllers/export_controller.py`)의
track-linked 비디오 스케줄링도 같은 이유로 `resolve_track_windows()`에서
`compile_playlist(tracks).presentation.windows`로 옮겼다 -- `track_id -> PlaylistTrack`
매핑으로 되찾은 트랙과 그 presentation 시작 시각을 쓴다. `ExportOrchestrator.playlist_duration()`도
`compile_playlist(tracks, enabled_only=True).duration_seconds`로 옮겨 `_timeline_duration()`과
같은 정책을 공유한다.

## 7.5. Metadata 소비처 (PlaylistExportService)

```
PlaylistTrack[] -> compile_playlist(enabled_only=True) -> MetadataPlan
                                                                |
                                        PlaylistExportService.description_text()
```

`PlaylistExportService.description_text()`(`app/services/playlist_export_service.py`)는
이제 `MetadataPlan.chapters`를 직접 읽는다. 이 메서드는 챕터의 `start`와 트랙
객체(artist/title)만 쓰고 `end`는 쓰지 않으므로, 7절에서 FFmpeg 챕터를 그대로 두게
만든 "gap이 있으면 end 정의가 갈린다"는 문제가 여기엔 없다 -- `MetadataChapter.start`는
gap 유무와 무관하게 `PresentationWindow.timeline_start`와 항상 같다. `track_id ->
PlaylistTrack` 매핑으로 artist/title을 복원한다. 호출부(`export()`)가 이미
`track.enabled`로 필터링한 리스트를 넘기지만, `description_text()` 자체의 계약
("enabled track order")을 스스로 보장하도록 `compile_playlist(..., enabled_only=True)`를
명시적으로 사용한다.

## 8. Legacy Compatibility

- `resolve_track_windows()` / `playlist_duration()`은 그대로 유지되며 오직
  `timeline_from_playlist()`(Legacy playlist -> Timeline adapter)와
  `PlaylistService`의 legacy 편집기 스케줄링, 그리고 `track_schedule.py` 자신의
  테스트에서만 쓰인다 -- Preview/Export의 실제 실행 경로(timeline marker, video
  scheduling, export duration, FFmpeg chapter, YouTube timestamp)는 전부
  `CompiledRenderPlan`을 거친다.
- `timeline_from_playlist()`도 그대로 유지되며 `compile_playlist()`가 그 위에 얹힌
  convenience 함수다.
- Project 저장 포맷은 변경하지 않았다. `CompiledRenderPlan`은 저장되지 않는다:
  `Project JSON -> Playlist -> Timeline -> CompiledRenderPlan` 순으로 매번 재계산된다.

## 9. Duration 정책

`CompiledRenderPlan.duration_seconds`는 **컴파일된 실행 상태가 실제로 도달하는 가장
먼 지점**이다: `max(clip.timeline_end for clip in compiled_clips, default=0.0)`.
이전에는 `Timeline.duration`(비활성 clip을 포함해 모든 lane의 최댓값)을 그대로
썼는데, 비활성 clip이 활성 clip들보다 뒤에 있으면 실행되지도 않을 구간만큼 duration이
부풀려졌다. 이제 비활성 clip은 duration 계산에서도 완전히 빠진다
(`test_disabled_clip_does_not_inflate_compiled_duration`). Sequential 재생목록처럼
gap이 없거나 마지막 clip이 항상 활성 상태인 경우 두 정의는 동일하므로 기존 동작은
바뀌지 않는다.

## 10. 미래 AutoMix 삽입 지점

```
Playlist
   |
Timeline
   |
+------------------------+
| Future Planner         |
| SequentialPlanner (now)|
| AutoMixPlanner (later) |
| ManualTimelinePlanner  |
+-----------+------------+
            |
CompiledRenderPlan
            |
Preview / Export
```

`compile_timeline(timeline, options)`의 `options: TimelineCompileOptions`가 미래
Planner 선택/파라미터(예: `automix_enabled`, `transition_preferences`)를 위한 확장
지점이다. 지금은 비어 있다 -- 실제 필드는 이번 작업 범위 밖.

## 11. 다음 단계 (이번 작업 범위 밖)

`ExportTimelinePlanner`, `ExportPreviewDialog`(`PlaylistTimeline` 포함),
`FFmpegRenderer`, `ExportOrchestrator` 전부 PresentationPlan/AudioRenderPlan/
MetadataPlan을 거치도록 전환 완료 (4·5·6·7절 참고). 남은 항목:

- Audio Pipeline 분리(`AudioRenderPlan -> AudioPipeline -> PreparedAudio`)는 시작하지
  않았음: `FFmpegRenderer.render()`의 normalize/concat 로직 자체를 건드리는 리스크가
  이번 스코프의 실익보다 크다고 판단. gap 계산(`_insert_silence_for_gaps`)과 클립
  배치(`_visual_sequence`)는 이미 RenderPlan을 거치므로, 다음 단계는 normalize/concat
  실행 로직 자체를 `AudioPipeline.prepare()`로 감싸는 것.
- `AudioRenderTransition`은 정보를 보존할 뿐 DSP가 없다 -- `CROSSFADE`/`EQUAL_POWER`/
  `BEAT_MATCH`/`AUTOMIX`를 실제 오디오 믹스로 렌더링하는 것은 AutoMix Core의 몫.
- Visualizer의 mixed-audio 소스 정책 (TODO, 섹션 24 참고): 코드에 아직 표시 안 함 --
  실제 visualizer 코드를 건드리지 않았으므로 여기 문서에만 남긴다.
- AutoMix Core: BPM/beat/key 분석, transition scoring, `AutoMixPlanner` 자체.
