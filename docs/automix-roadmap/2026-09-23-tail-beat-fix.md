# Audible tail and beat matching regression fix

The planner could choose an early structure outro hint and discard audio
after its fixed bar window. A 200.3-second source with an outro hint at 150
seconds could stop at 166 seconds, including when known vocals continued
to the end. Candidates now preserve the audible ending and finalize their
duration before scoring. Snapping may add up to one bar, within the preset's
maximum duration; an earlier hint cannot force a long overlap or a cut.
C.1 still requires scored and rendered geometry to match exactly.

Reliable BPM and at least four actual beat timestamps now allow tempo
matching even with uncertain bar phase. Such transitions use beat cues;
measured, reliable downbeats still take precedence. Missing or weak rhythm
data and incompatible tempos retain conservative fallbacks. A fallback after
a rate-matched track now converts its outgoing source span using the retained
playback rate.

When either vocal map is unknown, key/energy/drift alone no longer select a
style that mutes mids before the overlap ends. Rate-matched pairs use bass
swap with full-window mids; other pairs use the legacy full-window fade.
Known vocal maps retain the existing handoff policy. The selector reports
this reason explicitly.

Regression checks live in `tests/test_automix_tail_protection.py`. The real
FFmpeg check renders 120/124 BPM pulses in separate channels and compares
their timing throughout the overlap. Set `PLAYLIST_CANVAS_TEST_FFMPEG` to
the FFmpeg executable to enable it. The original main fails the new cases.

This does not add singing detection, stem separation, phrase alignment, or
variable-tempo warping. Full-window fades still attenuate ending vocals.
Production analysis currently has no vocal activity provider, so unknown
timing is handled conservatively. Human listening on the reported song pair
is still required; no claim is made that every musical transition is solved.
