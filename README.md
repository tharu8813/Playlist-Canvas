<div align="center">
  <img src="docs/images/playlist-canvas-icon.png" width="128" alt="Playlist Canvas 아이콘">
  <h1>Playlist Canvas</h1>
  <p>음악, 가사, 비주얼 요소를 하나의 캔버스에서 편집해 플레이리스트 영상을 만드는 Windows 데스크톱 편집기</p>

  <p>
    <img src="https://img.shields.io/badge/version-1.0.2-1685D1" alt="Version 1.0.2">
    <img src="https://img.shields.io/badge/platform-Windows%2064--bit-0078D4" alt="Windows 64-bit">
    <img src="https://img.shields.io/badge/Python-3.12-3776AB" alt="Python 3.12">
    <img src="https://img.shields.io/badge/UI-PySide6-41CD52" alt="PySide6">
    <img src="https://img.shields.io/badge/license-Source--Available%20Noncommercial-blue" alt="Source-Available Noncommercial License">
    <img src="https://img.shields.io/badge/Built%20with-ChatGPT%20Codex-10A37F" alt="Built with ChatGPT Codex">
  </p>
</div>

![Playlist Canvas 편집기](docs/images/playlist-canvas-main.png)

## 소개

Playlist Canvas는 정적인 이미지와 음악을 합치는 수준을 넘어, 앨범 커버·가사·재생 진행률·오디오 비주얼라이저·파티클 등의 요소를 자유롭게 배치하고 MP4 영상으로 내보낼 수 있는 편집기입니다.

프로젝트는 미디어를 함께 포함할 수 있는 단일 `.pvsproj` 파일로 저장할 수 있어 다른 컴퓨터로 옮기거나 백업하기 쉽습니다.

## 제작 방식

**Playlist Canvas는 ChatGPT Codex만을 이용해 제작한 프로그램입니다.**

기능 설계와 요구사항을 바탕으로 한 소스코드 작성, UI 개선, 리팩터링, 오류 분석과 수정, 자동화 테스트, Windows 배포 빌드 점검 및 GitHub 문서화 전 과정을 ChatGPT Codex와의 대화를 통해 진행했습니다.

## 주요 기능

- 자유 배치 캔버스와 레이어 기반 편집
- 이미지, 텍스트, 도형, 배경, 로고, 워터마크 지원
- 앨범 커버, 현재 재생 정보, 트랙 목록과 재생 진행률 표시
- LRC, SRT, WebVTT 가사·자막 및 곡별 타이밍 조절
- 오디오 비주얼라이저, 파형, 레벨 미터와 파티클 효과
- 요소 시작·종료 애니메이션과 캔버스 미리보기
- 다중 선택, 그룹, 정렬, 복사·자르기·붙여넣기, 실행 취소·다시 실행
- 디자인 프리셋과 AI 프로젝트 빌더 프롬프트 생성
- 자동 저장, 비정상 종료 복구와 누락 미디어 재연결
- 한국어·영어 UI, 라이트·다크 테마와 부드러운 스크롤
- 검증된 외부 JSON 언어팩 가져오기와 사용자 제작 번역 패치
- H.264/H.265 및 지원되는 GPU 인코더를 이용한 MP4 내보내기
- FFmpeg 자동 다운로드, SHA-256 검증, 설치 및 즉시 적용
- GitHub Releases 기반 자동 업데이트 확인, 릴리즈 노트 표시와 검증된 Setup 실행

## 1.0.2 주요 변경 사항

이번 패치는 1.0.1 이후의 편집 편의성, 내보내기 성능과 배포 안정성을 다듬은 업데이트입니다.

- 곡이 많은 프로젝트도 UI를 멈추지 않고 저장하도록 백그라운드 저장을 추가했습니다.
- 프로젝트 콘텐츠를 목록·격자·간단히 보기로 전환하고 우클릭 메뉴를 사용할 수 있습니다.
- 이미 추가한 오디오의 제목·아티스트·앨범 등 메타데이터를 곡 설정에서 수정할 수 있습니다.
- 창 크기를 바꿀 때 좌우·하단 패널 크기를 유지하고 중앙 캔버스 영역만 반응하도록 개선했습니다.
- 캔버스 리사이즈 노브에 방향별 커서를 적용하고 낮은 확대 배율의 클릭 판정을 안정화했습니다.
- 초보자용 내보내기 추천 설정과 화질 프리셋을 추가하고, 선택한 설정을 진행 창에서 계속 확인할 수 있습니다.
- 내보내기 창을 최소화할 수 있으며 오디오 준비·결합·비주얼라이저·프레임·인코딩 진행 상황을 자세히 표시합니다.
- 남은 예상 시간은 진행 창의 전용 시간 영역에만 일관되게 표시합니다.
- 오디오 정규화와 비주얼라이저 레이어 처리를 제한적으로 병렬화하고 중복 오디오 인코딩을 제거했습니다.
- 자막·가사와 요소 애니메이션을 부드럽게 다듬고 미리보기와 결과물의 타이밍 일치를 개선했습니다.
- 파티클·노이즈·비주얼라이저가 레이어 순서를 무시하던 합성 문제를 수정했습니다.
- 손상 프로젝트, 긴 미디어 파일명, 저장 실패, 덮어쓰기 확인, 취소 처리 등 예외 상황을 보강했습니다.
- FFmpeg 자동 설치에 다운로드·압축 해제 크기 제한과 불완전 파일 검사를 추가했습니다.
- `.pvsproj` 파일을 탐색기에서 더블클릭해 열 수 있으며 프로젝트 전용 파일 아이콘을 제공합니다.

