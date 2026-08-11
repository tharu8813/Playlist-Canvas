"""Searchable, offline user guide for the installed application."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from app.utils.i18n import Language, Translator


@dataclass(frozen=True, slots=True)
class HelpTopic:
    identifier: str
    title: str
    keywords: str
    body: str


class HelpDialog(QDialog):
    """Present a searchable topic list without requiring a web connection."""

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.translator = translator
        self._topics: list[HelpTopic] = []
        self.setObjectName("helpDialog")
        self.setMinimumSize(820, 600)
        self.resize(980, 720)

        self.title_label = QLabel()
        self.title_label.setObjectName("dialogTitle")
        self.intro_label = QLabel()
        self.intro_label.setObjectName("mutedLabel")
        self.intro_label.setWordWrap(True)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setObjectName("helpSearch")

        self.topic_list = QListWidget()
        self.topic_list.setObjectName("helpTopicList")
        self.topic_list.setMinimumWidth(210)
        self.topic_list.setMaximumWidth(300)
        self.browser = QTextBrowser()
        self.browser.setObjectName("helpBrowser")
        self.browser.setOpenExternalLinks(False)
        self.browser.document().setDefaultStyleSheet(
            "h1{font-size:22px;margin:0 0 12px 0;}"
            "h2{font-size:18px;margin:20px 0 8px 0;}"
            "h3{font-size:15px;margin:16px 0 6px 0;}"
            "p,li{font-size:13px;line-height:1.55;}"
            "li{margin-bottom:5px;}"
            "code{font-family:monospace;font-weight:600;padding:2px 5px;}"
            ".note{border-left:4px solid #1685D1;padding:8px 12px;margin:12px 0;}"
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.topic_list)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([245, 700])

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        self.buttons = buttons

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.intro_label)
        layout.addWidget(self.search_edit)
        layout.addWidget(splitter, 1)
        layout.addWidget(buttons)

        self.search_edit.textChanged.connect(self._filter_topics)
        self.topic_list.currentItemChanged.connect(self._show_topic)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        korean = self.translator.language is Language.KOREAN
        current_id = self.current_topic_id
        self.setWindowTitle("Playlist Canvas 도움말" if korean else "Playlist Canvas Help")
        self.title_label.setText("사용 설명서" if korean else "User Guide")
        self.intro_label.setText(
            "주제를 선택하거나 검색하세요. 이 도움말은 인터넷 연결 없이 사용할 수 있습니다."
            if korean else
            "Choose a topic or search the guide. This help is available without an internet connection."
        )
        self.search_edit.setPlaceholderText(
            "도움말 검색 (예: 내보내기, FFmpeg, 가사)"
            if korean else
            "Search help (for example: export, FFmpeg, lyrics)"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            "닫기" if korean else "Close"
        )
        self._topics = self._korean_topics() if korean else self._english_topics()
        self._filter_topics(self.search_edit.text(), current_id)

    @property
    def current_topic_id(self) -> str | None:
        item = self.topic_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def select_topic(self, identifier: str) -> None:
        """Select a topic by stable identifier for menu or test integration."""
        for row in range(self.topic_list.count()):
            item = self.topic_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == identifier:
                self.topic_list.setCurrentItem(item)
                return

    def _filter_topics(self, query: str, preferred_id: str | None = None) -> None:
        normalized = query.strip().casefold()
        selected_id = preferred_id or self.current_topic_id
        self.topic_list.blockSignals(True)
        self.topic_list.clear()
        for topic in self._topics:
            searchable = f"{topic.title} {topic.keywords} {topic.body}".casefold()
            if normalized and normalized not in searchable:
                continue
            item = QListWidgetItem(topic.title)
            item.setData(Qt.ItemDataRole.UserRole, topic.identifier)
            self.topic_list.addItem(item)
            if topic.identifier == selected_id:
                self.topic_list.setCurrentItem(item)
        if self.topic_list.currentItem() is None and self.topic_list.count():
            self.topic_list.setCurrentRow(0)
        self.topic_list.blockSignals(False)
        if self.topic_list.currentItem() is not None:
            self._show_topic(self.topic_list.currentItem(), None)
        else:
            korean = self.translator.language is Language.KOREAN
            self.browser.setHtml(
                "<h1>검색 결과 없음</h1><p>다른 검색어를 입력해 보세요.</p>"
                if korean else
                "<h1>No results</h1><p>Try a different search term.</p>"
            )

    def _show_topic(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        identifier = current.data(Qt.ItemDataRole.UserRole)
        topic = next(
            (entry for entry in self._topics if entry.identifier == identifier), None
        )
        if topic is not None:
            self.browser.setHtml(f"<h1>{topic.title}</h1>{topic.body}")
            self.browser.verticalScrollBar().setValue(0)

    @staticmethod
    def _korean_topics() -> list[HelpTopic]:
        return [
            HelpTopic("start", "시작하기", "새 프로젝트 시작 화면 기본 흐름",
                """<p>Playlist Canvas는 음악 재생목록과 시각 요소를 조합해 영상을 만드는 데스크톱 편집기입니다.</p>
                <h2>기본 작업 순서</h2><ol>
                <li><b>새 프로젝트</b>를 만들 때 화면 비율을 선택하거나 기존 <code>.pvsproj</code> 파일을 엽니다.</li>
                <li>오디오 파일을 플레이리스트에 추가하고 순서를 정합니다.</li>
                <li>캔버스에 배경, 텍스트, 앨범 커버, 진행 바, 비주얼라이저 등을 배치합니다.</li>
                <li>미리보기로 재생 결과를 확인한 뒤 MP4로 내보냅니다.</li></ol>
                <div class='note'>상단 도구 모음은 새로 만들기, 열기, 저장, 실행 취소, 프리셋과 AI 프로젝트 빌더를 빠르게 실행합니다.</div>"""),
            HelpTopic("workspace", "화면 구성과 패널", "화면 UI 패널 요소 프로젝트 콘텐츠 레이어 속성 플레이리스트 타임라인",
                """<p>편집 화면은 작업 목적에 따라 여러 패널로 나뉩니다.</p><ul>
                <li><b>왼쪽</b>: 요소 추가와 프로젝트 콘텐츠</li><li><b>가운데</b>: 캔버스 작업 공간</li>
                <li><b>오른쪽</b>: 레이어와 선택 요소 속성</li><li><b>아래</b>: 플레이리스트와 타임라인</li></ul>
                <p><b>보기 → 패널 표시</b>로 편집 패널을 접거나 다시 열 수 있습니다. 패널 경계를 드래그하면 작업에 맞게 너비와 높이를 조절할 수 있습니다.</p>
                <div class='note'>캔버스의 회색 작업 영역과 그리드는 편집 보조 화면이며 최종 영상에는 아트보드 내부만 포함됩니다.</div>"""),
            HelpTopic("sources", "요소 종류와 추가 방법", "요소 추가 배경 이미지 텍스트 도형 앨범 커버 로고 워터마크 진행 바 시간",
                """<p><b>요소</b> 탭 또는 <b>추가</b> 메뉴에서 캔버스 구성 요소를 넣습니다. 버튼에 마우스를 올리면 해당 요소의 용도와 주요 설정을 확인할 수 있습니다.</p>
                <h2>자주 쓰는 요소</h2><ul><li><b>배경·이미지·로고·워터마크</b>: 이미지 기반 장면 구성</li>
                <li><b>텍스트·시간</b>: 고정 문구 또는 곡 정보/재생 시간 토큰</li><li><b>앨범 커버·현재 곡·트랙 목록</b>: 재생 곡에 맞춰 자동 갱신</li>
                <li><b>진행 바·가사</b>: 곡 진행률과 타임코드 가사 표시</li><li><b>비주얼라이저·파형·레벨 미터·파티클</b>: 음악 반응형 표현</li></ul>
                <p>추가된 요소는 레이어 패널에서 이름, 표시 여부, 잠금과 앞뒤 순서를 관리합니다. 캔버스 요소를 우클릭하면 잘라내기·복제·삭제뿐 아니라 레이어 순서, 다중 정렬, 그룹, 표시와 잠금 명령을 사용할 수 있습니다.</p>"""),
            HelpTopic("project_content", "프로젝트 콘텐츠 관리", "프로젝트 콘텐츠 라이브러리 미디어 재사용 이미지 오디오 파일 누락",
                """<p><b>프로젝트 콘텐츠</b> 탭은 현재 프로젝트에서 사용하는 이미지와 오디오 같은 미디어를 모아 확인하고 다시 사용하는 공간입니다.</p>
                <ul><li>콘텐츠를 캔버스 요소나 플레이리스트로 추가할 수 있습니다.</li><li>항목을 우클릭하면 프로젝트에 추가, 목록에서 제거, 파일 정보 확인 메뉴가 표시됩니다. 빈 영역을 우클릭하면 새 콘텐츠를 가져올 수 있습니다.</li><li>같은 파일을 반복해서 찾지 않고 프로젝트 안에서 재사용할 수 있습니다.</li>
                <li>원본 파일을 이동하거나 삭제하면 프로젝트를 열 때 누락 미디어로 표시될 수 있습니다.</li></ul>
                <p>다른 컴퓨터로 프로젝트를 옮길 때는 미디어가 포함되는 <code>.pvsproj</code> 패키지 저장을 권장합니다.</p>"""),
            HelpTopic("projects", "프로젝트 저장과 불러오기", "저장 열기 pvsproj json 자동 저장 복구 종료 취소",
                """<h2>권장 형식</h2><p><code>.pvsproj</code>는 프로젝트 정보와 포함된 미디어를 하나의 파일로 묶는 휴대용 형식입니다. 레거시 <code>.project.json</code>도 열 수 있지만 프로젝트 메뉴에서 패키지로 업그레이드하는 것을 권장합니다.</p>
                <h2>저장되지 않은 변경 사항</h2><ul><li><code>Ctrl+S</code>로 저장합니다.</li><li>종료 시 <b>저장</b>, <b>저장 안 함</b>, <b>취소</b>를 선택할 수 있습니다.</li><li><b>취소</b>를 누르거나 저장창을 취소하면 편집기가 종료되지 않습니다.</li></ul>
                <h2>자동 복구</h2><p>작업 중 복구 사본이 주기적으로 기록됩니다. 비정상 종료 뒤 시작 화면에서 복구 가능한 작업이 감지되면 복원 여부를 선택할 수 있습니다.</p>"""),
            HelpTopic("canvas", "캔버스와 레이어 편집", "선택 이동 크기 회전 복제 정렬 그룹 잠금 스냅 우클릭 메뉴",
                """<h2>선택과 변형</h2><ul><li>요소를 클릭해 선택하고 드래그해 이동합니다.</li><li>테두리 핸들로 크기를 조절하고 위쪽 원형 핸들로 회전합니다.</li><li><code>Ctrl</code> 클릭 또는 드래그 선택으로 여러 요소를 선택합니다.</li><li><code>Delete</code>로 삭제하고 <code>Ctrl+D</code>로 복제합니다.</li></ul>
                <h2>정밀 편집</h2><ul><li><code>Alt+방향키</code>: 1px 이동</li><li><code>Alt+Shift+방향키</code>: 10px 이동</li><li><code>Ctrl+]</code> / <code>Ctrl+[</code>: 맨 앞으로 / 맨 뒤로</li><li><code>Ctrl+G</code> / <code>Ctrl+Shift+G</code>: 그룹 / 그룹 해제</li></ul>
                <p>캔버스 요소를 우클릭하면 클립보드, 레이어 순서, 캔버스 중앙 정렬, 여러 요소의 가장자리 정렬, 그룹, 표시와 잠금 명령을 사용할 수 있습니다. 빈 캔버스를 우클릭하면 붙여넣기, 모두 선택, 캔버스에 맞추기가 표시됩니다.</p>
                <p>레이어 패널에서는 표시 여부와 잠금을 바꾸고, 여러 요소를 선택해 한 단계 또는 맨 앞·뒤로 이동할 수 있습니다. 레이어를 드래그하면 실제 캔버스 쌓임 순서가 바뀌며 다른 그룹에 놓으면 그룹 소속도 함께 변경됩니다. 이름은 더블클릭하거나 <code>F2</code>로 수정합니다. 실행 취소와 다시 실행 후에도 복원된 요소의 선택 상태가 유지됩니다.</p>"""),
            HelpTopic("properties", "속성 편집", "속성 위치 크기 색상 글꼴 그림자 그라데이션 애니메이션",
                """<p>캔버스나 레이어 패널에서 요소를 선택하면 오른쪽 속성 패널에 해당 요소의 설정이 나타납니다.</p>
                <h2>주요 영역</h2><ul><li><b>콘텐츠</b>: 이름, 텍스트, 이미지 파일, 요소별 전용 옵션</li><li><b>변형</b>: X/Y 위치, 너비, 높이, 회전, 배율</li><li><b>모양</b>: 투명도, 색상, 테두리, 글꼴, 그림자, 그라데이션, 애니메이션</li></ul>
                <p>텍스트에는 <code>%title%</code>, <code>%artist%</code>, <code>%album%</code>, <code>%track_current_time%</code> 같은 동적 토큰을 사용할 수 있습니다. 여러 요소를 선택하면 모두에 적용할 수 있는 공통 속성만 표시됩니다. 값이 서로 다른 입력칸은 공백, 체크 항목은 혼합 상태로 나타나며 새 값을 지정하면 선택 요소 전체에 적용됩니다. 선택된 요소가 없으면 패널 중앙의 안내 문구가 표시됩니다.</p>"""),
            HelpTopic("playlist", "플레이리스트와 가사", "오디오 음악 곡 순서 제목 아티스트 앨범 가사 lyrics 자막",
                """<h2>곡 추가</h2><p>플레이리스트 패널에서 오디오 파일을 추가하고 곡 순서를 정합니다. 곡 상세 정보에서 제목, 아티스트, 앨범, 시작 시간과 가사를 편집할 수 있습니다.</p>
                <h2>가사 사용</h2><ul><li>곡에 LRC, SRT 또는 VTT 가사 파일을 연결합니다.</li><li>캔버스에 <b>가사</b> 요소를 추가합니다.</li><li>플레이리스트에서 곡을 선택하고 <b>곡/가사 설정</b>을 눌러 해당 곡만의 타이밍을 조절합니다.</li><li>가사 요소 속성의 타이밍 보정은 모든 곡에 공통으로 적용되며 곡별 보정과 합산됩니다.</li></ul>
                <div class='note'>파일 경로가 바뀌면 프로젝트를 열 때 누락 미디어 창에서 새 위치를 지정할 수 있습니다.</div>"""),
            HelpTopic("lyrics", "가사 표시와 곡별 동기화", "가사 자막 lrc srt vtt 하이라이트 타이밍 보정 첫 가사 현재 줄 소프트 포커스 스무스 슬라이드",
                """<h2>표시 규칙</h2><p>가사가 있는 곡은 첫 번째 줄을 곡 시작부터 보여줍니다. 단, 현재 타임코드에 들어가기 전에는 강조하지 않습니다. 가사 사이 공백에는 직전 줄을 유지하고 활성 구간에서만 현재 줄 강조와 전환 애니메이션이 적용됩니다.</p>
                <h2>가사 전환</h2><ul><li><b>소프트 포커스</b>: 부드러운 초점·페이드와 완만한 상승·확대</li><li><b>스무스 슬라이드</b>: 짧고 선명한 이동과 강조</li><li><b>블러 리빌</b>: 흐릿한 글자가 또렷해지는 시네마틱 전환</li></ul>
                <h2>곡/가사 설정</h2><ul><li>플레이리스트에서 곡을 선택하고 <b>곡/가사 설정</b>을 엽니다.</li>
                <li>LRC, SRT, VTT 파일을 연결하거나 해제합니다.</li><li>실제 노래를 재생하면서 현재·이전·다음 가사를 확인합니다.</li>
                <li>곡별 타이밍 보정의 양수 값은 가사를 빠르게, 음수 값은 늦게 표시합니다.</li></ul>
                <div class='note'>곡/가사 미리보기의 볼륨은 영상 전체 미리보기와 공유됩니다.</div>"""),
            HelpTopic("audio_visuals", "오디오 시각 효과", "비주얼라이저 waveform 파형 레벨 미터 particle 파티클 민감도 반응 속도",
                """<p>오디오 시각 요소는 실제 재생 음원을 분석해 움직입니다.</p><ul>
                <li><b>비주얼라이저</b>: 주파수 밴드를 막대나 선 형태로 표시</li><li><b>파형</b>: 시간에 따른 오디오 파형과 진행 상태 표시</li>
                <li><b>레벨 미터</b>: 모노/스테레오 음량과 피크 표시</li><li><b>파티클</b>: 음악에 반응하는 입자 효과</li></ul>
                <p>민감도, 어택/릴리스, 스무딩, 노이즈 게이트를 조절해 움직임을 다듬습니다. 요소 수, 해상도, FPS와 파티클 밀도가 높을수록 미리보기와 내보내기 시간이 늘어날 수 있습니다.</p>"""),
            HelpTopic("timeline", "타임라인과 애니메이션", "타임라인 시작 시간 지속 시간 등장 퇴장 전환",
                """<p>하단 <b>타임라인</b> 탭에서는 곡과 캔버스 요소의 표시 구간을 확인하고 편집합니다.</p>
                <ul><li><b>시작 시간</b>: 전체 영상에서 요소가 나타나는 시점</li><li><b>지속 시간</b>: 요소가 보이는 시간. 0이면 시작 시점부터 영상 끝까지 표시</li><li><b>등장/퇴장 애니메이션</b>: 페이드, 슬라이드, 줌 등을 속성 패널에서 설정</li></ul>
                <p>속성 패널의 <b>애니메이션 미리보기</b>를 누르면 선택 요소의 등장·종료 효과가 캔버스에서 직접 재생됩니다. 재생 중에는 편집이 잠기며 끝나면 위치와 선택 상태가 복원됩니다.</p>
                <p>플레이리스트의 활성 곡은 서로 겹치지 않도록 순서대로 배치됩니다. Now Playing과 트랙 목록 요소는 재생 중인 곡에 맞춰 자동으로 바뀝니다.</p>"""),
            HelpTopic("preview_export", "미리보기와 영상 내보내기", "미리보기 export 내보내기 mp4 해상도 fps codec crf",
                """<h2>미리보기</h2><p>내보내기 전에 미리보기에서 동적 텍스트, 가사, 진행 바, 오디오 반응형 요소와 곡 전환을 확인합니다.</p>
                <h2>MP4 내보내기</h2><ol><li>상단 <b>내보내기</b>를 누릅니다.</li><li>해상도, FPS, 비디오 인코더, CRF와 오디오 품질을 확인합니다.</li><li>미리보기 범위 또는 전체 영상을 렌더링합니다.</li></ol>
                <p>CRF는 값이 낮을수록 화질과 파일 크기가 커집니다. 일반적인 시작값은 Full HD, 30 FPS, H.264, CRF 18입니다. GPU 인코더는 호환 그래픽 드라이버와 FFmpeg 지원이 필요합니다.</p>"""),
            HelpTopic("full_preview", "영상 전체 미리보기 조작", "전체 미리보기 재생 일시정지 탐색 볼륨 품질 fps 단축키",
                """<p>상단 <b>미리보기</b>는 플레이리스트 전체를 실제 음원과 캔버스 화면으로 재생합니다.</p><ul>
                <li>재생/일시정지와 이전/다음 곡 버튼을 사용할 수 있습니다.</li><li>타임라인을 클릭하거나 드래그해 원하는 위치로 이동합니다.</li>
                <li>볼륨은 곡/가사 미리보기와 동일한 값으로 유지됩니다.</li><li>미리보기 품질을 낮추면 복잡한 비주얼라이저 장면을 더 부드럽게 확인할 수 있습니다.</li></ul>
                <p>표시되는 실제 FPS가 목표 FPS보다 계속 낮으면 장면 합성 또는 화면 표시가 현재 PC의 처리 속도보다 무거운 상태입니다. 미리보기 품질 변경은 최종 내보내기 품질에 영향을 주지 않습니다.</p>"""),
            HelpTopic("export_process", "내보내기 진행 단계와 취소", "내보내기 프레임 준비 렌더링 인코딩 진행률 취소 확인 잠금",
                """<h2>진행 단계</h2><ol><li><b>화면 프레임 준비</b>: 애니메이션, 가사, 곡 정보와 정적 레이어를 캡처합니다.</li>
                <li><b>오디오 준비</b>: 곡의 형식과 음량을 정리하고 순서대로 결합합니다.</li><li><b>비주얼라이저 준비</b>: 필요한 경우 Python 시각 효과 프레임을 생성합니다.</li>
                <li><b>영상 인코딩</b>: FFmpeg가 화면과 오디오를 최종 MP4로 합칩니다.</li></ol>
                <p>내보내기 중에는 프로젝트 상태가 바뀌지 않도록 메인 편집 화면이 잠깁니다. 진행 창의 <b>취소</b>를 누르고 확인하면 임시 프레임과 실행 중인 작업을 안전하게 정리한 뒤 편집 화면으로 돌아갑니다.</p>"""),
            HelpTopic("performance", "성능과 내보내기 시간 줄이기", "성능 최적화 느림 프레임 준비 비주얼라이저 렌더링 속도 메모리",
                """<h2>미리보기가 느릴 때</h2><ul><li>영상 전체 미리보기의 품질/FPS를 낮춥니다.</li><li>동시에 보이는 비주얼라이저, 파형, 파티클 수를 줄입니다.</li><li>파티클 밀도와 오디오 분석 밴드 수를 낮춥니다.</li></ul>
                <h2>내보내기가 오래 걸릴 때</h2><ul><li>필요하지 않다면 4K 또는 60 FPS 대신 Full HD 30 FPS를 사용합니다.</li>
                <li>가사 애니메이션과 매우 짧은 변화가 많으면 준비할 프레임 수가 증가합니다.</li><li>지원되는 GPU 인코더는 최종 인코딩을 줄일 수 있지만 화면 프레임 준비 시간까지 모두 줄이지는 않습니다.</li></ul>
                <div class='note'>진행 창의 단계명과 백분율로 시간이 화면 준비, 비주얼라이저, 인코딩 중 어디에서 사용되는지 먼저 확인하세요.</div>"""),
            HelpTopic("ffmpeg", "FFmpeg 설치와 확인", "ffmpeg 다운로드 설치 경로 인코더 오류",
                """<p>MP4 내보내기에는 FFmpeg가 필요합니다. FFmpeg는 프로그램에 기본 포함되지 않습니다.</p>
                <h2>자동 설치</h2><ol><li><b>도구 → 설정 → FFmpeg</b>를 엽니다.</li><li><b>FFmpeg 자동 다운로드 및 설치</b>를 누릅니다.</li><li>다운로드와 SHA-256 검증이 끝날 때까지 진행 창을 확인합니다.</li></ol>
                <p>이미 설치된 FFmpeg가 있다면 <code>ffmpeg.exe</code> 경로를 직접 선택하고 <b>확인</b>을 누를 수 있습니다. 다운로드 버튼이 동작하지 않으면 인터넷 연결, 방화벽, 로그 폴더의 오류 내용을 확인하세요.</p>"""),
            HelpTopic("presets_ai", "디자인 프리셋과 AI 빌더", "프리셋 AI 프로젝트 빌더 prompt 프롬프트 원샷 질문",
                """<h2>디자인 프리셋</h2><p>미리 준비된 레이아웃을 현재 캔버스에 적용합니다. 플레이리스트는 유지되며 캔버스 요소만 바뀝니다. 적용 직후 <code>Ctrl+Z</code>로 되돌릴 수 있습니다.</p>
                <h2>AI 프로젝트 빌더</h2><p>다른 AI가 Playlist Canvas 호환 프로젝트를 만들도록 지시하는 독립 실행형 프롬프트를 생성합니다. 앱 안에서 AI를 직접 실행하는 기능은 아닙니다.</p>
                <ul><li>프로젝트 요구사항을 입력하면 복사한 프롬프트 하나로 생성을 시작할 수 있습니다.</li><li><b>필요할 때만 질문</b>은 정보가 충분하면 바로 만들고, 결과를 크게 바꾸는 정보가 없을 때만 질문합니다.</li><li>출력 형식, 미디어 정책, 화면비, 스타일과 기능 범위를 설정할 수 있습니다.</li></ul>"""),
            HelpTopic("settings", "설정과 화면 동작", "설정 테마 언어 스크롤 부드러운 출력 폴더 렌더링",
                """<p><b>도구 → 설정</b>에서 애플리케이션 전체 기본값을 관리합니다.</p>
                <ul><li><b>일반</b>: 라이트/다크/자동 테마, 한국어/영어, 부드러운 스크롤</li><li><b>내보내기</b>: 기본 출력 폴더, 해상도, FPS, 인코더, 품질</li><li><b>FFmpeg</b>: 실행 파일 경로, 상태 확인, 자동 설치</li></ul>
                <p>부드러운 스크롤은 끄거나 80~420ms 범위에서 조절할 수 있습니다. 값이 높을수록 더 천천히 부드럽게 이동하며, <code>Ctrl+휠</code> 캔버스 확대/축소에는 영향을 주지 않습니다.</p>"""),
            HelpTopic("shortcuts", "자주 사용하는 단축키", "키보드 shortcut ctrl alt shift f1",
                """<ul><li><code>Ctrl+N / Ctrl+O / Ctrl+S</code>: 새 프로젝트 / 열기 / 저장</li><li><code>Ctrl+Z / Ctrl+Shift+Z</code>: 실행 취소 / 다시 실행</li><li><code>Ctrl+X / Ctrl+C / Ctrl+V</code>: 요소 잘라내기 / 복사 / 붙여넣기</li><li><code>Ctrl+D</code>: 선택 요소 복제</li><li><code>Delete</code>: 삭제</li><li><code>Esc</code>: 선택 해제</li><li><code>F</code>, <code>Home</code> 또는 <code>Ctrl+0</code>: 캔버스 맞춤</li><li><code>Ctrl+= / Ctrl+-</code>: 캔버스 확대 / 축소</li><li><code>Space+드래그</code>: 캔버스 이동</li><li><code>F1</code>: 이 도움말 열기</li></ul>
                <p>전체 목록은 <b>도움말 → 단축키 안내</b>에서 확인할 수 있습니다. 텍스트 입력 중에는 일반 편집 키가 우선합니다.</p>"""),
            HelpTopic("troubleshooting", "문제 해결", "오류 문제 로그 dll python 미디어 복구 내보내기",
                """<h2>프로그램이 실행되지 않을 때</h2><p>배포 폴더에서 EXE만 분리하지 마세요. <code>Playlist Canvas</code> 폴더와 내부 <code>_internal</code> 폴더를 함께 유지해야 합니다.</p>
                <h2>미디어를 찾을 수 없을 때</h2><p>프로젝트 열기 중 표시되는 누락 미디어 창에서 이동된 파일의 새 경로를 지정합니다. 다른 컴퓨터로 옮길 프로젝트는 미디어를 포함한 <code>.pvsproj</code> 형식을 권장합니다.</p>
                <h2>내보내기 오류</h2><p>설정의 FFmpeg 상태와 선택한 인코더를 확인하세요. GPU 인코더가 실패하면 CPU H.264(<code>libx264</code>)로 다시 시도합니다.</p>
                <h2>지원 정보</h2><p><b>도움말 → 프로그램 정보</b>에서 진단 정보를 복사하거나 로그 폴더를 열 수 있습니다. 오류 문의 시 진단 정보와 가장 최근 로그를 함께 제공하세요.</p>"""),
        ]

    @staticmethod
    def _english_topics() -> list[HelpTopic]:
        # English topics intentionally mirror the Korean guide by stable ID.
        return [
            HelpTopic("start", "Getting started", "new project aspect ratio workflow", """<p>Playlist Canvas combines a music playlist with visual sources to produce a video.</p><h2>Basic workflow</h2><ol><li>Create a project and choose its aspect ratio, or open a <code>.pvsproj</code> file.</li><li>Add audio to the playlist and arrange its order.</li><li>Place backgrounds, text, cover art, progress bars, and visualizers on the Canvas.</li><li>Check Preview, then export an MP4.</li></ol><div class='note'>The project Canvas ratio is fixed at creation. Export resolution changes output quality, not the authored Canvas ratio.</div>"""),
            HelpTopic("workspace", "Workspace and panels", "workspace ui panels sources content layers inspector playlist timeline", """<p>The editor is divided by task:</p><ul><li><b>Left</b>: Sources and Project Content</li><li><b>Center</b>: Canvas workspace</li><li><b>Right</b>: Layers and properties</li><li><b>Bottom</b>: Playlist and Timeline</li></ul><p>Use <b>View → Show panels</b> to collapse or restore editing panels, and drag panel dividers to resize them.</p><div class='note'>The gray workspace and grid are editing aids. Only the artboard is included in the final video.</div>"""),
            HelpTopic("sources", "Source types and insertion", "add source background image text shape cover logo watermark progress time", """<p>Add Canvas objects from the <b>Sources</b> tab or <b>Insert</b> menu. Hover a source button for its purpose and main settings.</p><ul><li><b>Background, Image, Logo, Watermark</b>: image-based composition</li><li><b>Text and Time</b>: fixed text or dynamic track/time tokens</li><li><b>Cover, Now Playing, Track List</b>: follow the active track</li><li><b>Progress and Lyrics</b>: playback progress and timed text</li><li><b>Visualizer, Waveform, Level Meter, Particles</b>: audio-reactive graphics</li></ul><p>Manage visibility, locking, names, and stacking in Layers.</p>"""),
            HelpTopic("project_content", "Managing Project Content", "project content library media reuse image audio missing files context menu", """<p>The <b>Project Content</b> tab collects media used by the current project so it can be reviewed and reused.</p><ul><li>Add content to the Canvas or Playlist.</li><li>Right-click an item to add it, remove it from the list, or inspect its file information. Right-click empty space to import content.</li><li>Reuse a file without browsing for it repeatedly.</li><li>Moved or deleted originals may appear as missing media when reopening.</li></ul><p>Use the embedded <code>.pvsproj</code> package when moving work to another computer.</p>"""),
            HelpTopic("projects", "Saving and opening projects", "save open pvsproj json autosave recovery cancel", """<p><code>.pvsproj</code> is the recommended portable package format. Legacy <code>.project.json</code> files can be opened and upgraded from the Project menu.</p><h2>Unsaved work</h2><ul><li>Press <code>Ctrl+S</code> to save.</li><li>On exit, choose Save, Discard, or Cancel.</li><li>Cancel or cancelling Save As keeps the editor open.</li></ul><h2>Recovery</h2><p>Recovery snapshots are written periodically and can be offered after an abnormal shutdown.</p>"""),
            HelpTopic("canvas", "Canvas and layer editing", "select move resize rotate duplicate align group lock snap context menu drag layer", """<p>Click a source to select it, drag to move it, use border handles to resize, and use the round top handle to rotate.</p><ul><li><code>Ctrl</code>-click or marquee: select multiple</li><li><code>Alt+Arrow</code>: nudge 1 px</li><li><code>Ctrl+D</code>: duplicate</li><li><code>Ctrl+]</code> / <code>Ctrl+[</code>: front / back</li><li><code>Ctrl+G</code> / <code>Ctrl+Shift+G</code>: group / ungroup</li></ul><p>Right-click a Canvas source for clipboard commands, layer ordering, Canvas centering, multi-source edge alignment, grouping, visibility, and locking. Right-click empty Canvas space to paste, select all, or fit the Canvas.</p><p>In Layers, drag rows to change the real Canvas stacking order or drop them into another group. Multi-selected rows move as one block. Use the four arrow buttons for front, forward, backward, and back, and double-click or press <code>F2</code> to rename a source or group.</p><p>Selection stays on a restored source after undo or redo.</p>"""),
            HelpTopic("properties", "Editing properties", "position size color font shadow gradient animation multi selection mixed", """<p>Select a source to show its settings in the Inspector.</p><ul><li><b>Content</b>: name, text, media, and source-specific options</li><li><b>Transform</b>: position, size, rotation, and scale</li><li><b>Appearance</b>: opacity, colors, fonts, shadows, gradients, and animation</li></ul><p>With multiple sources selected, the Inspector shows only properties supported by every selection. Different values appear blank, while check boxes use a mixed state; entering a value applies it to every selected source.</p><p>Text supports dynamic tokens including <code>%title%</code>, <code>%artist%</code>, and <code>%track_current_time%</code>.</p>"""),
            HelpTopic("playlist", "Playlist and lyrics", "audio tracks artist album lyrics subtitles timing offset", """<p>Add audio in the Playlist panel and arrange track order.</p><h2>Lyrics</h2><ol><li>Attach an LRC, SRT, or VTT file.</li><li>Add a Lyrics source to the Canvas.</li><li>Select one playlist track and open <b>Track/Lyrics Settings</b> to adjust only that track's timing.</li><li>The Lyrics source timing offset is global and is added to each track's individual offset.</li></ol><p>Use the missing-media dialog to relocate files whose paths have changed.</p>"""),
            HelpTopic("lyrics", "Lyrics display and per-track sync", "lyrics subtitles lrc srt vtt highlight timing first line preview volume soft focus smooth slide", """<p>When a track has lyrics, its first line is visible from playback start but is not highlighted before its cue. Between cues, the prior line remains visible; highlighting and transition animation apply only during an active cue.</p><h2>Transitions</h2><ul><li><b>Soft focus</b>: a soft fade, gentle lift, and subtle scale.</li><li><b>Smooth slide</b>: shorter, crisper movement and emphasis.</li><li><b>Blur reveal</b>: a cinematic focused reveal.</li></ul><h2>Track/Lyrics Settings</h2><ul><li>Attach or detach LRC, SRT, and VTT files.</li><li>Play the actual song while viewing previous, current, and next lyrics.</li><li>A positive per-track offset displays lyrics earlier; a negative value displays them later.</li></ul><div class='note'>Track/Lyrics Preview shares its volume with Full Preview.</div>"""),
            HelpTopic("audio_visuals", "Audio-reactive visuals", "visualizer waveform level meter particles sensitivity attack release", """<p>Audio-reactive sources analyze the actual playlist sound.</p><ul><li><b>Visualizer</b>: frequency bars or lines</li><li><b>Waveform</b>: waveform and playback progress</li><li><b>Level Meter</b>: mono/stereo level and peak</li><li><b>Particles</b>: music-reactive particle motion</li></ul><p>Tune sensitivity, attack/release, smoothing, and noise gate. More layers, higher FPS, and dense particles increase preview and export work.</p>"""),
            HelpTopic("timeline", "Timeline and animation", "timeline start duration entrance exit transition", """<p>The Timeline tab edits tracks and source visibility.</p><ul><li><b>Start</b>: when a source appears in the complete video</li><li><b>Duration</b>: how long it remains visible; zero means through the end</li><li><b>Entrance/exit</b>: fade, slide, zoom, and other Inspector animations</li></ul><p>Use <b>Preview animation</b> in the Inspector to play the selected source directly on the Canvas. Editing is locked during playback and the original state is restored afterward.</p><p>Now Playing and Track List sources update automatically with playback.</p>"""),
            HelpTopic("preview_export", "Preview and video export", "preview export mp4 resolution fps codec crf", """<p>Use Preview to check dynamic text, lyrics, progress, reactive sources, and transitions.</p><h2>Export</h2><ol><li>Click Export.</li><li>Review resolution, FPS, video encoder, CRF, and audio quality.</li><li>Render the preview range or complete video.</li></ol><p>A practical starting point is Full HD, 30 FPS, H.264, and CRF 18. Lower CRF means higher quality and larger files.</p>"""),
            HelpTopic("full_preview", "Controlling Full Preview", "full preview play pause seek volume quality fps shortcuts", """<p>Full Preview plays the complete playlist with real audio and Canvas visuals.</p><ul><li>Use play/pause and previous/next track controls.</li><li>Click or drag the timeline to seek.</li><li>Volume is shared with Track/Lyrics Preview.</li><li>Lower preview quality for smoother inspection of complex reactive scenes.</li></ul><p>Preview quality does not change final export quality. A sustained actual FPS below the target indicates that composition or display is heavier than the current machine can present in real time.</p>"""),
            HelpTopic("export_process", "Export stages and cancellation", "export frame preparation visualizer encoding progress cancel lock", """<ol><li><b>Visual frame preparation</b>: captures animation, lyrics, metadata, and static layers.</li><li><b>Audio preparation</b>: normalizes and combines tracks.</li><li><b>Visualizer preparation</b>: renders Python visual layers when needed.</li><li><b>Video encoding</b>: FFmpeg combines picture and audio into MP4.</li></ol><p>The main editor is locked during export to keep project state stable. Choose Cancel and confirm to stop safely, remove temporary work, and return to editing.</p>"""),
            HelpTopic("performance", "Performance and faster exports", "performance optimize slow frame preparation visualizer render memory", """<h2>Slow preview</h2><ul><li>Lower Full Preview quality/FPS.</li><li>Reduce simultaneous visualizers, waveforms, and particles.</li><li>Lower particle density and analysis complexity.</li></ul><h2>Long export</h2><ul><li>Prefer Full HD 30 FPS over 4K or 60 FPS when those are unnecessary.</li><li>Frequent lyric animations and short changes require more prepared frames.</li><li>A supported GPU encoder can shorten final encoding but does not eliminate visual-frame preparation.</li></ul><div class='note'>Use the progress stage and percentage to identify whether preparation, visualization, or encoding is taking the time.</div>"""),
            HelpTopic("ffmpeg", "Installing and checking FFmpeg", "ffmpeg download install path encoder error", """<p>FFmpeg is required for MP4 export and is not bundled by default.</p><ol><li>Open <b>Tools → Settings → FFmpeg</b>.</li><li>Choose <b>Download and install FFmpeg</b>.</li><li>Wait for download and SHA-256 verification.</li></ol><p>You may instead select an existing <code>ffmpeg.exe</code> and click Check. If downloading fails, review connectivity, firewall rules, and the application log.</p>"""),
            HelpTopic("presets_ai", "Design Presets and AI Builder", "preset ai project builder prompt one shot", """<p><b>Design Presets</b> replace Canvas sources while keeping the playlist. Use <code>Ctrl+Z</code> to undo.</p><p><b>AI Project Builder</b> creates a self-contained prompt for another AI to build a compatible project; it does not run an AI inside this application.</p><p>Enter a project brief for one-shot generation and configure question policy, output, media handling, aspect ratio, style, and feature scope.</p>"""),
            HelpTopic("settings", "Settings and interface behavior", "settings theme language scrolling smooth export", """<p><b>Tools → Settings</b> manages:</p><ul><li><b>General</b>: theme, language, and smooth scrolling</li><li><b>Export</b>: output folder and render defaults</li><li><b>FFmpeg</b>: executable, status, and managed installation</li></ul><p>Smooth scrolling can be disabled or adjusted from 80–420 ms. It does not intercept Canvas <code>Ctrl+wheel</code> zoom.</p>"""),
            HelpTopic("shortcuts", "Common keyboard shortcuts", "keyboard ctrl alt shift f1 copy cut paste zoom", """<ul><li><code>Ctrl+N / Ctrl+O / Ctrl+S</code>: new / open / save</li><li><code>Ctrl+Z / Ctrl+Shift+Z</code>: undo / redo</li><li><code>Ctrl+X / Ctrl+C / Ctrl+V</code>: cut / copy / paste sources</li><li><code>Ctrl+D</code>: duplicate</li><li><code>Delete</code>: delete</li><li><code>Esc</code>: clear selection</li><li><code>F</code>, <code>Home</code>, or <code>Ctrl+0</code>: fit Canvas</li><li><code>Ctrl+= / Ctrl+-</code>: zoom in / out</li><li><code>Space+drag</code>: pan</li><li><code>F1</code>: open this guide</li></ul><p>See <b>Help → Keyboard shortcuts</b> for the complete reference.</p>"""),
            HelpTopic("troubleshooting", "Troubleshooting", "error log dll python media recovery export", """<h2>Application does not start</h2><p>Do not separate the EXE from its distribution. Keep the complete folder, including <code>_internal</code>.</p><h2>Missing media</h2><p>Relocate moved files when prompted. Use an embedded <code>.pvsproj</code> when transferring projects.</p><h2>Export errors</h2><p>Check FFmpeg and the encoder. If a GPU encoder fails, try CPU H.264 (<code>libx264</code>).</p><h2>Support</h2><p>Use <b>Help → About</b> to copy diagnostics and open the log folder.</p>"""),
        ]
