; Inno Setup 6 安装脚本（融合版 JARVIS v1 · T14）
; 编译：在 Windows 上安装 Inno Setup 6，然后用其附带的 ISCC 命令行编译：
;     iscc jarvis_console\platform\win\build.iss
; 输出：仓库根\installer\Jarvis-Setup-2.0.0.exe
;
; 设计要点：
;  - PrivilegesRequired=lowest：不要求管理员权限（模型下载目录落在安装目录内，故安装目录
;    选在用户可写的 {localappdata}\Jarvis，避免 UAC）。
;  - 创建桌面快捷方式；开始菜单项；安装后可选运行 Jarvis.exe。
;  - 版本号不再写死在本文件：由 tools/bump_version.py 统一从 jarvis_console/__init__.py
;    的 __version__ 派生到同目录 version.iss（MyAppVersion / MyDisplayVersion / MyOutputBase）。
;    改版本只改一处：python tools/bump_version.py --bump patch --write-inno --write-winver
;  - 源目录指向 PyInstaller 的 onedir 产物 dist\Jarvis\*（PyInstaller 6 的 _internal 子目录一并打包）。

; version.iss 由脚本生成；缺失时 iscc 会直接报错，提示先运行 bump_version.py。
#include "version.iss"

#define MyAppName "小王老师 总控台 (Jarvis)"
#define MyAppPublisher "JARVIS Fusion Team"
#define MyAppExeName "Jarvis.exe"

[Setup]
; 注：本机未显式设 AppId 时会自动生成；如需覆盖安装一致性可固定一个 GUID。
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyDisplayVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Jarvis
DefaultGroupName=Jarvis
; 不要求管理员权限：安装到用户可写目录，模型后续下载也落在安装目录内。
PrivilegesRequired=lowest
; 允许同一用户重复安装/升级到同一目录。
DisableProgramGroupPage=no
OutputDir=..\..\..\installer
OutputBaseFilename={#MyOutputBase}
Compression=lzma2
SolidCompression=yes
; 64 位优先；PyInstaller 产物为当前 Python 位数。
ArchitecturesInstallIn64BitMode=x64
; 卸载时清理整个安装目录（含可能已下载的模型）。
UninstallFilesDir={app}\Uninstall

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"
; 若需英文：取消下一行注释并调整 Name。
; Name: "english"; MessagesFile: "compiler:English.isl"

[Files]
; PyInstaller onedir 产物（含 _internal 子目录与 Jarvis.exe）。
Source: "..\..\..\dist\Jarvis\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务"; Flags: unchecked

[Run]
; 安装完成后可选运行（用户可取消勾选）。
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时一并删除可能已下载的模型与缓存，避免残留。
Type: filesandordirs; Name: "{app}\jarvis_console\models"
Type: filesandordirs; Name: "{app}\_internal\jarvis_console\models"
