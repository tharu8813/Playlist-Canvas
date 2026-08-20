"""Persistent settings and deployment-safe AI project-builder prompts."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QSettings, Signal

from app import __version__

@dataclass(frozen=True, slots=True)
class AIProjectPromptSettings:
    """Options controlling the copied AI Project Builder prompt."""

    language: str = "auto"
    question_policy: str = "adaptive"
    assumption_policy: str = "balanced"
    detail_level: str = "standard"
    output_format: str = "pvsproj"
    deliverable: str = "project"
    content_policy: str = "auto"
    missing_media_policy: str = "placeholder"
    canvas_preset: str = "auto"
    design_style: str = "auto"
    feature_policy: str = "smart"
    include_lyrics: bool = True
    include_audio_reactive: bool = True
    include_motion: bool = True
    include_thumbnail: bool = True
    validate_output: bool = True
    include_technical_context: bool = True
    project_brief: str = ""
    custom_instructions: str = ""


class AIProjectPromptService(QObject):
    """Store prompt preferences and build a self-contained compatibility prompt."""

    changed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = QSettings()
        self._current = self._load()

    @property
    def current(self) -> AIProjectPromptSettings:
        return self._current

    def save(self, settings: AIProjectPromptSettings) -> None:
        normalized = AIProjectPromptSettings(
            language=settings.language if settings.language in {"auto", "ko", "en"} else "auto",
            question_policy=(settings.question_policy if settings.question_policy in
                             {"adaptive", "never", "always"} else "adaptive"),
            assumption_policy=(settings.assumption_policy if settings.assumption_policy in
                               {"conservative", "balanced", "creative"} else "balanced"),
            detail_level=(settings.detail_level if settings.detail_level in
                          {"quick", "standard", "detailed"} else "standard"),
            output_format=settings.output_format if settings.output_format in
            {"pvsproj", "json"} else "pvsproj",
            deliverable=(settings.deliverable if settings.deliverable in
                         {"project", "blueprint", "project_and_blueprint"}
                         else "project"),
            content_policy=(settings.content_policy if settings.content_policy in
                            {"auto", "ask", "embed", "reference"} else "auto"),
            missing_media_policy=(
                settings.missing_media_policy if settings.missing_media_policy in
                {"placeholder", "omit", "ask"} else "placeholder"
            ),
            canvas_preset=(settings.canvas_preset if settings.canvas_preset in
                           {"auto", "landscape", "portrait", "square"} else "auto"),
            design_style=(settings.design_style if settings.design_style in
                          {"auto", "minimal", "modern", "cinematic", "energetic"}
                          else "auto"),
            feature_policy=(settings.feature_policy if settings.feature_policy in
                            {"request_only", "smart", "showcase"} else "smart"),
            include_lyrics=bool(settings.include_lyrics),
            include_audio_reactive=bool(settings.include_audio_reactive),
            include_motion=bool(settings.include_motion),
            include_thumbnail=bool(settings.include_thumbnail),
            validate_output=bool(settings.validate_output),
            include_technical_context=bool(settings.include_technical_context),
            project_brief=str(settings.project_brief).strip(),
            custom_instructions=str(settings.custom_instructions).strip(),
        )
        self._settings.beginGroup("ai_project_prompt")
        for key, value in (
            ("language", normalized.language),
            ("question_policy", normalized.question_policy),
            ("assumption_policy", normalized.assumption_policy),
            ("detail_level", normalized.detail_level),
            ("output_format", normalized.output_format),
            ("deliverable", normalized.deliverable),
            ("content_policy", normalized.content_policy),
            ("missing_media_policy", normalized.missing_media_policy),
            ("canvas_preset", normalized.canvas_preset),
            ("design_style", normalized.design_style),
            ("feature_policy", normalized.feature_policy),
            ("include_lyrics", normalized.include_lyrics),
            ("include_audio_reactive", normalized.include_audio_reactive),
            ("include_motion", normalized.include_motion),
            ("include_thumbnail", normalized.include_thumbnail),
            ("validate_output", normalized.validate_output),
            ("include_technical_context", normalized.include_technical_context),
            ("project_brief", normalized.project_brief),
            ("custom_instructions", normalized.custom_instructions),
        ):
            self._settings.setValue(key, value)
        self._settings.endGroup()
        self._current = normalized
        self.changed.emit(normalized)

    def generate(self, settings: AIProjectPromptSettings, ui_language: str) -> str:
        """Generate a prompt that works without this application's source tree."""
        language = ui_language if settings.language == "auto" else settings.language
        return self._generate_korean(settings) if language == "ko" else self._generate_english(settings)

    def _load(self) -> AIProjectPromptSettings:
        self._settings.beginGroup("ai_project_prompt")
        settings = AIProjectPromptSettings(
            language=str(self._settings.value("language", "auto")),
            question_policy=str(self._settings.value("question_policy", "adaptive")),
            assumption_policy=str(self._settings.value("assumption_policy", "balanced")),
            detail_level=str(self._settings.value("detail_level", "standard")),
            output_format=str(self._settings.value("output_format", "pvsproj")),
            deliverable=str(self._settings.value("deliverable", "project")),
            content_policy=str(self._settings.value("content_policy", "auto")),
            missing_media_policy=str(self._settings.value("missing_media_policy", "placeholder")),
            canvas_preset=str(self._settings.value("canvas_preset", "auto")),
            design_style=str(self._settings.value("design_style", "auto")),
            feature_policy=str(self._settings.value("feature_policy", "smart")),
            include_lyrics=self._bool_value("include_lyrics", True),
            include_audio_reactive=self._bool_value("include_audio_reactive", True),
            include_motion=self._bool_value("include_motion", True),
            include_thumbnail=self._bool_value("include_thumbnail", True),
            validate_output=self._bool_value("validate_output", True),
            include_technical_context=self._bool_value("include_technical_context", True),
            project_brief=str(self._settings.value("project_brief", "")),
            custom_instructions=str(self._settings.value("custom_instructions", "")),
        )
        self._settings.endGroup()
        return settings

    def _bool_value(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _question_topics(level: str, korean: bool) -> str:
        topics = [
            ("프로젝트 목적, 영상 제목, 시청자", "project purpose, video title, and audience"),
            ("캔버스 해상도와 화면비", "canvas resolution and aspect ratio"),
            ("음원·이미지·로고·폰트·가사 파일과 실제 경로", "audio, image, logo, font, and lyrics files with real paths"),
            ("전체 분위기, 장르, 참고 디자인", "mood, genre, and visual references"),
            ("색상 팔레트, 타이포그래피, 여백, 시각적 위계", "palette, typography, spacing, and visual hierarchy"),
            ("앨범 커버, 제목, 아티스트, 시간, 진행 바의 배치", "placement of cover, title, artist, time, and progress"),
            ("비주얼라이저·파형·레벨 미터 스타일과 반응", "visualizer, waveform, and level-meter style"),
            ("트랙 목록의 스타일, 표시 범위, 현재 곡 강조", "track-list style, range, and current-track emphasis"),
            ("가사 문맥 줄 수, 전환, 강조, 타이밍 보정", "lyrics context, transition, emphasis, and timing offset"),
            ("배경 방식, 앰비언트 앨범 아트, 파티클", "background, ambient album art, and particles"),
            ("곡 시작·종료 및 Now Playing 애니메이션", "track transition and now-playing animations"),
            ("콘텐츠 포함 또는 외부 경로 참조 정책", "embedded versus externally referenced content"),
            ("프로젝트 썸네일 구성", "project thumbnail composition"),
            ("필수·제외 요소와 접근성 요구", "required, excluded, and accessibility constraints"),
            ("출력 파일 이름과 저장 위치", "output filename and destination"),
            ("재사용할 제작 프롬프트의 범위", "scope of the reusable production prompt"),
        ]
        count = {"quick": 6, "standard": 11, "detailed": 16}.get(level, 16)
        return "\n".join(
            f"{index + 1}. {topic[0 if korean else 1]}"
            for index, topic in enumerate(topics[:count])
        )

    def _generate_korean(self, settings: AIProjectPromptSettings) -> str:
        format_text = "단일 파일 패키지(.pvsproj)" if settings.output_format == "pvsproj" else "UTF-8 프로젝트 JSON(.project.json)"
        deliverable = {
            "project": "Playlist Canvas에서 바로 열 수 있는 검증된 프로젝트 파일",
            "blueprint": "다른 AI가 같은 결과를 재현할 수 있는 독립 실행형 제작 프롬프트",
            "project_and_blueprint": "검증된 프로젝트 파일과 재사용 가능한 제작 프롬프트 둘 다",
        }[settings.deliverable]
        execution = {
            "project": "확정된 설계로 실제 프로젝트 파일을 만든다.",
            "blueprint": "파일은 만들지 않고 확정된 설계와 아래 호환 규격을 담은 독립 실행형 제작 프롬프트를 만든다.",
            "project_and_blueprint": "실제 프로젝트 파일을 만들고 같은 결과를 재현할 독립 실행형 제작 프롬프트도 만든다.",
        }[settings.deliverable]
        policy = {
            "auto": "첨부/접근 가능한 실제 파일은 포함하고, 유효한 절대 경로만 외부 참조",
            "ask": "결과에 실제 영향을 줄 때만 포함 또는 외부 참조 여부를 질문",
            "embed": "실제 콘텐츠를 패키지 내부에 포함",
            "reference": "사용자의 실제 파일 경로를 외부 참조",
        }[settings.content_policy]
        question_rule = {
            "adaptive": (
                "현재 요청과 아래 프로젝트 요구사항을 먼저 분석한다. 유효한 프로젝트를 만들 만큼 "
                "정보가 있으면 추가 질문 없이 같은 응답에서 바로 최종 산출물을 생성한다. 질문은 "
                "필수 미디어가 없고 대체할 수 없거나, 요구사항이 서로 충돌하거나, 누락된 선택이 "
                "결과를 크게 바꾸는 경우에만 한다. 질문이 필요하면 이미 제공된 내용은 빼고 최대 "
                "한 번의 짧은 묶음 질문으로 끝낸다. 색상·배치 같은 사소한 취향은 합리적으로 추론한다."
            ),
            "never": (
                "추가 질문을 하지 않는다. 주어진 정보와 아래 기본값으로 합리적인 결정을 내리고 "
                "같은 응답에서 즉시 최종 산출물을 생성한다. 모든 추론은 결과 보고에 짧게 밝힌다."
            ),
            "always": (
                "생성 전에 아래 질문 주제 중 이미 답이 없는 항목만 한 번의 간단한 질문지로 확인한다. "
                "답변을 받으면 추가 확인 없이 바로 생성한다."
            ),
        }[settings.question_policy]
        assumption = {
            "conservative": "명시된 요구를 우선하고 미지정 요소는 단순하고 안전한 값으로 채운다.",
            "balanced": "명시된 요구를 지키면서 장르와 목적에 맞는 실용적인 기본값을 선택한다.",
            "creative": "명시된 요구를 지키되 미지정 요소에는 완성도를 높이는 창의적 결정을 적극 적용한다.",
        }[settings.assumption_policy]
        missing_media = {
            "placeholder": "없는 미디어는 경로 없는 시각적 플레이스홀더로 대체하고 보고한다.",
            "omit": "없는 미디어에 의존하는 선택 요소는 생략하고 보고한다.",
            "ask": "필수 미디어가 없을 때만 한 번에 모아 요청한다.",
        }[settings.missing_media_policy]
        canvas = {
            "auto": "요청의 플랫폼을 추론하고, 알 수 없으면 1920×1080 가로형",
            "landscape": "1920×1080 가로형",
            "portrait": "1080×1920 세로형",
            "square": "1080×1080 정사각형",
        }[settings.canvas_preset]
        style = {
            "auto": "요청의 장르와 목적에 맞게 자동 선택(판단 근거가 없으면 모던)",
            "minimal": "미니멀",
            "modern": "모던",
            "cinematic": "시네마틱",
            "energetic": "에너지틱",
        }[settings.design_style]
        feature = {
            "request_only": "사용자가 요청한 기능만 사용",
            "smart": "요청과 콘텐츠에 도움이 되는 기능만 선별 사용",
            "showcase": "혼잡하지 않은 범위에서 앱의 표현 기능을 풍부하게 활용",
        }[settings.feature_policy]
        allowed = ", ".join(name for enabled, name in (
            (settings.include_lyrics, "가사/자막"),
            (settings.include_audio_reactive, "비주얼라이저·파형·레벨 미터"),
            (settings.include_motion, "등장·퇴장·곡 전환 애니메이션"),
        ) if enabled) or "추가 기능 없음"
        compatibility = self._compatibility_korean(settings.include_technical_context)
        thumbnail = "thumbnail.png를 패키지 루트에 포함한다." if settings.include_thumbnail else "썸네일은 생략한다."
        validation = (
            "완성 파일을 다시 열어 ZIP 구조와 project.json을 파싱하고, 필수 키·타입·고유 ID·캔버스 경계·모든 자산 경로를 검사한다."
            if settings.validate_output else "project.json이 유효한 UTF-8 JSON인지 검사한다."
        )
        brief = settings.project_brief or "(입력되지 않음 — 이 프롬프트 뒤에 사용자의 프로젝트 요구사항이 이어질 수 있음)"
        custom = settings.custom_instructions or "(추가 지침 없음)"
        topics = self._question_topics(settings.detail_level, True)
        return f"""# Playlist Canvas 배포판용 AI 프로젝트 빌더

너는 설치된 Playlist Canvas용 호환 프로젝트를 설계하고 만드는 제작 에이전트다. 사용자는 개발 소스코드나 Python 환경이 없을 수 있다. 저장소, `app/...` 파일, 내부 클래스, `ProjectService` 접근을 요구하지 말고 이 프롬프트의 호환 규격만 사용하라.

## 즉시 실행할 프로젝트 요구사항
{brief}

## 추가 사용자 지침
{custom}

## 작업 방식 — 원샷 생성을 우선
{question_rule}
{assumption}

질문이 정말 필요할 때만 참고할 주제:

{topics}

- 질문 수준: {settings.detail_level}
- 콘텐츠 정책: {policy}
- 누락 미디어: {missing_media}
- 목표 형식: {format_text}
- 최종 산출물: {deliverable}
- 캔버스 기본값: {canvas}
- 디자인 기본값: {style}
- 기능 사용 범위: {feature}
- 사용 가능한 선택 기능: {allowed}

요구사항이 충분하면 별도의 설계 승인이나 확인 응답을 기다리지 않는다. 내부적으로 캔버스, 재생 목록, 레이어/Z 순서, 좌표·크기·스타일·애니메이션, 동적 텍스트, 콘텐츠 정책을 정한 뒤 바로 생성한다. 사용자가 제공한 정보를 다시 묻거나 장황하게 반복하지 않는다.

## 프로젝트 생성
{execution}

{compatibility}

제작 규칙:
- 앱 소스코드를 수정하거나 개발 환경 설치를 요구하지 않는다.
- 최소한 `background` 1개와 곡 제목/아티스트용 `text`를 포함하고, 나머지는 확정된 설계에 필요한 것만 추가한다.
- 동적 텍스트 토큰은 `%title%`, `%artist%`, `%album%`, `%track%`, `%track_total%`, `%filename%`, `%track_current_time%`, `%track_total_time%`, `%video_current_time%`, `%video_total_time%`만 사용한다.
- 좌표 원점은 왼쪽 위이며 모든 소스는 가능한 한 캔버스 안에 둔다. 텍스트 대비와 현재/비활성 곡 구분도 검사한다.
- 가짜 파일 경로를 만들지 않는다. {missing_media}
- {thumbnail}
- 검증: {validation}

## 전달
실제 파일 생성 도구가 있으면 사용자가 지정한 폴더에 산출물을 만들고 다운로드 가능한 파일 또는 절대 경로를 제공한다. 파일 생성 도구가 없으면 전체 `project.json`을 하나의 JSON 코드 블록으로 제공하고, 사용자가 UTF-8로 저장하는 정확한 방법을 안내한다. 생성 파일명, 디자인 요약, 포함/참조 콘텐츠, 대체 또는 누락 항목, 검증 결과를 짧게 보고한다. 제작 프롬프트가 산출물에 포함되면 사용자의 답변이 반영된 독립 실행형 Markdown 프롬프트도 함께 제공한다.

최우선 원칙: 현재 메시지와 ‘즉시 실행할 프로젝트 요구사항’만으로 만들 수 있다면 질문·승인·중간 확인 없이 한 번에 완성한다.
"""

    def _generate_english(self, settings: AIProjectPromptSettings) -> str:
        format_text = "a single-file .pvsproj package" if settings.output_format == "pvsproj" else "a UTF-8 .project.json file"
        deliverable = {
            "project": "a validated project file that opens directly in Playlist Canvas",
            "blueprint": "a reusable, self-contained production prompt",
            "project_and_blueprint": "both a validated project and a reusable production prompt",
        }[settings.deliverable]
        execution = {
            "project": "Create the actual project file from the confirmed design.",
            "blueprint": "Do not create a file; produce a self-contained prompt containing the confirmed design and the compatibility contract below.",
            "project_and_blueprint": "Create the project file and a self-contained prompt that can reproduce it.",
        }[settings.deliverable]
        policy = {
            "auto": "embed attached or accessible real files and reference only valid absolute paths",
            "ask": "ask about embedding versus references only when it materially affects the result",
            "embed": "embed real content in the package",
            "reference": "use the user's real external file paths",
        }[settings.content_policy]
        question_rule = {
            "adaptive": (
                "Analyze the current request and embedded brief first. If they are sufficient for a valid project, "
                "ask no follow-up questions and generate the final deliverable in the same response. Ask only when "
                "required media is missing and cannot be substituted, requirements conflict, or a missing decision "
                "would materially change the result. When necessary, ask at most one compact batch of unanswered "
                "questions. Infer cosmetic preferences such as colors and layout."
            ),
            "never": (
                "Do not ask follow-up questions. Make reasonable decisions from the supplied information and defaults, "
                "then generate the final deliverable immediately in the same response. Briefly report assumptions."
            ),
            "always": (
                "Before generating, ask one compact questionnaire containing only unanswered items from the topics "
                "below. After the answer, generate without another confirmation round."
            ),
        }[settings.question_policy]
        assumption = {
            "conservative": "Honor explicit requirements and fill unspecified elements with simple, safe defaults.",
            "balanced": "Honor explicit requirements and choose practical defaults suited to the genre and purpose.",
            "creative": "Honor explicit requirements and actively make creative choices that improve unspecified elements.",
        }[settings.assumption_policy]
        missing_media = {
            "placeholder": "Replace missing media with path-free visual placeholders and report them.",
            "omit": "Omit optional elements that depend on missing media and report them.",
            "ask": "Request missing required media once in a single batch.",
        }[settings.missing_media_policy]
        canvas = {
            "auto": "infer the platform; otherwise use 1920×1080 landscape",
            "landscape": "1920×1080 landscape",
            "portrait": "1080×1920 portrait",
            "square": "1080×1080 square",
        }[settings.canvas_preset]
        style = {
            "auto": "infer from genre and purpose; otherwise use modern",
            "minimal": "minimal",
            "modern": "modern",
            "cinematic": "cinematic",
            "energetic": "energetic",
        }[settings.design_style]
        feature = {
            "request_only": "use only features explicitly requested",
            "smart": "select only features that help the request and content",
            "showcase": "use the app's expressive features richly without clutter",
        }[settings.feature_policy]
        allowed = ", ".join(name for enabled, name in (
            (settings.include_lyrics, "lyrics/subtitles"),
            (settings.include_audio_reactive, "visualizers/waveforms/level meters"),
            (settings.include_motion, "entrance/exit/track-transition animation"),
        ) if enabled) or "no optional features"
        compatibility = self._compatibility_english(settings.include_technical_context)
        thumbnail = "Place thumbnail.png at the package root." if settings.include_thumbnail else "Omit the thumbnail."
        validation = (
            "Reopen the result, parse the ZIP and project.json, and verify required keys, types, unique IDs, canvas bounds, and every asset path."
            if settings.validate_output else "Verify that project.json is valid UTF-8 JSON."
        )
        brief = settings.project_brief or "(Not entered — the user's project request may follow this prompt.)"
        custom = settings.custom_instructions or "(No additional instructions.)"
        topics = self._question_topics(settings.detail_level, False)
        return f"""# Playlist Canvas Deployment AI Project Builder

You are a production agent that creates compatible projects for the installed Playlist Canvas application. The user may have no source repository or Python environment. Never request repository files, `app/...` modules, internal classes, or `ProjectService`; work only from this prompt's compatibility contract.

## Project brief to execute now
{brief}

## Additional user instructions
{custom}

## Workflow — prefer one-shot generation
{question_rule}
{assumption}

Use these topics only if a question is truly necessary:

{topics}

- Question depth: {settings.detail_level}
- Content policy: {policy}
- Missing media: {missing_media}
- Target format: {format_text}
- Final deliverable: {deliverable}
- Canvas default: {canvas}
- Design default: {style}
- Feature scope: {feature}
- Allowed optional features: {allowed}

When the brief is sufficient, do not wait for design approval or confirmation. Internally determine the canvas, playlist, layer/Z order, geometry, styling, animation, dynamic text, and content policy, then generate immediately. Do not repeat supplied information or add a ceremonial confirmation step.

## Generate the project
{execution}

{compatibility}

Production rules:
- Do not modify application source code or require a development environment.
- Include at least one `background` and metadata `text`; add other sources only when the confirmed design needs them.
- Supported dynamic tokens are `%title%`, `%artist%`, `%album%`, `%track%`, `%track_total%`, `%filename%`, `%track_current_time%`, `%track_total_time%`, `%video_current_time%`, and `%video_total_time%`.
- Coordinates start at the top-left. Keep sources within the canvas where practical and check contrast and current/inactive track readability.
- Never invent media paths. {missing_media}
- {thumbnail}
- Validation: {validation}

## Deliver
If file tools are available, create the result in the user's requested folder and provide the downloadable file or absolute path. Otherwise return the complete `project.json` in one JSON code block and give exact UTF-8 save instructions. Report the filename, concise design summary, embedded/referenced content, substitutions or missing items, and validation result. If a reusable prompt is requested, also return a self-contained Markdown prompt incorporating the user's answers.

Highest-priority rule: If the current message and “Project brief to execute now” are sufficient, complete the work in one response without questions, approval, or intermediate confirmation.
"""

    @staticmethod
    def _compatibility_korean(detailed: bool) -> str:
        if not detailed:
            return f"""호환 규격 요약: 프로젝트 본문은 UTF-8 JSON 객체이며 `version: 2`, `app_version: {__version__}`, `canvas`, `settings`, `sources`, `playlist`, `groups`, `content_library`를 가진다. JSON 출력은 `.project.json`으로 저장한다. `.pvsproj` 출력은 이 JSON을 ZIP 루트의 `project.json`으로 넣은 ZIP 파일이며 확장자만 `.pvsproj`이다. 미디어를 포함할 때는 `assets/` 아래에 넣고 JSON 경로도 같은 상대 경로를 사용한다."""
        return """### 내장 호환 규격(v2)
프로젝트 루트 예시(주석 없이 유효한 JSON으로 생성):
```json
{
  "version": 2,
  "app_version": "__APP_VERSION__",
  "canvas": {"width": 1280, "height": 720, "show_grid": true, "snap_enabled": true, "zoom": 1.0},
  "theme": "dark",
  "language": "ko",
  "settings": {"title": "제목", "description": "", "author": "", "content_mode": "embed", "thumbnail_mode": "canvas", "thumbnail_path": "", "created_at": "ISO-8601 UTC", "modified_at": "ISO-8601 UTC"},
  "content_library": [], "sources": [], "groups": [], "playlist": []
}
```
- `sources` 항목 필수 키: `source_type`, `name`. 각 항목에는 고유 UUID 문자열 `id`를 권장한다. 나머지 키는 생략 시 앱 기본값이 적용된다.
- 지원 `source_type`: `image`, `text`, `shape`, `progress_bar`, `time`, `album_cover`, `logo`, `watermark`, `background`, `audio_visualizer`, `lyrics`, `track_list`, `now_playing`, `audio_waveform`, `audio_level_meter`, `particle_overlay`.
- 공통 소스 키: `id`, `x`, `y`, `width`, `height`, `rotation`, `scale`, `opacity`, `border_radius`, `z_index`, `visible`, `locked`, `fill_color`, `content_path`, `font_path`, `blur`, `outline_color`, `outline_width`, `group_id`, `animation_in`, `animation_out`, `animation_in_duration`, `animation_out_duration`, `timeline_start`, `timeline_duration`. 애니메이션 시간은 초 단위이며 시작과 종료를 각각 지정한다.
- `timeline_start`는 전체 영상 기준 표시 시작 초이며 `timeline_duration`은 표시 지속 초다. 지속시간이 `0`이면 시작 시각부터 영상 끝까지 표시한다. `time` 소스의 `text`에는 `%track_current_time% / %track_total_time%` 같은 시간 토큰을 사용한다.
- 중첩 스타일: `shadow`는 `enabled,color,blur_radius,offset_x,offset_y,opacity`; `gradient`는 `enabled,start_color,end_color,angle`만 사용한다.
- 텍스트/이미지 키: `text`, `font_family`, `font_size`, `font_weight`, `text_alignment`(`left|center|right`), `text_overflow`(`wrap|ellipsis|clip`), `image_fit_mode`(`cover|contain|stretch`), `shape_kind`(`rectangle|circle|line`).
- 배경/진행 키: `background_mode`(`color|image|album_art`), `background_ambient`, `brightness`, `contrast`, `progress_style`(`rounded|spotify|apple|youtube|gradient`), `progress_value`, `progress_track_color`, `progress_mode`(`track|video`).
- 오디오 시각화 키: `visualizer_style`(`bars|wave|dots|line|mirror|spectrum|led|center|capsule|arc`), `visualizer_bars`, `visualizer_line_width`, `visualizer_sensitivity`, `visualizer_reactivity`, `visualizer_noise_gate`, `visualizer_min_level`, `visualizer_max_level`, `visualizer_attack`, `visualizer_release`, `visualizer_smoothing`, `visualizer_curve`, `waveform_style`(`line|filled|mirror`).
- 가사 키: `subtitle_fallback`, `subtitle_style`(`karaoke|minimal|neon`), `subtitle_animation`(`apple_music|spotify|blur_reveal|none|fade|scroll_up|scroll_down|pop`), `subtitle_animation_duration`, `subtitle_context_lines`, `subtitle_next_lines`, `subtitle_line_spacing`, `subtitle_previous_opacity`, `subtitle_previous_blur`, `subtitle_timing_offset`. 별도 요구가 없으면 부드러운 `apple_music`을 우선 사용한다.
- 트랙 목록/Now Playing 키: `track_list_count`, `track_list_style`(`compact|cards|queue|minimal|scroll|glass|pills`), `track_list_window`(`centered|upcoming|history`), `track_list_show_number`, `track_list_show_artist`, `track_list_show_album`, `track_list_marker`(`play|dot|line|none`), `track_list_row_spacing`, `track_list_item_padding`, `track_list_current_color`, `track_list_inactive_color`, `track_list_current_background`, `track_list_inactive_opacity`, `track_list_current_scale`, `track_list_show_dividers`, `now_playing_style`(`card|minimal|glass`), `now_playing_duration`, `now_playing_exit_animation`(`fade|slide_up|slide_down|zoom`), `now_playing_exit_duration`, `album_frame_style`(`rounded|circle|polaroid|glass`).
- 레벨 미터/파티클 키: `level_meter_mode`(`stereo|mono`), `level_meter_style`(`gradient|solid|led|segments`), `level_meter_orientation`(`vertical|horizontal`) 및 `level_meter_`로 시작하는 감도·attack·release·min/max_level·segments·gap·show_peak·peak_hold·peak_decay·track/low/mid/high_color; `particle_style`(`dust|neon|noise|snow|stars|bokeh|confetti`) 및 `particle_`로 시작하는 density·speed·min_size·max_size·opacity·direction·drift·twinkle·glow·secondary_color·seed.
- `playlist` 항목 필수 키: `file_path`, `title`. 선택 키: `id`, `artist`, `album`, `cover_path`, `duration_seconds`, `start_time_seconds`, `enabled`, `lyrics_path`, `lyrics`, `lyrics_timing_offset_seconds`. `cover_path`는 음원 내장 커버보다 우선 사용할 곡별 이미지다. `lyrics`는 `[{"start": 시작초, "end": 종료초, "text": "가사"}]` 배열로 만들며, 곡별 가사 보정은 초 단위의 `lyrics_timing_offset_seconds`에 넣는다.
- 재생목록 순서대로 각 `start_time_seconds`는 이전 활성 곡의 종료 시각보다 빠르지 않게 만든다. 값을 생략하면 이전 곡 직후에 자동 배치된다.
- `groups`: `[{"id": "UUID", "name": "그룹명"}]`. `content_library`: `[{"id": "UUID", "path": "경로", "media_type": "audio|image|font|lyrics", "name": "이름"}]`.
- `.project.json`: 위 객체를 BOM 없는 UTF-8 JSON으로 저장한다.
- `.pvsproj`: 표준 ZIP 파일이다. 루트에 위 JSON을 `project.json`이라는 이름으로 넣는다. 포함 모드에서는 실제 파일을 `assets/고유이름.확장자`로 넣고 `content_path`, `font_path`, `file_path`, `cover_path`, `lyrics_path`, `content_library[].path`에 동일한 POSIX 상대 경로를 기록한다. 선택적으로 루트에 PNG `thumbnail.png`를 넣는다. ZIP 파일 확장자를 `.pvsproj`로 지정한다. 경로에 `..` 또는 절대 경로를 넣지 않는다.
- 외부 참조 모드에서는 `settings.content_mode`를 `reference`로 하고 사용자가 제공한 실제 절대 경로만 기록한다.""".replace("__APP_VERSION__", __version__)

    @staticmethod
    def _compatibility_english(detailed: bool) -> str:
        if not detailed:
            return f"""Compatibility summary: the body is a UTF-8 JSON object with `version: 2`, `app_version: {__version__}`, `canvas`, `settings`, `sources`, `playlist`, `groups`, and `content_library`. Save JSON output as `.project.json`. A `.pvsproj` is a ZIP file containing that JSON as root-level `project.json`; embedded media goes under `assets/` and the JSON uses matching relative paths."""
        return """### Embedded compatibility contract (v2)
Project root example (produce valid JSON without comments):
```json
{
  "version": 2,
  "app_version": "__APP_VERSION__",
  "canvas": {"width": 1280, "height": 720, "show_grid": true, "snap_enabled": true, "zoom": 1.0},
  "theme": "dark", "language": "en",
  "settings": {"title": "Title", "description": "", "author": "", "content_mode": "embed", "thumbnail_mode": "canvas", "thumbnail_path": "", "created_at": "ISO-8601 UTC", "modified_at": "ISO-8601 UTC"},
  "content_library": [], "sources": [], "groups": [], "playlist": []
}
```
- Every `sources` item requires `source_type` and `name`; a unique UUID string `id` is recommended. Omitted optional keys receive application defaults.
- Supported `source_type`: `image`, `text`, `shape`, `progress_bar`, `time`, `album_cover`, `logo`, `watermark`, `background`, `audio_visualizer`, `lyrics`, `track_list`, `now_playing`, `audio_waveform`, `audio_level_meter`, `particle_overlay`.
- Common source keys: `id`, `x`, `y`, `width`, `height`, `rotation`, `scale`, `opacity`, `border_radius`, `z_index`, `visible`, `locked`, `fill_color`, `content_path`, `font_path`, `blur`, `outline_color`, `outline_width`, `group_id`, `animation_in`, `animation_out`, `animation_in_duration`, `animation_out_duration`, `timeline_start`, `timeline_duration`. Animation durations are seconds and are configured independently for entrance and exit.
- `timeline_start` is the source's show time in whole-video seconds; `timeline_duration` is its visible duration. A zero duration means visible from its start through the end. For a `time` source, use time tokens such as `%track_current_time% / %track_total_time%` in `text`.
- Nested style keys: `shadow` uses `enabled,color,blur_radius,offset_x,offset_y,opacity`; `gradient` uses `enabled,start_color,end_color,angle`.
- Text/image keys: `text`, `font_family`, `font_size`, `font_weight`, `text_alignment` (`left|center|right`), `text_overflow` (`wrap|ellipsis|clip`), `image_fit_mode` (`cover|contain|stretch`), `shape_kind` (`rectangle|circle|line`).
- Background/progress keys: `background_mode` (`color|image|album_art`), `background_ambient`, `brightness`, `contrast`, `progress_style` (`rounded|spotify|apple|youtube|gradient`), `progress_value`, `progress_track_color`, `progress_mode` (`track|video`).
- Audio visualization keys: `visualizer_style` (`bars|wave|dots|line|mirror|spectrum|led|center|capsule|arc`), `visualizer_bars`, `visualizer_line_width`, `visualizer_sensitivity`, `visualizer_reactivity`, `visualizer_noise_gate`, `visualizer_min_level`, `visualizer_max_level`, `visualizer_attack`, `visualizer_release`, `visualizer_smoothing`, `visualizer_curve`, `waveform_style` (`line|filled|mirror`).
- Lyrics keys: `subtitle_fallback`, `subtitle_style` (`karaoke|minimal|neon`), `subtitle_animation` (`apple_music|spotify|blur_reveal|none|fade|scroll_up|scroll_down|pop`), `subtitle_animation_duration`, `subtitle_context_lines`, `subtitle_next_lines`, `subtitle_line_spacing`, `subtitle_previous_opacity`, `subtitle_previous_blur`, `subtitle_timing_offset`. Prefer the smooth `apple_music` option unless another transition is requested.
- Track-list/Now Playing keys: `track_list_count`, `track_list_style` (`compact|cards|queue|minimal|scroll|glass|pills`), `track_list_window` (`centered|upcoming|history`), `track_list_show_number`, `track_list_show_artist`, `track_list_show_album`, `track_list_marker` (`play|dot|line|none`), `track_list_row_spacing`, `track_list_item_padding`, `track_list_current_color`, `track_list_inactive_color`, `track_list_current_background`, `track_list_inactive_opacity`, `track_list_current_scale`, `track_list_show_dividers`, `now_playing_style` (`card|minimal|glass`), `now_playing_duration`, `now_playing_exit_animation` (`fade|slide_up|slide_down|zoom`), `now_playing_exit_duration`, `album_frame_style` (`rounded|circle|polaroid|glass`).
- Meter/particle keys: `level_meter_mode` (`stereo|mono`), `level_meter_style` (`gradient|solid|led|segments`), `level_meter_orientation` (`vertical|horizontal`) plus `level_meter_` sensitivity, attack, release, min/max_level, segments, gap, show_peak, peak_hold, peak_decay, and track/low/mid/high_color; `particle_style` (`dust|neon|noise|snow|stars|bokeh|confetti`) plus `particle_` density, speed, min_size, max_size, opacity, direction, drift, twinkle, glow, secondary_color, and seed.
- Every `playlist` item requires `file_path` and `title`; optional keys are `id`, `artist`, `album`, `cover_path`, `duration_seconds`, `start_time_seconds`, `enabled`, `lyrics_path`, `lyrics`, and `lyrics_timing_offset_seconds`. `cover_path` is a per-track image override used before embedded artwork. Use `[{"start": start_seconds, "end": end_seconds, "text": "line"}]` for inline lyrics and seconds for the per-track timing offset.
- In playlist order, never set `start_time_seconds` before the previous enabled track ends. Omit it to place the track immediately after the previous track.
- `groups`: `[{"id": "UUID", "name": "Group"}]`. `content_library`: `[{"id": "UUID", "path": "path", "media_type": "audio|image|font|lyrics", "name": "Name"}]`.
- `.project.json`: save the root object as UTF-8 JSON without a BOM.
- `.pvsproj`: create a standard ZIP with the JSON at root as `project.json`. In embed mode, put real files at `assets/unique-name.ext` and use the matching POSIX relative path in `content_path`, `font_path`, `file_path`, `cover_path`, `lyrics_path`, and `content_library[].path`. Optionally add root-level PNG `thumbnail.png`. Give the ZIP a `.pvsproj` extension. Never use absolute paths or `..` inside the package.
- For external references set `settings.content_mode` to `reference` and record only real absolute paths supplied by the user.""".replace("__APP_VERSION__", __version__)
