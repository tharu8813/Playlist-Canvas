"""Remove temp folders an earlier export/preview left behind after a crash or kill."""

from __future__ import annotations

from pathlib import Path
import shutil
from tempfile import gettempdir
import time

# Every TemporaryDirectory prefix the app creates in the system temp folder.
# "playlist-video-" also covers "playlist-video-frames-".
STALE_PREFIXES = (
    "playlist-video-",
    "playlist-audio-",
    "playlist-preview-audio-",
    "playlist-timestamps-",
)


def sweep_stale_temp_dirs(max_age_days: float = 2.0, root: Path | None = None) -> int:
    """Delete app temp folders untouched for ``max_age_days``; return how many went.

    The age guard keeps a second running instance's live export safe.
    """
    cutoff = time.time() - max_age_days * 86_400
    removed = 0
    for entry in (root or Path(gettempdir())).iterdir():
        try:
            if (
                entry.name.startswith(STALE_PREFIXES)
                and entry.is_dir()
                and entry.stat().st_mtime < cutoff
            ):
                shutil.rmtree(entry)
                removed += 1
        except OSError:
            continue  # still locked or already gone; retry next launch
    return removed
