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
    Path("python312.dll"),
    Path("app/automix/analysis/models/beat_this_final0_int8.onnx"),
    Path("app/automix/analysis/models/umxhq_vocals_int8.onnx"),
    Path("app/automix/analysis/models/BEAT_THIS_LICENSE.txt"),
    Path("app/automix/analysis/models/OPEN_UNMIX_LICENSE.txt"),
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
    if not list((internal / "onnxruntime" / "capi").glob("onnxruntime_pybind11_state*.pyd")):
        missing.append("_internal/onnxruntime/capi/onnxruntime_pybind11_state*.pyd")
    if missing:
        print("Missing distribution files:")
        print("\n".join(f"- {path}" for path in missing))
        return 1
    print(f"Distribution OK: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
