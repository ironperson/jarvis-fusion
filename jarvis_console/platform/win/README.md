# 融合版 JARVIS v1 · Windows 打包说明（T14）

本目录提供把 `jarvis_console` 打包成 Windows 双击安装包（`.exe`）的脚本。

## 产物
- `dist\Jarvis\Jarvis.exe` —— 免安装的程序目录（onedir）。
- `installer\Jarvis-Setup-<版本>.exe` —— Inno Setup 安装包（不要求管理员权限，安装到 `%LOCALAPPDATA%\Jarvis`）。

## 版本号：一处维护，自动派生

版本号唯一事实源是 `jarvis_console/__init__.py` 的 `__version__`，构建前由 `tools\bump_version.py`
同步派生本目录的两个文件，**不要在 `build.iss` 里手改版本**：

- `version.iss` —— Inno Setup 包含文件（`MyAppVersion` / `MyDisplayVersion` / `MyOutputBase`）
- `version_info.txt` —— PyInstaller 版本资源（资源管理器「属性 → 详细信息」里的版本/公司）

```bat
REM 修订号 +1（同时更新源码与两个生成物）
python tools\bump_version.py --bump patch
REM 指定版本
python tools\bump_version.py --set 2.1.0
REM 只同步生成物（不改版本）
python tools\bump_version.py --write-inno --write-winver
```

`build.bat` 已在构建前自动执行 `--write-inno --write-winver`，并按当前版本打印安装包名。

## 前置
1. 仓库根已建好 `.venv` 并装好依赖（含 `PySide6`、`pyinstaller`；语音相关 `sherpa-onnx==1.12.40`、`edge-tts==7.3.1` 等按 `jarvis_console/requirements.txt` 锁版安装）。
2. 安装 **Inno Setup 6**：<https://jrsoftware.org/isdl.php>。安装后 `ISCC.exe` 通常在
   `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`。

## 构建步骤
在仓库根（`D:\workbuddy worktool\贾维斯`）执行：

```bat
REM 0) 同步版本号（版本只在 jarvis_console/__init__.py 维护）
python tools\bump_version.py --write-inno --write-winver

REM 1) 打包程序（onedir）
python -m PyInstaller jarvis_console\platform\win\jarvis.spec --noconfirm --clean --distpath dist --workpath .build

REM 2) 编译安装包
iscc jarvis_console\platform\win\build.iss
```

或直接双击 `build.bat`（已包含上述三步）。

## 代码签名（可选但强烈建议）
未签名的安装包首次运行会被 Windows SmartScreen 拦截（"已保护你的电脑"）。两种签名方式：

- **本地签名**（需证书 `.pfx`）：

```bat
signtool sign /fd sha256 /td sha256 /tr http://timestamp.digicert.com /f "证书.pfx" /p "密码" installer\Jarvis-Setup-<版本>.exe
```

- **CI 签名**：把 `.pfx` 的 base64 存为仓库 Secret `WINDOWS_CERT_PFX_BASE64`、密码存为
  `WINDOWS_CERT_PASSWORD`，工作流会自动对 `Jarvis.exe` 与安装包签名并校验（未配置则跳过，不阻断出包）。
  详见 `docs/CI-自动出包说明.md`。

## 首次启动的模型下载
程序首次启动会检测 `jarvis_console\models\` 下的语音模型，缺失时由 `first_run.py` 引导下载
（`download_models.py` 支持断点续传 + sha256 校验；清单见 `model_manifest.json`）。

- **唤醒词一键配置**：`tools\setup_wakeword.bat`（或 `python tools\kws_setup.py`）自动下载 KWS 模型、
  解压、生成 `keywords.txt` 并做加载自测；完整教程见 `docs/唤醒词自训教程.md`。
- SenseVoice ASR、Piper 英式嗓音在清单中给了稳定公开源 URL，**但 `sha256` 为 `__VERIFY__` 占位，发布前请核对**。
- 无网络：按 `first_run.py` 打印的手动说明把模型放进对应目录即可。

## 已知限制
- `build.iss` 的 UI 语言当前借用 Inno 自带 `Default.isl`（英文界面），如需简体中文可另配第三方中文语言文件。
