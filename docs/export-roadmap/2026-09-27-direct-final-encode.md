# 작업 프롬프트: 내보내기 중간 영상 파일 없애기 (캡처 → 최종 인코더 직접 전송)

> 이 문서는 다른 에이전트에게 그대로 전달하는 작업 지시서입니다.
> 사용자에게는 한국어로 보고하세요. 코드·식별자·커밋 메시지는 기존 관례(영어)를 따릅니다.

## 목표

영상 내보내기에서 Canvas 레이어를 **중간 파일(`canvas-*.mkv`)로 저장하지 않고**,
캡처하는 즉시 최종 FFmpeg 인코더로 흘려보내 임시 저장 공간을 0에 가깝게 만든다.
결과 영상의 화질·타이밍·오디오·메타데이터는 지금과 동일해야 한다.

## 배경 (2026-09-27 측정)

- 곡 5개(약 15분) 720p30 내보내기에서 실제 임시 공간이 20~50 GB까지 사용됐다.
- 원인은 움직이는 Canvas 레이어의 중간 영상이다. 화면 전체에 질감 있는 내용이
  매 프레임 바뀌면 무손실 중간 파일이 원본 RGB 크기의 40~90%를 차지한다.
- 1차 완화로 색상 스트림을 `crf 0` → `crf 12`(시각적 무손실, PSNR 46~56 dB)로 바꿔
  40~70%를 줄였다 (`app/renderer/static_video_stream.py`의 `INTERMEDIATE_CRF`).
  그래도 움직이는 사진 배경은 15분에 약 26 GB가 필요하다. 이 작업이 근본 해결이다.
- 참고 수치 (15분, 720p30, `crf 12 ultrafast`): 움직이는 사진 26 GB, 깨끗한 그래픽
  1.9 GB, 텍스트 레이어 0.01 GB, 파이썬 시각화(qtrle) 레이어당 약 0.4 GB.

## 현재 구조

1. `app/controllers/export_controller.py` `ExportOrchestrator`
   - `prepare_transition_audio()` — AutoMix/크로스페이드 오디오를 먼저 준비
     (`prepared_audio_path`).
   - `build_export_plan()` (`app/preview/export_plan.py`) — Z 밴드 분할,
     `use_streamed_visuals` / `direct_final_stream` 결정, 스트림별 샘플 일정.
   - `prepare_staging_space()` — 디스크 공간 사전 점검(추정식은
     `app/services/export_storage_service.py`).
   - `ExportSession.run()` (`app/preview/export_session.py`) — **Qt 메인 스레드**에서
     Canvas를 캡처하고 스트림마다 `StaticVideoStreamEncoder`
     (`app/renderer/static_video_stream.py`)로 중간 mkv를 만든다.
     - 불투명 base: `libx264rgb` (또는 `direct_final_stream`이면 최종 코덱으로 바로)
     - 투명 레이어: 색상 `libx264rgb` + 알파 `ffv1 gray` 2개 스트림
     - 변하지 않는 스트림은 PNG 1장 + 길이(“sparse invariant”)로 처리
   - 캡처가 끝나면 `RenderWorker`(`app/renderer/render_worker.py`, QThread)를 시작한다.
2. `FFmpegRenderer.render()` (`app/renderer/ffmpeg_renderer.py`)
   - 오디오 준비(필요 시), 파이썬 시각화 레이어 렌더(`app/renderer/python_visualizer.py`,
     qtrle `.mov`), 그다음 단일 최종 FFmpeg 호출로 base + 레이어
     (`StaticOverlayLayer` / `PreparedStaticOverlayLayer`) + 시각화 + 오디오를 합성한다.
   - 필터 그래프: `app/renderer/ffmpeg/filter_graph.py`.

즉 지금은 **캡처 단계 → (디스크) → 최종 인코드 단계**가 순차 실행된다.

## 원하는 구조

