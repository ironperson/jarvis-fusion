@echo off
chcp 65001 >nul
REM ============================================================
REM  融合版 JARVIS · Windows 一键构建脚本
REM  前置：① 已在本仓库 .venv 装好依赖（PySide6 / PyInstaller 等）
REM        ② 已安装 Inno Setup 6（ISCC 在 PATH，或改下面的 ISCC 路径）
REM  用法：双击本文件，或在 cmd 里执行 build.bat
REM        改版本号：python tools\bump_version.py --bump patch
REM ============================================================
setlocal
cd /d "%~dp0..\..\.."

echo [0/3] 同步版本号（源码 -> version.iss / version_info.txt）...
python tools\bump_version.py --write-inno --write-winver
if errorlevel 1 goto :fail
for /f "delims=" %%v in ('python tools\bump_version.py --show') do set "VER=%%v"
echo       当前版本：%VER%

echo [1/3] PyInstaller 打包（onedir）...
python -m PyInstaller jarvis_console\platform\win\jarvis.spec --noconfirm --clean --distpath dist --workpath .build
if errorlevel 1 goto :fail

echo [2/3] Inno Setup 编译安装包...
REM 如果 iscc 不在 PATH，请把下面替换为完整路径，例如：
REM   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" jarvis_console\platform\win\build.iss
iscc jarvis_console\platform\win\build.iss
if errorlevel 1 goto :fail

echo [3/3] （可选）本地签名，示例如下（需证书 .pfx）：
echo       signtool sign /fd sha256 /td sha256 /tr http://timestamp.digicert.com /f "证书.pfx" /p "密码" installer\Jarvis-Setup-%VER%.exe

echo.
echo 构建完成：
echo   程序目录：dist\Jarvis\Jarvis.exe
echo   安装包  ：installer\Jarvis-Setup-%VER%.exe
goto :eof

:fail
echo.
echo 构建失败，请检查上方错误信息。
exit /b 1
