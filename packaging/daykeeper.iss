; Daykeeper's installer for Windows (Inno Setup 6.3 or later). Per user, no administrator
; rights, into %LOCALAPPDATA%\Programs\Daykeeper; a Start-menu entry, an optional desktop
; icon, an optional start with the system, an uninstall entry under HKCU. The data folder
; (%APPDATA%\Daykeeper: the vault, its copies, the settings) is never touched, by the
; setup or by the uninstaller.
;
; Built by CI (.github/workflows/build.yml) from the onedir build, from the repository root:
;   iscc /DAppVersion=2.1.0 /DAppNumericVersion=2.1.0 /DSourceDir=..\dist\Daykeeper /Odist packaging\daykeeper.iss
; -> dist\Daykeeper-<version>-setup.exe. Paths below are relative to this file.
;
; The app's update (daykeeper/update.py) runs this setup with
;   /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /LAUNCH=1
; after the user confirmed it and once the app has quit (a detached waiter starts it): it
; installs over the copy (closing one that still runs, by force if it must) and LAUNCH=1
; starts the new one at the end. The app also looks for the uninstall entry of this AppId
; under HKCU to tell an installed copy from a portable folder: keep the AppId.

#ifndef AppVersion
  #error Pass the version: iscc /DAppVersion=2.1.0 ...
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion AppVersion
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Daykeeper"
#endif

[Setup]
; one id for every version: a newer setup upgrades the older install in place
AppId={{BEE0D65A-A989-4ED5-B3C9-34F1DC2BDC81}
AppName=Daykeeper
AppVersion={#AppVersion}
AppVerName=Daykeeper {#AppVersion}
AppPublisher=Daykeeper
AppPublisherURL=https://github.com/a73chadin/daykeeper
AppSupportURL=https://github.com/a73chadin/daykeeper/issues
AppUpdatesURL=https://github.com/a73chadin/daykeeper/releases
VersionInfoVersion={#AppNumericVersion}
VersionInfoProductVersion={#AppNumericVersion}
VersionInfoDescription=Daykeeper setup
; per user: no UAC prompt, the uninstall entry goes to HKCU
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Daykeeper
UsePreviousAppDir=yes
DisableDirPage=auto
DisableProgramGroupPage=yes
DisableReadyPage=no
OutputBaseFilename=Daykeeper-{#AppVersion}-setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Daykeeper.exe
UninstallDisplayName=Daykeeper
WizardStyle=modern
ShowLanguageDialog=auto
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 version 1809, the oldest Qt 6.12 runs on
MinVersion=10.0.17763
; a running Daykeeper from this folder: the setup offers to close it (Restart Manager), and
; does it by itself under /CLOSEAPPLICATIONS; it is started again by [Run], not by the
; Restart Manager (which would start a second copy)
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
; a log in %TEMP% ("Setup Log <date> #001.txt"): what an update did, when it did not come back
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
english.AutoStart=Start Daykeeper when I sign in (minimized to the taskbar)
russian.AutoStart=Запускать Daykeeper при входе в систему (свёрнутым на панель задач)
english.StartGroup=Start:
russian.StartGroup=Запуск:
english.AppRunning=Daykeeper is running from this folder. Close it, then press OK to go on, or Cancel to stop.
russian.AppRunning=Daykeeper запущен из этой папки. Закройте его и нажмите OK, чтобы продолжить, или «Отмена», чтобы прервать.

[Messages]
english.UninstalledAll=%1 was removed from your computer.%n%nYour data was not touched: the vault, its copies and the settings stay in the Daykeeper folder under AppData\Roaming. Delete that folder by hand only when you no longer need them.
russian.UninstalledAll=%1 удалён с компьютера.%n%nВаши данные не тронуты: хранилище, его копии и настройки остаются в папке Daykeeper внутри AppData\Roaming. Удаляйте её вручную, только если они больше не нужны.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "{cm:AutoStart}"; GroupDescription: "{cm:StartGroup}"; Flags: unchecked

[InstallDelete]
; the onedir build's libraries are replaced as a whole: a file an older version had and
; this one does not must not stay behind and be loaded
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Daykeeper"; Filename: "{app}\Daykeeper.exe"
Name: "{autodesktop}\Daykeeper"; Filename: "{app}\Daykeeper.exe"; Tasks: desktopicon

[Registry]
; the same per-user Run value the app writes itself (Settings → General → Start); an
; update leaves it as the app set it (the user may have changed or removed it there)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Daykeeper"; ValueData: """{app}\Daykeeper.exe"" --minimized"; Tasks: autostart; Check: not LaunchAfterUpdate

[Run]
Filename: "{app}\Daykeeper.exe"; Description: "{cm:LaunchProgram,Daykeeper}"; Flags: nowait postinstall skipifsilent
; the app's own update: start the new version once it is in place
Filename: "{app}\Daykeeper.exe"; Flags: nowait; Check: LaunchAfterUpdate

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

function LaunchAfterUpdate: Boolean;
begin
  Result := ExpandConstant('{param:LAUNCH|0}') = '1';
end;

{ Daykeeper.exe of this folder is running (WMI; False when that cannot be asked) }
function AppRunning(const Exe: String): Boolean;
var
  Locator, Service, Items, Item: Variant;
  I: Integer;
begin
  Result := False;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Items := Service.ExecQuery('SELECT ExecutablePath FROM Win32_Process WHERE Name = ''Daykeeper.exe''');
    for I := 0 to Items.Count - 1 do
    begin
      Item := Items.ItemIndex(I);
      if not VarIsNull(Item.ExecutablePath) then
        if CompareText(Item.ExecutablePath, Exe) = 0 then
          Result := True;
    end;
  except
    Result := False;
  end;
end;

{ the uninstaller: a running copy is closed by the user first, or its files would stay }
function InitializeUninstall(): Boolean;
var
  Exe: String;
begin
  Result := True;
  Exe := ExpandConstant('{app}\Daykeeper.exe');
  while (not UninstallSilent) and AppRunning(Exe) do
  begin
    if MsgBox(CustomMessage('AppRunning'), mbError, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

{ the start with the system goes with the app, but only when it starts this copy: another
  copy of Daykeeper (a portable folder) may own the entry }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value: String;
begin
  if CurUninstallStep = usPostUninstall then
    if RegQueryStringValue(HKCU, RunKey, 'Daykeeper', Value) then
      if Pos(Lowercase(ExpandConstant('{app}\Daykeeper.exe')), Lowercase(Value)) > 0 then
        RegDeleteValue(HKCU, RunKey, 'Daykeeper');
end;