## 사용자 설치

1. GitHub의 **Releases** 페이지에서 최신 `Playlist Canvas-1.0.2-setup.exe`를 받습니다.
2. Setup 파일을 실행하고 설치 언어를 선택합니다.
3. 설치 위치와 바탕 화면 바로가기 생성 여부를 선택한 뒤 **설치**를 누릅니다.
4. 설치가 완료되면 **Playlist Canvas 실행**을 선택하거나 시작 메뉴의 바로가기를 실행합니다. 설치 후에는 파일 탐색기의 `.pvsproj` 프로젝트를 더블클릭해 프로그램과 프로젝트를 바로 열 수도 있습니다.
5. 처음 실행한 뒤 **도구 → 설정 → FFmpeg → FFmpeg 자동 다운로드 및 설치**를 선택합니다.
6. 새 프로젝트를 만들고 음악과 화면 요소를 추가한 뒤 MP4로 내보냅니다.

> 코드 서명되지 않은 개발 빌드에서는 Windows SmartScreen 안내가 표시될 수 있습니다. GitHub Releases의 공식 설치 파일인지 확인한 후 실행하세요.

## FFmpeg 자동 설치

FFmpeg는 애플리케이션 배포본에 포함되지 않습니다. 설정 화면에서 자동 설치를 실행하면 다음 절차가 백그라운드에서 진행됩니다.

1. BtbN FFmpeg Windows 64비트 GPL 배포본 확인
2. 다운로드 진행률과 현재 작업 표시
3. 공식 체크섬을 이용한 SHA-256 검증
4. 압축 해제 후 `ffmpeg -version` 실행 검증
5. `%LOCALAPPDATA%\PlaylistCanvas\tools\ffmpeg`에 사용자별 설치
6. 설치된 실행 파일 경로를 자동 저장하고 즉시 적용

설치 중에는 진행 창이 애플리케이션 모달로 표시되어 다른 편집 작업을 수행할 수 없습니다.

## 프로그램 업데이트

프로그램 시작 후 GitHub의 최신 안정 릴리즈를 백그라운드에서 확인합니다. 새 버전이 있으면 릴리즈 설명을 표시하며, 동의하면 공식 Setup 자산을 내려받아 GitHub가 제공하는 파일 크기와 SHA-256 digest를 검증한 뒤 실행합니다.

- 시작 알림을 닫으면 해당 릴리즈 버전은 자동으로 다시 표시하지 않습니다.
- 이후 새 릴리즈가 등록되면 다시 한 번 안내합니다.
- 건너뛴 버전도 **도움말 → 업데이트 확인**에서 수동으로 확인하고 설치할 수 있습니다.
- 업데이트 실행 전 저장되지 않은 프로젝트가 있으면 저장·버리기·취소를 선택할 수 있습니다.

## 외부 언어팩

기본 한국어·영어 외의 번역은 **도구 → 설정 → 일반 → 외부 언어팩**에서 UTF-8 JSON 파일로 가져올 수 있습니다. 언어팩은 사용자 데이터 폴더에 설치되며 프로그램을 다시 빌드하지 않고 가져오기·제거·새로고침할 수 있습니다.

- 누락된 번역은 영어로 표시됩니다.
- 실행 코드가 없는 JSON 데이터만 허용합니다.
- 잘못된 JSON, 호환되지 않는 스키마, 자리표시자 오류와 1MB 초과 파일은 거부합니다.
- 형식과 예제는 [`docs/language-packs`](docs/language-packs/README.md)에서 확인할 수 있습니다.

