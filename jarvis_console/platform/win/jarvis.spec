# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包规格（融合版 JARVIS v1 · T14）。

产物形态选择：【onedir 单目录】（而非 onefile 单文件）
- 取舍理由：应用体积大（PySide6 + QWebEngine + sherpa-onnx + numpy），onefile 每次启动都要
  把 ~2GB 临时文件解压到 %TEMP% 再运行，启动慢且占临时盘；onedir 直接原地运行，启动快、便于
  把首启下载的模型落在安装目录内、也便于排查。代价是安装目录文件较多（由 Inno Setup 封装成
  单个 Setup.exe 解决）。
- 锁版本：依赖版本由 requirements.txt 锁定（sherpa-onnx==1.12.40、edge-tts==7.3.1 等），
  本规格只负责收集，不引入未锁定的新版本。

运行（在 Windows 上，仓库根目录）：
    pyinstaller jarvis_console/platform/win/jarvis.spec --noconfirm --clean
随后：iscc jarvis_console/platform/win/build.iss
"""
from PyInstaller.utils.hooks import collect_submodules

import os
from pathlib import Path

# PyInstaller 执行 spec 时会注入 SPECPATH（spec 所在目录）；不保证 __file__ 存在，故用 SPECPATH。
_SPECPATH = globals().get("SPECPATH")
if not _SPECPATH:
    _SPECPATH = os.path.dirname(os.path.abspath(globals().get("SPEC", "jarvis.spec")))
SPEC_DIR = Path(_SPECPATH).resolve()          # <repo>/jarvis_console/platform/win
APP_ROOT = SPEC_DIR.parents[1]                 # <repo>/jarvis_console
REPO_ROOT = APP_ROOT.parent                    # <repo>

# 入口使用绝对导入的启动器 entry.py；__main__.py 用相对导入 from .app，直接当脚本跑会失败。
ENTRY = SPEC_DIR / "entry.py"
APP_NAME = "Jarvis"

# Windows 版本资源（资源管理器「属性 → 详细信息」的版本/公司等，也是代码签名的元数据）。
# 由 tools/bump_version.py --write-winver 生成；缺失时退回无版本资源，不阻断构建。
_VERSION_INFO = SPEC_DIR / "version_info.txt"
VERSION_FILE = str(_VERSION_INFO) if _VERSION_INFO.is_file() else None

# —— 数据文件：HUD 静态资源 + 配置（运行时按 PACKAGE_ROOT 解析）——
datas = [
    (str(APP_ROOT / "hud"), "jarvis_console/hud"),
    (str(APP_ROOT / "config"), "jarvis_console/config"),
    # 首启下载向导随包附带（高级用户 / 无网络手动放置场景可用 python 直接运行）：
    (str(SPEC_DIR / "first_run.py"), "."),
    (str(SPEC_DIR / "download_models.py"), "."),
    (str(SPEC_DIR / "model_manifest.json"), "."),
]

# —— 隐藏导入：凡是运行时才 import 的模块都要显式声明，避免冻结后缺模块 ——
hiddenimports = [
    # PySide6（QWebEngine 承载 HUD，QtWebSockets 做桥，均被 hud.py 静态引用，保险起见列出）
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    # 语音/音频（多为函数内动态 import）
    "sherpa_onnx",
    "sounddevice",
    "webrtcvad",
    "resemblyzer",
    # TTS / 网络
    "edge_tts",
    "websockets",
    "requests",
    # 配置 / 解析
    "yaml",
    "trafilatura",
    "numpy",
]

# sherpa_onnx 含若干子模块，运行时按需加载，收集其全部子模块以防漏包。
try:
    hiddenimports.extend(collect_submodules("sherpa_onnx"))
except Exception:  # noqa: BLE001 - 构建机未装 sherpa 时跳过，不影响其它收集
    pass

excludes = [
    "pytest",
    "py",
    "__pycache__",
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(REPO_ROOT), str(APP_ROOT), str(REPO_ROOT / "pipeline")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 双击启动，无控制台窗口；日志走 UTF-8 文件（logs/）
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=VERSION_FILE,
    icon=None,  # 如需图标，放置 jarvis.ico 后改为 "jarvis.ico"
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

# 输出目录由命令行 --distpath/--workpath 指定（见 build.bat），默认即 <repo>/dist/Jarvis。
# （COLLECT 的 distpath/workpath 由 PyInstaller 构建框架决定，在 spec 里赋值无效，故不在此设置。）
