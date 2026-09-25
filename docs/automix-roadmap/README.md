# Playlist Canvas AutoMix — Next Roadmap Prompt Pack

## 권장 진행 순서

1. `01_progressive_automix_preview.md`
2. `02_real_music_listening_and_tuning.md`
3. `03_automix_debug_explainability_ui.md`
4. `04_continuous_filter_sweep.md`
5. `05_vocal_aware_dsp.md`
6. `06_optional_stem_separation.md` — 실제 필요성이 확인될 때만
7. `07_user_automix_presets.md`
8. `08_cache_background_ux_polish.md`
9. `09_release_hardening_known_issues.md`

## 중요한 분기
Phase 4의 실제 음악 청감 결과를 보고 이후 DSP 단계 우선순위를 바꿔도 된다. Continuous filter sweep, vocal-aware DSP, stem separation은 실제 필요성이 낮으면 생략 가능하다. Stem separation은 필수 완료 조건이 아니다.

## 추천 milestone
- A: Progressive Preview
- B: 실제 음질 검증
- C: Debug + DSP polish
- D: Advanced/Productization
- E: Release hardening

각 MD를 코딩 에이전트에게 한 단계씩 전달하고, 각 단계 종료 후 commit SHA 확인 → 실제 diff 검토 → regression 확인 → 다음 단계 진행 여부 결정 순서로 운영한다.


## Status (2026-09-23)

| step | result | commit |
|---|---|---|
| 01 progressive preview | done earlier; re-verified, churn numbers recorded | `789b73a`, `98cc274` |
| 02 real music tuning | diagnostics framework + 2 real-music fixes; human listening still TODO | `fad3a72` |
| 03 debug / explainability UI | done | `a7284da` |
| 04 continuous filter sweep | implemented and A/B-able; selector keeps FILTER_BLEND until listening prefers it | `f98ff95` (+ `c8ab25f` long-graph fix) |
| 05 vocal-aware DSP | done; voice-band heuristic removed (inverted on real music) | `b4ac154` |
| 06 stem separation | deferred with cost, criteria and proposal | `aee8b5e` |
| 07 presets | done | `449c168` |
| 08 cache / background UX | done | `e9d45f2` |
| 09 release hardening | done; full suite green | `4424d93` |

Each step's file ends with an **Outcome** section. The completed phase 1-8 AutoMix documents live in `docs/automix-history/`.

2026-09-26: the default analyzer became Beat This! + Open-Unmix on ONNX Runtime, and transitions over a decaying tail drop the next track in -- measurements in `2026-09-26-onnx-analyzer-vocals-drop-in.md`.