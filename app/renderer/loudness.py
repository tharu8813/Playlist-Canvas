"""The playlist audio loudness policy, shared by Export and Preview (no Qt, no FFmpeg).

One linear gain brings the whole mix to TARGET_LUFS integrated -- the level
YouTube and Spotify play music at, so an upload is neither turned down nor
left quieter than the rest of a listener's queue. Tracks keep their relative
mastering levels: nothing rides the gain per song.

Peaks above TRUE_PEAK_DBTP after that gain are limited at 4x oversampling,
where inter-sample peaks show (a sample-rate limiter left them untouched:
equal-power blends of two loud masters peak between samples). loudnorm's
dynamic mode, the old fallback for this case, rode the gain per song
(-3.7 to -6.6 dB across one 12-song mix) and still ended at +0.4 dBTP.
The gain never asks the limiter for more than LIMITER_MAX_REDUCTION_DB: a
quiet, peaky playlist then stays a little under the target instead of being
squashed.
"""

from __future__ import annotations

TARGET_LUFS = -14.0
TRUE_PEAK_DBTP = -1.5
LIMITER_MAX_REDUCTION_DB = 3.0
OVERSAMPLED_RATE = 192000
LOUDNORM = f"loudnorm=I={TARGET_LUFS:g}:TP={TRUE_PEAK_DBTP:g}:LRA=11"
"""Only for a mix ebur128 could not measure (see FFmpegRenderer._measure_loudness_filter)."""


def normalization_gain_db(integrated: float, true_peak: float) -> float:
    """The one gain for a mix measured at ``integrated`` LUFS with ``true_peak`` dBTP."""
    return min(TARGET_LUFS - integrated, TRUE_PEAK_DBTP - true_peak + LIMITER_MAX_REDUCTION_DB)


def normalization_filter(integrated: float, true_peak: float, output_rate: int = 48000) -> str:
    """FFmpeg audio filter applying the policy to a mix with these measurements."""
    gain = normalization_gain_db(integrated, true_peak)
    chain = f"volume={gain:.2f}dB"
    if true_peak + gain > TRUE_PEAK_DBTP:
        chain += (f",aresample={OVERSAMPLED_RATE},"
                  f"alimiter=limit={10 ** (TRUE_PEAK_DBTP / 20):.4f}:attack=1:release=60:level=0:latency=1,"
                  f"aresample={output_rate}")
    return chain
