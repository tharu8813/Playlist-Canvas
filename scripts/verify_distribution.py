"""Validate the files required by a PyInstaller one-folder distribution."""

from __future__ import annotations

from pathlib import Path


REQUIRED_FILES = (
    Path("app/resources/app_icon.ico"),
    Path("app/resources/ko.json"),
    Path("app/resources/en.json"),
    Path("app/resources/language-pack-template.json"),
    Path("app/ui/studio.qss"),
    Path("assets/icons/check.svg"),
    Path("LICENSE.txt"),
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    distribution = root / "dist" / "Playlist Canvas"
    executable = distribution / "Playlist Canvas.exe"
    internal = distribution / "_internal"
    missing = [str(path) for path in REQUIRED_FILES if not (internal / path).is_file()]
    if not executable.is_file():
        missing.append(str(executable.relative_to(root)))
    numpy_core = internal / "numpy" / "_core"
    if not numpy_core.is_dir() or not list(numpy_core.glob("_multiarray_umath*.pyd")):
        missing.append("_internal/numpy/_core/_multiarray_umath*.pyd")
    if missing:
        print("Missing distribution files:")
        print("\n".join(f"- {path}" for path in missing))
        return 1
    print(f"Distribution OK: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
