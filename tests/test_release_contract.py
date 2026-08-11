from __future__ import annotations

from pathlib import Path
import unittest

from app import __version__
from app.services.ai_project_prompt_service import (
    AIProjectPromptService, AIProjectPromptSettings,
)
from app.utils.subprocess_utils import hidden_process_kwargs


ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractTests(unittest.TestCase):
    def test_ai_prompt_is_deployment_self_contained(self) -> None:
        prompt = AIProjectPromptService().generate(
            AIProjectPromptSettings(language="ko"), "ko"
        )
        self.assertIn("project.json", prompt)
        self.assertIn(".pvsproj", prompt)
        self.assertIn('"start": 시작초', prompt)
        self.assertIn("소스코드", prompt)

    def test_adaptive_builder_prefers_one_shot_and_embeds_brief(self) -> None:
        prompt = AIProjectPromptService().generate(
            AIProjectPromptSettings(
                language="ko",
                question_policy="adaptive",
                project_brief="세로형 재즈 플레이리스트 영상을 만들어줘.",
                canvas_preset="portrait",
                design_style="cinematic",
            ),
            "ko",
        )
        self.assertIn("세로형 재즈 플레이리스트 영상을 만들어줘.", prompt)
        self.assertIn("추가 질문 없이", prompt)
        self.assertIn("1080×1920", prompt)
        self.assertNotIn("질문지부터 제시한다", prompt)

    def test_never_ask_policy_is_explicit(self) -> None:
        prompt = AIProjectPromptService().generate(
            AIProjectPromptSettings(language="en", question_policy="never"), "en"
        )
        self.assertIn("Do not ask follow-up questions", prompt)
        self.assertIn("generate the final deliverable immediately", prompt)

    def test_pyinstaller_does_not_bundle_development_ffmpeg(self) -> None:
        specification = (ROOT / "playlist_canvas.spec").read_text(encoding="utf-8")
        self.assertNotIn("bundled_ffmpeg", specification)
        self.assertIn("app_icon.ico", specification)
        self.assertIn('name="Playlist Canvas"', specification)

    def test_every_ffmpeg_subprocess_uses_hidden_window_options(self) -> None:
        paths = (
            "app/ffmpeg/managed_installer.py",
            "app/renderer/ffmpeg_renderer.py",
            "app/renderer/python_visualizer.py",
            "app/services/playlist_service.py",
            "app/dialogs/settings_dialog.py",
        )
        for relative_path in paths:
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            process_calls = source.count("subprocess.run(") + source.count("subprocess.Popen(")
            self.assertGreater(process_calls, 0, relative_path)
            self.assertEqual(
                source.count("hidden_process_kwargs()"), process_calls, relative_path
            )

    def test_windows_hidden_process_options_disable_console_windows(self) -> None:
        options = hidden_process_kwargs()
        self.assertIn("creationflags", options)
        self.assertIn("startupinfo", options)
        self.assertNotEqual(int(options["creationflags"]), 0)
        startup_info = options["startupinfo"]
        self.assertNotEqual(startup_info.dwFlags, 0)  # type: ignore[attr-defined]

    def test_gitignore_keeps_runtime_ffmpeg_source_tracked(self) -> None:
        ignore_file = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/ffmpeg/", ignore_file.splitlines())
        self.assertNotIn("ffmpeg/", ignore_file.splitlines())

    def test_release_icon_exists(self) -> None:
        icon = ROOT / "app" / "resources" / "app_icon.ico"
        self.assertTrue(icon.is_file())
        self.assertGreater(icon.stat().st_size, 1_000)

    def test_gpl_v3_license_is_applied_to_repository_build_and_installer(self) -> None:
        license_path = ROOT / "LICENSE.txt"
        license_text = license_path.read_text(encoding="utf-8")
        specification = (ROOT / "playlist_canvas.spec").read_text(encoding="utf-8")
        installer = (ROOT / "setup.iss").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 29 June 2007", license_text)
        self.assertIn('project_root / "LICENSE.txt"', specification)
        self.assertIn('#define LicenseFilePath "LICENSE.txt"', installer)
        self.assertIn('Source: "LICENSE.txt"; DestDir: "{app}"', installer)
        self.assertIn("GNU General Public License v3.0", readme)

    def test_release_version_is_semantic(self) -> None:
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(part.isdigit() for part in parts))

    def test_windows_and_python_packaging_versions_are_synchronized(self) -> None:
        parts = ", ".join(__version__.split("."))
        version_info = (ROOT / "windows_version_info.txt").read_text(
            encoding="utf-8"
        )
        packaging = (ROOT / "PACKAGING.md").read_text(encoding="utf-8")
        lock_file = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8")
        self.assertIn(f"filevers=({parts}, 0)", version_info)
        self.assertIn(f"prodvers=({parts}, 0)", version_info)
        self.assertIn('StringStruct("FileVersion", "' + __version__ + '")', version_info)
        self.assertIn("Python 3.12", packaging)
        self.assertIn("python312.dll", packaging)
        self.assertIn("Python 3.12", lock_file.splitlines()[0])
        self.assertNotIn("python314.dll", packaging)


if __name__ == "__main__":
    unittest.main()