- 최종 FFmpeg 프로세스를 먼저 띄우고, Canvas 캡처 프레임을 파이프로 곧바로 넣는다.
- 입력이 여러 개(base, 레이어 N개)이므로 stdin 하나로는 부족하다. Windows 명명
  파이프(`\\.\pipe\...`) 또는 여러 레이어를 한 장으로 합쳐 하나의 스트림으로 보내는
  방식 중 무엇이 안전한지 먼저 조사하고 선택 근거를 보고하라.
  - 참고: 레이어를 나누는 이유는 파이썬 시각화·비디오 클립이 Z 순서상 레이어
    사이에 끼기 때문이다. 이런 요소가 없으면 이미 `direct_final_stream`으로
    중간 파일 없이 처리된다. 그 경로를 확장하는 방안도 검토하라.
- 오디오와 파이썬 시각화 레이어는 최종 인코드 전에 준비돼야 하므로,
  캡처 시작 전에 끝내거나(순서 변경) 파일로 유지한다(작아서 괜찮음).
- 캡처는 Qt 메인 스레드에서만 가능하다. FFmpeg 쓰기는 기존처럼 제한된 큐
  (`BoundedExportPipeline`)를 거친 백그라운드 스레드에서 하고, 파이프가 막혀도
  UI가 멈추지 않게 `QApplication.processEvents` 펌프를 유지하라.
- 스트림 간 캡처는 이미 타임라인 순서(heap)로 번갈아 진행되므로(`_run_streamed`),
  여러 입력을 동시에 채우는 데 활용할 수 있다. 입력 간 교착(한 파이프가 가득 차서
  FFmpeg가 다른 입력을 기다리는 상황)을 반드시 분석하고 막아라.

## 반드시 지킬 것

- 결과 동일성: 길이(`playlist_duration`), FPS, Z 순서, 알파 합성, 가사/전환 타이밍,
  메타데이터, 챕터, -14 LUFS 오디오 정책(`app/renderer/loudness.py`)이 그대로여야 한다.
- 취소: 준비·인코드 어느 시점에서 취소해도 FFmpeg와 스레드가 멈추고 부분 출력 파일이
  남지 않아야 한다(현재 `cancel_active_session`, `RenderWorker` 취소 경로 참고).
- 진행률: 지금의 "화면 준비 25% + 인코드 75%" 표시가 하나의 흐름으로 합쳐지므로
  진행 창(`app/dialogs/export_progress_dialog.py`)의 단계와 퍼센트를 자연스럽게 재구성하라.
  모든 UI 문자열은 한국어/영어 둘 다 제공한다.
- 대체 경로: 새 방식이 불가능한 환경(인코더 미지원, 파이프 실패 등)에서는 현재
  중간 파일 방식으로 자동 전환하라. 기존 PNG 대체 경로도 유지한다.
- 저장 공간 추정(`estimate_export_storage`, `prepare_staging_space`)을 새 구조에 맞게 고쳐,
  불필요한 "저장 공간 부족" 경고가 뜨지 않게 하라.
- 하드웨어 인코더(h264_nvenc 등) 사용 시 최종 인코드 속도가 캡처 속도에 묶이게 되므로
  전체 내보내기 시간이 지금보다 크게 늘지 않는지 측정해서 보고하라.

## 검증

- 전체 테스트: `.venv\Scripts\python.exe -m unittest discover -s tests -t .`
  (`QT_QPA_PLATFORM=offscreen`). pytest는 설치돼 있지 않다.
- 실제 FFmpeg로 짧은 프로젝트(정적 배경 + 텍스트 레이어 + 움직이는 레이어 + 시각화)를
  기존 방식과 새 방식으로 각각 내보내 프레임 단위로 비교(PSNR 또는 픽셀 비교)하고,
  최대 임시 디스크 사용량과 총 소요 시간을 표로 보고하라.
- 내보내기 도중 취소, 디스크 부족, FFmpeg 비정상 종료 시나리오를 테스트로 남겨라.
- 코드를 바꾼 뒤 `graphify update .`를 실행하라.

## 참고

- 사용자는 메인 체크아웃에서 앱을 실행한다. 워크트리에서 작업했다면 main에 합쳐야
  사용자가 변경을 확인할 수 있다.
- 로그: `%LOCALAPPDATA%\PlaylistCanvas\logs\playlist-canvas.log`
  (`Canvas capture bands`, `Canvas independent timelines`, `Final FFmpeg stage` 줄이 유용).

