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
(`AudioRenderClip`)와 향후 전환 구간(`AudioRenderTransition`)을 담는다. 지금은
`TransitionType.CUT` 외 타입도 표현은 가능하지만 DSP는 없다 -- Compiler는 CUT 스케줄만
만든다.

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

## 5. Metadata Plan

`MetadataPlan.chapters`는 챕터/YouTube 타임스탬프가 최종적으로 공유해야 할 데이터
형태다. Sequential 컴파일러에서는 `MetadataChapter`가 `PresentationWindow`와 1:1로
대응해 기존 `resolve_track_windows()` 결과와 정확히 같은 시작/끝을 만든다
(`tests/test_render_plan.py::test_metadata_chapters_match_legacy_windows`).

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
`PlaylistTimeline`(재생바 눈금 위젯)의 `paintEvent()`는 `resolve_track_windows()`를
그대로 쓴다 -- `ExportPreviewDialog` 클래스 밖의 별도 `QSlider` 위젯이라 이번 스코프에
포함하지 않았다.

## 7. Export 데이터 흐름 (현재)

```
PlaylistTrack[] -> compile_playlist() -> CompiledRenderPlan
                                              |
                    FFmpegRenderer._visual_sequence()        (Presentation)
                    FFmpegRenderer._insert_silence_for_gaps()(Presentation)
                    FFmpegRenderer._track_windows()           (Audio)
                    FFmpegRenderer._write_export_ffmetadata() (via _track_windows)
```

`FFmpegRenderer`(`app/renderer/ffmpeg_renderer.py`)의 네 지점을 모두
`compile_playlist(tracks)`로 옮겼다:

- `_visual_sequence()` / `_insert_silence_for_gaps()`는 `PresentationPlan.windows`를
  순회한다. `PresentationWindow`에는 legacy `TrackWindow.floor`(이전 트랙의 끝, gap
  크기 계산용)가 없어서 윈도우들을 한 번 훑어 `floor` 배열을 직접 만든다 -- 이 값은
  원래 `resolve_track_windows()`가 내부적으로 추적하던 cursor와 정확히 같다.
- `_track_windows()`는 `AudioRenderPlan.clips`에서 `(timeline_start, duration)`을
  뽑는다. `AudioRenderClip.duration`은 `(source_out - source_in) / playback_rate`인데
  Sequential 컴파일에서는 `source_in=0, source_out=track.duration_seconds,
  playback_rate=1`이라 `track.duration_seconds`와 정확히 같다.
- `_write_export_ffmetadata()`의 챕터 계산은 `_track_windows()`를 그대로 재사용한다
  (변경 없음). 챕터 end는 **다음 트랙의 시작**(마지막은 전체 duration)으로 계산해야
  한다는 기존 규칙 -- `test_container_tags_and_contiguous_chapters`가 "chapters are
  contiguous and cover the whole timeline"을 강제한다 -- 을 그대로 지키기 위해
  `MetadataPlan.chapters`(각 챕터가 트랙 자신의 끝에서 끝나 트랙 사이 gap을 비워둠)로
  바로 바꾸지 않았다. Sequential 타임라인엔 gap이 없는 한 두 정의가 동일하지만, gap이
  있으면 다르다 -- 이 차이는 6절에서 짚은 "Audio active != Presentation owner"와 같은
  종류의 구분이다.

`FFmpegRenderer`는 호출부(`export_controller.py`)가 이미 `track.enabled`로 걸러낸
`active_tracks`만 넘기므로 `compile_playlist()`의 disabled-clip 제외 로직은 영향이
없다. `test_ffmpeg_streaming_integration.py`, `test_bounded_export_pipeline.py`가
그대로 통과해 실제 렌더 출력(콘테이너 태그, 챕터, silence 삽입, concat 매니페스트)이
바뀌지 않았음을 증명한다.

## 8. Legacy Compatibility

- `resolve_track_windows()` / `playlist_duration()`은 그대로 유지되며 Sequential
  Compiler가 내부적으로 사용하는 compatibility layer다 (`track_schedule.py`의 기존
  docstring대로).
- `timeline_from_playlist()`도 그대로 유지되며 `compile_playlist()`가 그 위에 얹힌
  convenience 함수다.
- Project 저장 포맷은 변경하지 않았다. `CompiledRenderPlan`은 저장되지 않는다:
  `Project JSON -> Playlist -> Timeline -> CompiledRenderPlan` 순으로 매번 재계산된다.

## 9. 미래 AutoMix 삽입 지점

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

## 10. 다음 단계 (이번 작업 범위 밖)

`ExportTimelinePlanner`, `ExportPreviewDialog`, `FFmpegRenderer` 전부 PresentationPlan/
AudioRenderPlan을 거치도록 전환 완료 (6·7절 참고). 남은 항목:

- `PlaylistTimeline`(재생바 눈금 위젯)의 `paintEvent()`도 원한다면 같은 방식으로
  전환 가능 -- `ExportPreviewDialog` 밖의 별도 위젯이라 이번에는 제외.
- `PlaylistExportService`가 `MetadataPlan.chapters`를 직접 소비하도록 전환 (현재는
  track_id -> PlaylistTrack 매핑이 없고, `MetadataChapter.end`가 트랙 자신의 끝이라
  gap이 있는 타임라인에서 FFmpeg 챕터의 "다음 트랙 시작까지" 규칙과 다름 -- 7절
  참고. `MetadataPlan`을 gap-aware하게 만들 것인지 먼저 결정해야 함).
- Audio Pipeline 분리(`AudioRenderPlan -> AudioPipeline -> PreparedAudio`)는 시작하지
  않았음: `FFmpegRenderer.render()`의 normalize/concat 로직 자체를 건드리는 리스크가
  이번 스코프의 실익보다 크다고 판단. gap 계산(`_insert_silence_for_gaps`)과 클립
  배치(`_track_windows`, `_visual_sequence`)는 이미 RenderPlan을 거치므로, 다음
  단계는 normalize/concat 실행 로직 자체를 `AudioPipeline.prepare()`로 감싸는 것.
- Visualizer의 mixed-audio 소스 정책 (TODO, 섹션 24 참고): 코드에 아직 표시 안 함 --
  실제 visualizer 코드를 건드리지 않았으므로 여기 문서에만 남긴다.
- AutoMix Core: BPM/beat/key 분석, transition scoring, `AutoMixPlanner` 자체.
