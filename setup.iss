; ============================================================================
; Playlist Canvas 1.0.2 Windows 설치 프로그램
; ChatGPT Codex를 이용해 제작한 Playlist Canvas의 Inno Setup 스크립트입니다.
; ============================================================================

#define MyAppName "Playlist Canvas"
#define MyAppVersion "1.0.2"
#define MyAppFileVersion "1.0.2.0"
#define MyAppPublisher "Ji Beak min(tharu8813)"
#define MyAppCopyright "© 2026 Ji Beak min(tharu8813). All rights reserved."
#define MyAppDescription "음악, 가사와 비주얼 요소를 편집해 플레이리스트 영상을 만드는 Windows 데스크톱 편집기"
#define MyAppComments "ChatGPT Codex만 이용해 제작한 프로그램입니다."
#define MyAppURL "https://github.com/tharu8813/Playlist-Canvas"
#define MyAppExeName "Playlist Canvas.exe"
; 최초 공개 설치본부터 이 GUID를 변경하지 않아야 이후 버전이 정상 업그레이드됩니다.
#define MyAppGUID "{{28b780ab-d9ec-420a-88d8-17d364505722}"

; 선택적 설정 (필요시 수정)
#define SourcePath "dist\Playlist Canvas"
#define SetupIconPath "app\resources\app_icon.ico"
#define ProjectIconPath "app\resources\project_file_icon.ico"
#define LicenseFilePath "LICENSE.txt"
#define ReadmeFilePath ""   ; Readme 파일 경로 (예: "readme.txt")

; ============================================================================
; 이하 코드는 수정하지 마세요
; ============================================================================

[Setup]
AppId={#MyAppGUID}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
#if MyAppURL != ""
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
#endif
AppCopyright={#MyAppCopyright}
AppComments={#MyAppComments}
DefaultDirName={commonpf}\{#MyAppName}
PrivilegesRequired=admin
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output-setup
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-setup
#if SetupIconPath != ""
SetupIconFile={#SetupIconPath}
#endif
#if LicenseFilePath != ""
LicenseFile={#LicenseFilePath}
#endif
#if ReadmeFilePath != ""
InfoBeforeFile={#ReadmeFilePath}
#endif
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
Uninstallable=yes
AlwaysRestart=no
DisableDirPage=no
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
VersionInfoVersion={#MyAppFileVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppDescription}
VersionInfoCopyright={#MyAppCopyright}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
ChangesAssociations=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourcePath}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pdb,*.log,*.tmp"
Source: "{#ProjectIconPath}"; DestDir: "{app}"; DestName: "project_file_icon.ico"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Windows 탐색기에서 .pvsproj를 더블클릭하면 설치된 앱으로 프로젝트를 엽니다.
Root: HKA; Subkey: "Software\Classes\.pvsproj"; ValueType: string; ValueName: ""; ValueData: "PlaylistCanvas.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\PlaylistCanvas.Project"; ValueType: string; ValueName: ""; ValueData: "Playlist Canvas Project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\PlaylistCanvas.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\project_file_icon.ico,0"
Root: HKA; Subkey: "Software\Classes\PlaylistCanvas.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; ValueType: string; ValueName: ".pvsproj"; ValueData: ""; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
{ 이전 버전 제거를 위한 함수들 }

function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  sUnInstPath := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1');
  sUnInstallString := '';
  if not RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function IsUpgrade(): Boolean;
begin
  Result := (GetUninstallString() <> '');
end;

function UnInstallOldVersion(): Integer;
var
  sUnInstallString: String;
  iResultCode: Integer;
begin
  { Return Values: }
  { 1 - uninstall string is empty }
  { 2 - error executing the UnInstallString }
  { 3 - successfully executed the UnInstallString }

  Result := 0;
  sUnInstallString := GetUninstallString();
  
  if sUnInstallString <> '' then begin
    sUnInstallString := RemoveQuotes(sUnInstallString);
    if Exec(sUnInstallString, '/SILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_HIDE, ewWaitUntilTerminated, iResultCode) then
      Result := 3
    else
      Result := 2;
  end else
    Result := 1;
end;

{ 실행 중인 프로세스 종료 }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  
  { 프로그램이 실행 중인 경우 종료 시도 }
  if CheckForMutexes('{#MyAppName}') then
  begin
    if MsgBox('설치를 계속하려면 {#MyAppName}을(를) 종료해야 합니다.' + #13#10 + '지금 종료하시겠습니까?', 
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Sleep(1000);
    end else
      Result := '설치가 취소되었습니다.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssInstall) then
  begin
    if (IsUpgrade()) then
    begin
      UnInstallOldVersion();
    end;
  end;
end;

{ 설치 완료 후 정보 }
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    { 필요시 추가 작업 }
  end;
end;
