# 비트 매칭 · 템포 램프 · 보컬 회피 개선과 AutoMix 조사 (2026-09-23)

## 1. 비트 매칭이 안 맞던 원인

Beat This!는 비트를 50 fps(20 ms) 격자로 보고한다. 기존 BPM은 비트 간격의 **중앙값**이라
이 양자화를 그대로 물려받았다.

| 실제 BPM | 중앙값 기반 BPM | 오차 |
|---|---|---|
| 124 | 125.00 | +0.81 % |
| 126 | 125.00 | −0.79 % |
| 128 | 130.43 | +1.90 % |
| 140 | 142.86 | +2.04 % |

두 곡의 오차가 반대 방향이면 속도 비율이 최대 약 4 % 어긋나고, 16마디 겹침이면 끝에서
반 박자 이상 벌어진다. librosa 경로도 hop 단위로 양자화된다.

**수정**: `app/automix/beatgrid.py`가 비트 타임스탬프에 직선(`time = origin + n·period`)을
최소제곱으로 맞춘다(놓친/중복 비트에 강건). 같은 데이터에서 128.000 BPM이 나온다.
전환에 쓰는 속도 비율은 전역 BPM이 아니라 **각 곡의 믹스 구간 60초에서 맞춘 로컬 격자**로
계산하고, 큐도 격자 위의 정확한 위치로 보정한다.

## 2. BPM을 낮춘 곡이 원래대로 돌아오는가 → 기존에는 돌아오지 않았음

기존 정책은 들어오는 곡 **전체**를 나가는 곡 템포로 재생했고(`AudioRenderClip`의 단일
`playback_rate`), 그 속도가 다음 전환의 기준으로 전파됐다. 120 → 124 → 128 BPM 재생목록이면
두 번째 곡은 끝까지 120, 세 번째 곡도 120에 맞춰져 재생목록 전체가 첫 곡 템포에 묶였다.

## 3. 새 템포 방식 (요청한 방식)

- 모든 곡은 **자기 템포로** 시작해 재생된다(`playback_rate` 1.0).
- BEAT_MATCH일 때 **나가는 곡**이 겹침 직전 최대 8마디(`RAMP_BARS`) 동안 들어오는 곡 템포로
  점진적으로 바뀌고(`TempoRamp`, 32단계), 겹침 동안 그 속도를 유지한다. 들어오는 곡은
  다운비트 큐에서 자기 템포로 시작한다.
- 렌더링: FFmpeg `rubberband` + 시간 지정 `tempo` 명령. 실측 결과 `atempo`는 `tempo` 명령마다
  입력을 약 21 ms 버려 32단계 램프에서 690 ms 앞당겨졌고, `rubberband`는 계획 대비 3 ms 이내였다.
  `rubberband`가 없는 FFmpeg는 단계별 `atempo` 구간을 샘플 단위로 고정해 이어 붙인다(15–18 ms 오차).
- 검증: 합성 펄스(120 → 124 BPM) 렌더에서 두 곡 펄스 차이 20 ms 이내(기존 테스트 허용치 45 ms).
  실제 음악(REDRED 반주 121 BPM → 2.5 % 빠르게 늘인 사본)에서 비율 1.0249를 정확히 찾고, 겹침 구간
  Beat This 비트 차이 중앙값 0 ms · 최대 20 ms(측정 해상도 한계).

## 4. 앞뒤 무음

첫 곡은 첫 소리부터, 마지막 곡은 마지막 소리까지 재생한다. CUT이거나 전환이 없는 이음새도
나가는 곡의 소리 끝과 들어오는 곡의 소리 시작을 바로 붙인다(분석이 있는 곡만).

## 5. 보컬 구간에서는 믹싱하지 않음

- 규칙(보컬끼리만 금지): 나가는 곡이 노래하는 동안에는 절대 섞지 않는다. 후보 큐가 마지막
  보컬 안에 있으면 **보컬이 끝난 직후의 첫 다운비트**(없으면 비트, 감지 해상도 100 ms 허용)로
  옮기고, 남은 반주가 짧아도 1초 이상이면 블렌드한다(`MIN_AFTER_VOCAL_SECONDS`). 들어오는 곡의
  보컬은 나가는 곡이 이미 노래를 멈췄으므로 겹침 안에서 허용한다. 끝까지 노래하는 곡만 컷.
- 감지: Demucs `htdemucs`(Meta, MIT, 모델 약 84 MB)로 곡의 앞/뒤 45초만 보컬을 분리하고,
  100 ms 프레임에서 보컬 스템이 믹스 대비 −20 dB 이상이고 −45 dBFS 이상이면 노래로 본다
  (0.8 s 이하 숨 쉬는 틈은 이어 붙임). 14스레드 CPU에서 약 0.5배속 → 곡당 약 40초, 분석 캐시에 저장.
- 교차 검증: Attention은 BS-Roformer 분리 결과와 프레임 단위 정밀도·재현율 1.00.
  REDRED는 MR 제거 결과가 멜밴드 **karaoke**(리드 보컬만) 모델과 일치했고, 전체 보컬 모델
  (melband-kim, BS-Roformer-gabox)은 htdemucs와 초 단위로 1–2 dB 안에서 일치했다(백보컬·
  아웃트로 보컬 포함).