Windows에서는 첫 실행 시 `%LOCALAPPDATA%\PlaylistCanvas\languages\`에 기본
`ko.json`, `en.json`과 모든 번역 값이 비어 있는
`language-pack-template.json`을 자동 생성합니다. 빈 템플릿을 복사해 로캘
이름으로 변경한 다음 번역 값과 메타데이터를 작성하면 됩니다. 아직 작성하지
않은 빈 번역 값은 영어로 표시됩니다.

## 지원 형식

| 분류 | 형식 |
| --- | --- |
| 오디오 | MP3, WAV, FLAC, AAC, M4A, OGG |
| 이미지 | JPG, JPEG, PNG, WebP, SVG |
| 가사·자막 | LRC, SRT, VTT |
| 프로젝트 | PVSProj, Project JSON |
| 영상 출력 | MP4 |

## 소스에서 실행

필요 환경은 Windows 64비트와 Python 3.12입니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-lock.txt
python main.py
```

## 테스트

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m compileall -q main.py app tests
python -m unittest discover -s tests -v
```

Windows GitHub Actions에서도 같은 컴파일, 회귀 테스트, PyInstaller 빌드와 패키지 스모크 테스트를 실행합니다.

## Windows 배포본 빌드

먼저 PyInstaller로 설치 프로그램에 포함할 애플리케이션 폴더를 생성합니다.

```powershell
python -m PyInstaller --noconfirm --clean playlist_canvas.spec
```

그다음 Inno Setup 6으로 [setup.iss](setup.iss)를 컴파일합니다.

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup.iss
```

완성된 `output-setup\Playlist Canvas-1.0.2-setup.exe`를 GitHub Release에 첨부합니다. 자세한 배포 절차는 [PACKAGING.md](PACKAGING.md)를 참고하세요.

## 프로젝트 구조

```text
playlist_project/
├─ app/                    # 편집기 UI, 모델, 서비스와 렌더러
├─ app/resources/          # 앱·프로젝트 파일 아이콘과 기본 언어팩
├─ docs/images/            # README 이미지
├─ tests/                  # 회귀 및 배포 계약 테스트
├─ tools/                  # 개발 보조 도구
├─ main.py                 # 애플리케이션 진입점
├─ playlist_canvas.spec    # PyInstaller Windows 빌드 설정
├─ setup.iss               # Inno Setup 설치 프로그램 설정
└─ PACKAGING.md            # 배포 및 검수 가이드
```

## 로그 및 문제 해결

- 로그 폴더: `%LOCALAPPDATA%\PlaylistCanvas\logs`
- 앱이 실행되지 않으면 EXE와 `_internal` 폴더가 같은 배포 폴더에 있는지 확인합니다.
- GPU 인코더가 실패하면 설정에서 CPU H.264 (`libx264`)를 선택합니다.
- FFmpeg 다운로드가 실패하면 네트워크, 방화벽과 앱 로그를 확인합니다.
- 프로그램의 **도움말 → 프로그램 정보**에서 진단 정보를 복사할 수 있습니다.

## 라이선스 안내

Playlist Canvas는 [Playlist Canvas Source-Available Noncommercial Share-Alike License 1.0](LICENSE.txt)에 따라 배포됩니다. 이 라이선스는 OSI 승인 오픈소스 라이선스가 아닌 맞춤형 비상업용 소스 공개(source-available) 라이선스입니다.

- 프로그램으로 영상과 콘텐츠를 제작하는 용도는 개인·교육·업무·상업 목적 모두 허용합니다.
- 제작물의 판매·광고·후원·수익화·라이선스에는 제한이 없습니다. 단, 사용한 음악·이미지 등 입력 자료의 권리는 사용자가 별도로 확인해야 합니다.
- 프로그램과 수정본의 상업적 수정·판매·유료 배포·유료 접근 제공은 금지됩니다.
- 수정본을 배포할 때는 이 라이선스를 그대로 적용하고, 완전한 대응 소스를 무료로 함께 공개해야 합니다.
- 프로그램 또는 수정본을 배포할 때 저작권, Playlist Canvas 기반이라는 사실, 수정 내역과 [원본 GitHub 저장소](https://github.com/tharu8813/Playlist-Canvas)를 표시해야 합니다.
- 제작한 영상을 웹사이트·SNS·스트리밍 또는 동영상 플랫폼에 공개할 때 아래 크레딧을 표시하는 것을 권장하지만, 이는 선택 사항이며 라이선스 의무가 아닙니다.

```text
이 영상은 Playlist Canvas를 이용해 제작되었습니다.
https://github.com/tharu8813/Playlist-Canvas
```

영상 내부 워터마크, 설명 크레딧 또는 저장소 링크는 요구하지 않습니다. 별도의 상업적 소프트웨어 이용 허가는 저작권자에게 문의해야 합니다.

이 프로그램은 상품성 또는 특정 목적 적합성에 대한 묵시적 보증을 포함해 **어떠한 보증도 없이** 제공됩니다. 정확한 조건은 `LICENSE.txt` 전문을 확인하세요.

FFmpeg 자동 설치 기능이 내려받는 BtbN FFmpeg GPL 배포본과 PySide6 등 제3자 구성요소에는 각각의 라이선스 조건이 적용됩니다.