## 결과 (2026-09-27 구현)

- 전송 방식: Canvas 스트림(불투명 base, 투명 Z 밴드)마다 명명 파이프 하나
  (`app/renderer/canvas_pipe.py`). Windows는 `\\.\pipe\playlist-canvas-...`
  (단일 인스턴스, 원격 거부, overlapped I/O로 취소 가능), 그 외는 FIFO.
  - 레이어를 한 장으로 합치는 방식은 쓰지 않았다. 시각화·비디오 클립이 Z 밴드 사이에
    끼므로 합치려면 FFmpeg의 디코드·필터를 파이썬으로 다시 만들어야 한다.
    stdin은 입력 하나뿐이고, loopback TCP는 방화벽 경고·다른 프로세스 접속 위험이 있다.
  - base는 `bgr0`, 투명 레이어는 straight `rgba` 원시 프레임으로 보내고 최종 그래프는
    기존 overlay 그대로 쓴다. 중간 CRF 12 손실이 없어져 결과가 오히려 원본에 더 가깝다.
- 교착 방지: 스트림마다 전용 쓰기 스레드 + 작은 큐. 캡처는 타임라인 순서(heap)라
  한 큐에서 막혀도 다른 스트림은 그 시점까지 데이터가 들어가 있다. FFmpeg가 입력을
  순서대로 열 때 막히지 않도록 `-probesize 32 -analyzeduration 0`, 각 파이프의 첫
  프레임을 FFmpeg 시작 전에 캡처한다. 그래도 멈추면 120초 무진행 감시가 오류를 낸다.
- 순서: 각 파이프 첫 프레임 캡처 → RenderWorker 시작(오디오·시각화 준비 후 FFmpeg
  실행) → 나머지 캡처가 FFmpeg와 동시에 진행. 변하지 않는 스트림은 PNG 한 장.
  워커 결과는 `RenderOutcomeGate`가 캡처가 끝날 때까지 보관한다.
- 대체 경로: 파이프 오류·멈춤, 또는 FFmpeg가 파이프를 읽기 시작한 뒤 실패하면(디스크
  부족 제외) 처음부터 기존 중간 파일 방식으로 다시 캡처한다. FFmpeg가 파이프를 열기
  전에 실패한 경우(오디오 등)는 같은 오류가 반복되므로 그대로 보고한다.
  `PLAYLIST_CANVAS_DISABLE_PIPED_EXPORT=1`로 새 방식을 끌 수 있다.
- 진행률: "준비"(오디오·시각화, 4~12%) 뒤 "화면 캡처와 영상 만들기" 한 단계로
  FFmpeg 출력 시간을 따라간다.
- 저장 공간 추정: 파이프 스트림은 정지 PNG 몇 장만 계산한다.

### 실측 (Linux 컨테이너, CPU 4코어, FFmpeg 6.1, libx264 medium, 1080p30)

`python tools/export_pipe_benchmark.py --seconds 60` — 움직이는 앨범아트 배경 +
진행 바 + 파이썬 시각화 + 페이드 텍스트, 실제 내보내기 컨트롤러로 두 방식을 각각 실행.

| 항목 | 중간 파일 방식 | 새 방식 |
|---|---|---|
| 총 소요 시간 (60초 영상) | 268.1초 | 84.3초 |
| 최대 임시 디스크 (출력 파일 포함) | 305.6 MB | 49.6 MB |
| 프레임 수 / 길이 | 1800 / 60.0초 | 1800 / 60.0초 |
| 두 결과 PSNR | — | 평균 57.7 dB (최소 53.9) |

새 방식의 남은 임시 공간은 시각화 `.mov`(qtrle)와 쓰는 중인 출력 mp4다.
GPU가 없는 환경이라 h264_nvenc는 측정하지 못했다. Windows에서
`python tools/export_pipe_benchmark.py --encoder h264_nvenc --ffmpeg <ffmpeg.exe>`로
같은 표를 얻을 수 있다. 원리상 전체 시간은 "캡처 + 최종 인코드"에서
"둘 중 느린 쪽"으로 바뀌므로, NVENC처럼 인코드가 빠르면 캡처 속도가 상한이 된다.