- 영향(실제 팝 4곡): 두 곡 모두 엄격 금지일 때는 3개 이음새가 전부 컷이었다. 현재 규칙에서는
  Give Me a Reason → Birthday가 마지막 보컬(198.5 s) 직후 다운비트에서 1.5초 비트매칭 블렌드
  (80 → 76 BPM, 8마디 램프)가 되고, Birthday와 Daze Inn은 마지막 소리 0.1초 전까지 노래해 컷이다.
- Demucs가 없으면(개발용 `.venv`) 보컬은 "알 수 없음"으로 남고 기존처럼 동작한다. 감지가
  실패한 결과는 캐시 적중으로 쓰지 않아 다음 분석 때 다시 시도한다.

## 6. 장르별 믹싱 참고

| 장르 | 관행 | 현재 대응 |
|---|---|---|
| House/Techno/Trance | 16–32마디 프레이즈 경계에서 긴 EQ 블렌드, DJ용 인트로/아웃트로 | `smooth`/`dj` 프리셋(16마디), 베이스 스왑 |
| Drum & Bass (160–180) | 8–16마디의 결단력 있는 전환, 드롭 맞추기(double drop) | `energetic`(4마디) 근접, 드롭 정렬은 없음 |
| Hip-hop / 오픈 포맷 | 짧은 컷, 다운비트("on the one")에서 투입, 가사 훅을 넘김점으로 | 보컬 회피 + 컷 |
| 장르가 바뀔 때 | 억지 블렌드 대신 에코 아웃/컷 | 템포 한계 초과 시 짧은 크로스페이드 |

실제 DJ 믹스 1,557개(20,765 전환) 분석(Kim et al., ISMIR 2020): 템포 조정의 86.1 %가 5 % 미만,
94.5 %가 10 % 미만. 키 전조는 2.5 %뿐이고 그중 94.3 %가 반음. 전환 길이는 32박 프레이즈마다 봉우리.
→ 템포 한계(`max_tempo_change_percent`)를 5–8 % 수준으로 두고, 전환 길이를 프레이즈(8/16마디)에
맞추는 현재 방향이 관행과 맞다. 장르 자동 분류는 Sonara에 학습된 장르 모델이 없어(사용자 학습용
틀만 제공) 지금은 프리셋 선택으로 대신하는 것이 현실적이다.

## 7. AutoMix AI 모델 / 오픈소스 검토

| 후보 | 라이선스 | 무엇을 하나 | 적용 판단 |
|---|---|---|---|
| DJtransGAN (ICASSP 2022) | MIT, 사전학습 모델 있음 | 두 곡과 **큐 지점을 주면** 전환 구간의 EQ/페이더 곡선을 GAN으로 생성 | EDM 전용, 큐 선택·비트매칭은 하지 않음. 현재 DSP(베이스 스왑 등) 대체 후보일 뿐 계획기는 대체 불가 |
| lenvdv/auto-dj | 저장소 COPYRIGHT 파일(조건 미확인) | 드럼앤베이스 전용 자동 DJ, 구조 경계 매칭, 보컬 겹침 회피 | 장르 전용, Essentia 기반. 아이디어(보컬 회피·구조 매칭)는 이미 반영 |
| mir-aidj/all-in-one | MIT | 템포·비트·다운비트·구간 라벨(인트로/벌스/코러스/아웃트로) 한 번에 | Beat This + Sonara를 하나로 대체할 후보. 무겁고 설치 부담 큼 → 별도 평가 필요 |
| Demucs htdemucs | MIT | 스템 분리 | **이번에 보컬 감지로 채택** |
| Rubber Band (FFmpeg `rubberband`) | GPL(FFmpeg 빌드에 포함) | 고품질 시간 늘이기, 실시간 템포 변경 | **이번에 템포 램프로 채택** |
| Mixxx AutoDJ | GPL | 단순 크로스페이드 자동 DJ | 참고 수준 |
| AI-DJ-Mixing-System, AI-DJ-Software | 다양 | LLM/ONNX 기반 앱 | 재사용 가능한 믹싱 엔진 아님 |

결론: 믹싱 **전체**를 맡길 수 있는 검증된 오픈소스 AutoMix 엔진은 찾지 못했다(상용 Apple Music
AutoMix/Spotify는 비공개). 지금 구조(계획기 + 렌더러)는 유지하고, 오픈소스는 부품으로 쓰는 것이
맞다: 분석(Beat This, Demucs, 후보 all-in-one), 시간 늘이기(Rubber Band), 전환 곡선(DJtransGAN은
EDM 한정 실험 후보).

## 출처

- DJtransGAN: https://github.com/ChenPaulYu/DJtransGAN
- Auto-DJ(드럼앤베이스): https://lenvdv.github.io/2018-03-20-autodj/ , https://github.com/lenvdv/auto-dj
- All-In-One Music Structure Analyzer: https://github.com/mir-aidj/all-in-one
- DJ 믹스 분석(ISMIR 2020): https://github.com/mir-aidj/djmix-analysis/blob/master/index.md , https://arxiv.org/pdf/2008.10267
- 장르별 믹싱: https://blog.pioneerdj.com/djtips/we-uncover-the-mixing-techniques-behind-every-major-genre/ ,
  https://dj.studio/blog/dj-mixing-genres , https://www.djkit.com/blogs/news/genre-dj-tips
- 자동 DJ 저장소 목록: https://github.com/topics/automatic-dj
