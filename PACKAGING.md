# Playlist Canvas Windows 패키징 가이드

이 앱은 Windows 11 기준으로 PyInstaller의 **one-folder** 배포를 권장합니다. PySide6/Qt 플러그인을 안정적으로 포함하고, FFmpeg는 처음부터 번들에 넣지 않고 사용자가 앱 안에서 검증된 배포본을 내려받도록 구성되어 있습니다.

> 저장소 루트의 `ffmpeg` 폴더는 개발 및 로컬 렌더링 테스트용입니다. PyInstaller 배포본에는 포함되지 않습니다. 배포 폴더에 이 디렉터리를 수동으로 복사하지 마세요.

## 1. 빌드 환경 준비

프로젝트 루트에서 공식 배포 버전인 Python 3.12 가상 환경을 활성화한 뒤 빌드 의존성을 설치합니다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-lock.txt
```

## 2. Windows 배포본 빌드

```powershell
pyinstaller --noconfirm --clean playlist_canvas.spec
```

완성 파일은 다음 폴더에 생성됩니다.

```text
dist\Playlist Canvas\Playlist Canvas.exe
```

`dist\Playlist Canvas` 폴더 전체가 Inno Setup 설치 프로그램에 포함되어야 합니다. 실행 파일만 따로 복사하면 Qt 및 Python 런타임 파일이 없어 실행되지 않습니다.

> **주의:** `build\playlist_canvas\Playlist Canvas.exe`는 PyInstaller가 조립 중에 만드는 중간 파일입니다. 이 파일은 `_internal\python312.dll`이 함께 배치되지 않으므로 실행하거나 배포하면 안 됩니다. 반드시 `dist\Playlist Canvas` 폴더 안의 EXE를 사용하세요.

공식 배포 빌드는 `requirements-lock.txt`의 고정 버전을 사용합니다. 의존성을 갱신할 때는 로컬 테스트와 Windows CI가 모두 통과한 뒤 잠금 파일을 함께 갱신합니다.

## 3. Setup 설치 파일 빌드

Inno Setup 6을 설치한 뒤 프로젝트 루트에서 다음 명령을 실행합니다.

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup.iss
```

`setup.iss`는 `dist\Playlist Canvas` 폴더 전체를 설치 원본으로 사용합니다. 따라서 PyInstaller 빌드가 먼저 성공해야 합니다.

설치 마법사는 루트의 `LICENSE.txt`를 GNU GPL v3 동의 화면으로 표시하며, 설치된 프로그램 폴더에도 같은 원문을 복사합니다.

완성된 설치 파일은 다음 위치에 생성됩니다.

```text
output-setup\Playlist Canvas-1.0.0-setup.exe
```

이 Setup EXE를 GitHub Release에 첨부합니다. 사용자는 ZIP을 직접 관리하지 않고 설치 마법사를 통해 프로그램 위치, 시작 메뉴와 바탕 화면 바로가기를 설정할 수 있습니다.

## 4. 배포 전 확인

새 Windows 사용자 계정 또는 가상 머신에서 다음을 확인합니다.

1. `Playlist Canvas-1.0.0-setup.exe`로 설치와 제거가 정상 동작하는지 확인합니다.
2. 설치된 `Playlist Canvas.exe`가 실행되는지 확인합니다.
3. 설정 화면에서 언어·테마·출력 폴더를 저장하고 재시작 후 유지되는지 확인합니다.
4. 설정 화면의 FFmpeg 다운로드를 사용해 설치와 SHA-256 검증이 완료되는지 확인합니다.
5. MP3와 이미지 파일을 드래그 앤 드롭하여 MP4 내보내기가 정상 동작하는지 확인합니다.
6. `%LOCALAPPDATA%\PlaylistCanvas\logs\playlist-canvas.log`에 오류 로그가 생성되는지 확인합니다.

## 5. 배포 시 유의 사항

- 앱이 내려받는 FFmpeg Windows 빌드는 GPL 라이선스입니다. 배포 페이지와 앱 안내에서 FFmpeg 및 해당 배포본의 라이선스 정보를 함께 고지합니다.
- Playlist Canvas 바이너리를 배포할 때는 동일한 GPL v3 라이선스 아래의 대응 소스 코드도 GitHub Release와 같은 공개 위치에서 접근할 수 있어야 합니다.
- FFmpeg를 별도로 번들하기로 정책을 변경할 경우 `ffmpeg.exe`와 필요한 라이선스 파일만 선별하고, 배포본 크기와 해당 바이너리의 출처를 다시 검토합니다.
- 코드 서명을 적용하면 Windows SmartScreen 경고를 줄일 수 있습니다. 서명 인증서와 타임스탬프 서버는 배포 환경에서 관리합니다.
- FFmpeg 설치 폴더와 로그는 `%LOCALAPPDATA%\PlaylistCanvas` 아래에 있으므로 관리자 권한 없이 동작합니다. 이전 버전에서 저장한 설정과 복구본은 첫 실행 시 자동으로 이전됩니다.
