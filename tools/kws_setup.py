#!/usr/bin/env python3
"""唤醒词「Hey Jarvis」一键配置：下载 → 解压 → 生成 keywords.txt → 加载自测。

把教程里「手点五步」压成一条命令：

    python tools/kws_setup.py

它会：
1. 检查 ``jarvis_console/models/wakeword/`` 是否已就绪（4 个模型文件 + keywords.txt）；
2. 缺失则下载官方预训练英文 KWS 模型（复用 ``download_models.py`` 的断点续传 + sha256 校验）；
   也可用 ``--archive`` 指定本地压缩包，完全离线；
3. 用 ``tokens.txt`` 的词表把唤醒词切成 token，写出 ``keywords.txt``；
4. 用与线上完全相同的加载路径（``speech/wake.py`` 的 ``WakeEngine``）真实加载一次，
   并可选录音/指定 wav 做命中自测。

常用：
    python tools/kws_setup.py                        # 一键：缺什么补什么 + 加载自测
    python tools/kws_setup.py --mirror               # 走 GitHub 加速镜像（国内网络）
    python tools/kws_setup.py --archive D:\\kws.tar.bz2   # 离线包
    python tools/kws_setup.py --test-wav me.wav      # 用现成 16k 单声道 wav 自测
    python tools/kws_setup.py --record 6             # 录 6 秒麦克风自测
    python tools/kws_setup.py --keyword "Hey Jarvis" --force

退出码：0 = 模型就绪（自测失败也返回 0，但会明确告警）；1 = 硬失败（下载/解压/分词失败）。
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
import time
import wave
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
WIN_DIR = REPO_ROOT / "jarvis_console" / "platform" / "win"
MODEL_FILES = ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")

# 官方 KWS 模型候选源（k2-fsa/sherpa-onnx，release tag: kws-models）。
# 顺序即优先级：先试 gigaspeech（英文，词表覆盖 "Hey Jarvis"），失败自动回退下一个。
# ⚠️ 这些 URL 已实测可用（2026-10-30）；若日后 404，用 --url 指定新地址。
MODEL_CANDIDATES: list[tuple[str, str]] = [
    (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
        "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2",
        "gigaspeech 英文 KWS 3.3M（推荐，词表覆盖英文唤醒词）",
    ),
    (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
        "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2",
        "wenetspeech 中文向 KWS 3.3M（中文场景备选，英文词覆盖较弱）",
    ),
    (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
        "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2",
        "中英混合 KWS 3M（2025-12 新版，中文口音英语识别更好）",
    ),
]
DEFAULT_URL = MODEL_CANDIDATES[0][0]
DEFAULT_MIRROR = "https://ghproxy.net/"  # GitHub 加速镜像；镜像可用性随时间变化，失败请去掉 --mirror


def info(msg: str) -> None:
    print(f"[信息] {msg}")


def warn(msg: str) -> None:
    print(f"[告警] {msg}")


def fail(msg: str) -> None:
    print(f"[错误] {msg}", file=sys.stderr)


# —— 路径与依赖 ——————————————————————————————————————————————


def default_wake_dir() -> Path:
    """默认模型目录取自 ``jarvis_console.paths.WAKE_MODEL``，避免路径各写一份。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from jarvis_console.paths import WAKE_MODEL

    return Path(WAKE_MODEL)


def load_module(name: str, path: Path) -> ModuleType:
    """按文件路径加载同级工具模块（tools/ 与 platform/win/ 都不是包）。"""
    if not path.is_file():
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# —— 模型文件检查 / 下载 / 解压 ————————————————————————————————


def missing_files(model_dir: Path) -> list[str]:
    """返回缺失的模型文件名列表（按 ``MODEL_FILES`` 顺序）。"""
    return [name for name in MODEL_FILES if not (model_dir / name).is_file()]


def _relocate_nested(model_dir: Path) -> list[str]:
    """解压后若 4 个文件仍散落在子目录里，把它们搬到模型目录根部。"""
    moved: list[str] = []
    for name in MODEL_FILES:
        if (model_dir / name).is_file():
            continue
        candidates = sorted(model_dir.rglob(name), key=lambda p: len(p.parts))
        if not candidates:
            continue
        source = candidates[0]
        target = model_dir / name
        try:
            shutil.move(str(source), str(target))
            moved.append(name)
        except OSError as exc:  # 跨盘/权限问题：退化为复制
            warn(f"移动 {source} 失败（{exc}），改为复制")
            shutil.copy2(source, target)
            moved.append(name)
    return moved


def _normalize_model_files(model_dir: Path) -> list[str]:
    """把官方发布的长命名 onnx 归一成 ``wake.py`` 期望的短名。

    复用 ``jarvis_console.speech.wake.normalize_model_dir``（与首启向导同一实现），
    它优先选 int8（体积小、CPU 推理更快）。导入失败时返回空列表并告警。
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from jarvis_console.speech.wake import normalize_model_dir
    except ImportError as exc:
        warn(f"无法导入 normalize_model_dir（{exc}），跳过文件名归一化。")
        return []
    return normalize_model_dir(model_dir)


def _progress(done: int, total: int, speed: float, package_id: str) -> None:
    pct = (done / total * 100) if total > 0 else 0.0
    mb_done = done / (1024 * 1024)
    mb_total = total / (1024 * 1024) if total > 0 else 0.0
    mbps = speed / (1024 * 1024)
    sys.stderr.write(f"\r  下载 {pct:5.1f}%  {mb_done:7.1f}/{mb_total:7.1f} MB  {mbps:5.2f} MB/s")
    sys.stderr.flush()


def download_and_extract(url: str, model_dir: Path, sha256: str | None) -> bool:
    """下载压缩包并铺平解压到 ``model_dir``；成功返回 True。"""
    try:
        downloader = load_module("jarvis_download_models", WIN_DIR / "download_models.py")
    except (FileNotFoundError, ImportError) as exc:
        fail(f"无法加载断点续传下载器：{exc}")
        return False

    cache_dir = model_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_path = cache_dir / Path(url.split("?")[0]).name
    info(f"模型源：{url}")
    info(f"缓存到：{archive_path}（中断可重跑续传）")
    try:
        actual = downloader.download_file(
            url,
            archive_path,
            expected_sha256=sha256,
            progress_cb=_progress,
            package_id="wakeword",
        )
    except downloader.CancelRequested:
        warn("下载已取消；已下载部分保留，可重跑本脚本续传。")
        return False
    except downloader.DownloadError as exc:
        print()
        fail(f"下载失败：{exc}")
        if getattr(exc, "manual_hint", ""):
            info("手动方案：\n" + exc.manual_hint)
        info("也可用 --archive <本地压缩包> 完全离线安装。")
        return False

    print()
    info(f"sha256 = {actual}" + ("（已按 --sha256 校验通过）" if sha256 else "（未提供 --sha256，仅记录）"))
    info("解压中…")
    try:
        downloader.extract_archive(archive_path, model_dir, flatten=True)
    except Exception as exc:  # noqa: BLE001 - 压缩包损坏/格式异常统一提示
        fail(f"解压失败：{exc}")
        return False
    try:
        archive_path.unlink()
    except OSError:
        pass
    return True


def extract_local(archive: Path, model_dir: Path) -> bool:
    """离线模式：直接解压本地压缩包。"""
    try:
        downloader = load_module("jarvis_download_models", WIN_DIR / "download_models.py")
        downloader.extract_archive(archive, model_dir, flatten=True)
        return True
    except Exception as exc:  # noqa: BLE001
        fail(f"解压本地包失败（{archive}）：{exc}")
        return False


# —— keywords.txt ————————————————————————————————————————————


def ensure_keywords(model_dir: Path, keyword: str, force: bool) -> bool:
    """生成 ``keywords.txt``（纯 token 序列）；已含当前唤醒词则复用。

    官方 KWS 包自带的 keywords.txt 是示例词（HELLO WORLD 等），不含当前唤醒词；
    旧版可能还带着 ``@显示文本`` 后缀（sherpa-onnx 1.12.40 不支持，会 exit 127）。
    这两种情况都先备份为 ``keywords.official.txt`` 再重建。分词失败时给出可执行的下一步。
    """
    keywords_path = model_dir / "keywords.txt"
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from jarvis_console.speech.kws_tokens import (
            build_keywords_line,
            invalid_tokens,
            read_tokens,
        )
    except ImportError as exc:
        fail(f"无法导入分词模块 jarvis_console.speech.kws_tokens：{exc}")
        return False

    tokens = read_tokens(model_dir / "tokens.txt")
    try:
        expected = build_keywords_line(keyword, tokens)
    except ValueError as exc:
        fail(str(exc))
        info("下一步：换词表更全的英文 KWS 模型，或按 docs/唤醒词自训教程.md 路线 B 自训。")
        return False

    if not force and keywords_path.is_file() and keywords_path.stat().st_size > 0:
        try:
            existing = [
                line.strip()
                for line in keywords_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip()
            ]
        except OSError:
            existing = []
        if expected in existing and not invalid_tokens(existing, tokens):
            info(f"keywords.txt 已包含「{keyword}」且 token 全部合法，复用：{expected}")
            return True
        reason = "不含当前唤醒词" if expected not in existing else "含非法 token（会导致加载时 exit 127）"
        backup = model_dir / "keywords.official.txt"
        if not backup.is_file():
            try:
                shutil.copy2(keywords_path, backup)
            except OSError:
                pass
        info(f"现有 keywords.txt {reason}，已备份为 keywords.official.txt，重建。")

    keywords_path.write_text(expected + "\n", encoding="utf-8")
    info(f"已生成 keywords.txt：{expected}")
    return True


# —— 自测 ——————————————————————————————————————————————————


def _read_wav(path: Path):
    """读 16k 单声道 wav 为 float32 numpy 数组；参数不符时抛 ValueError。"""
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if rate != 16000 or channels != 1:
        raise ValueError(f"需要 16kHz 单声道 wav，当前为 {rate}Hz / {channels} 声道")
    if width != 2:
        raise ValueError(f"需要 16bit PCM wav，当前位宽 {width * 8} bit")
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def _feed(engine, samples) -> tuple[bool, float]:
    """按 0.1 秒分块喂给引擎，命中即返回。

    块大小取 1600 采样（0.1s）：这是实测能稳定命中的节奏——整段一次性喂入不会触发命中。
    """
    chunk = 1600  # 0.1s @16k
    for start in range(0, len(samples), chunk):
        hit, score = engine.feed(samples[start : start + chunk])
        if hit:
            return True, score
    return False, 0.0


def self_test(model_dir: Path, keyword: str, wav: Path | None, record: float) -> bool:
    """用线上同一条加载路径验证模型可用；返回是否真正加载成功（缺库只告警，不算失败）。"""
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        warn("未安装 sherpa-onnx，跳过加载自测。安装命令：pip install \"sherpa-onnx==1.12.40\"")
        return False
    version = getattr(sherpa_onnx, "__version__", "未知")
    if version != "1.12.40":
        warn(f"sherpa-onnx 版本为 {version}，本项目锁定 1.12.40，行为可能不一致。")

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from jarvis_console.speech.wake import WakeEngine
    except Exception as exc:  # noqa: BLE001 - 依赖缺失/导入错误都只需提示
        warn(f"无法导入 WakeEngine（{exc}），跳过加载自测。")
        return False

    info("加载自测：走 speech/wake.py 的真实加载路径…")
    started = time.monotonic()
    try:
        engine = WakeEngine(model_dir=model_dir, keyword=keyword)
    except Exception as exc:  # noqa: BLE001 - 把加载失败原样呈现给用户
        fail(f"模型加载失败：{exc}")
        return False
    info(f"加载成功（{time.monotonic() - started:.2f}s），引擎已就绪。")

    samples = None
    if wav is not None:
        try:
            samples = _read_wav(wav)
            info(f"读取测试音频：{wav}（{len(samples) / 16000:.1f}s）")
        except (OSError, ValueError) as exc:
            fail(f"读取 wav 失败：{exc}")
            return False
    elif record > 0:
        try:
            import sounddevice as sd

            info(f"请说「{keyword}」——录 {record:.0f} 秒…")
            audio = sd.rec(int(record * 16000), samplerate=16000, channels=1, dtype="float32")
            sd.wait()
            samples = audio.reshape(-1)
        except Exception as exc:  # noqa: BLE001 - 无声卡/无库都只提示
            warn(f"录音失败（{exc}），跳过音频命中自测。可改用 --test-wav <wav>。")
            return True

    if samples is None:
        info("跳过音频自测。想验证唤醒效果：--record 6（录音）或 --test-wav <16k 单声道 wav>。")
        return True

    hit, score = _feed(engine, samples)
    if hit:
        print(f"[通过] 命中唤醒词，置信度 {score:.2f}。下次启动总控台，直接说「{keyword}」即可。")
    else:
        warn("未命中。排查顺序：①音频确为 16k 单声道；②说的是同一个唤醒词；"
             "③调低 keywords_threshold（wake.py，默认 0.35）；④换词表更全的模型或自训。")
    return True


# —— 主流程 ————————————————————————————————————————————————


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="唤醒词「Hey Jarvis」一键配置 + 自测")
    parser.add_argument("--models-dir", default="", help="模型目录（默认取 paths.WAKE_MODEL）")
    parser.add_argument("--keyword", default="Hey Jarvis", help="唤醒词，默认 'Hey Jarvis'")
    parser.add_argument("--url", default=DEFAULT_URL, help="KWS 模型压缩包地址")
    parser.add_argument("--mirror", action="store_true", help=f"走 GitHub 加速镜像（{DEFAULT_MIRROR}）")
    parser.add_argument("--archive", default="", help="本地压缩包（离线安装，跳过下载）")
    parser.add_argument("--sha256", default="", help="可选：压缩包 sha256，提供则严格校验")
    parser.add_argument("--force", action="store_true", help="忽略已有文件，强制重新下载/解压/生成")
    parser.add_argument("--skip-download", action="store_true", help="只做检查/分词/自测，不下载")
    parser.add_argument("--test-wav", default="", help="用指定 16k 单声道 wav 做命中自测")
    parser.add_argument("--record", type=float, default=0.0, metavar="SEC", help="录 N 秒麦克风做命中自测")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    model_dir = Path(args.models_dir).expanduser().resolve() if args.models_dir else default_wake_dir()
    model_dir.mkdir(parents=True, exist_ok=True)
    info(f"模型目录：{model_dir}")
    info(f"唤醒词：{args.keyword}")

    # 1) 下载 / 解压（按需；主源失效时自动回退候选源）
    need = missing_files(model_dir)
    if need and not args.skip_download:
        info(f"缺少模型文件：{', '.join(need)}")
        if args.archive:
            archive = Path(args.archive).expanduser().resolve()
            if not archive.is_file():
                fail(f"本地压缩包不存在：{archive}")
                return 1
            info(f"离线安装：{archive}")
            if not extract_local(archive, model_dir):
                return 1
        elif args.url != DEFAULT_URL:
            # 用户显式指定 URL：只试这一个，不走候选回退。
            url = (DEFAULT_MIRROR + args.url) if args.mirror else args.url
            if not download_and_extract(url, model_dir, args.sha256 or None):
                return 1
        else:
            candidates = list(MODEL_CANDIDATES)
            downloaded = False
            for index, (base_url, label) in enumerate(candidates):
                url = (DEFAULT_MIRROR + base_url) if args.mirror else base_url
                info(f"尝试模型源 {index + 1}/{len(candidates)}：{label}")
                if download_and_extract(url, model_dir, args.sha256 or None):
                    downloaded = True
                    break
                if not missing_files(model_dir):
                    # 上一次中断续传已完成全部文件，视为成功。
                    downloaded = True
                    break
                warn("该源失败，尝试下一个候选源…")
            if not downloaded:
                fail("所有候选模型源均失败。")
                info("可手动下载后重跑（支持断点续传）：")
                for base_url, label in candidates:
                    print(f"  - {label}\n    {base_url}")
                info("或用 --archive <本地压缩包> 完全离线安装；也可用 --url 指定其它地址。")
                return 1
    elif need and args.skip_download:
        warn(f"缺少模型文件（{', '.join(need)}），已按 --skip-download 跳过下载。")

    moved = _relocate_nested(model_dir)
    if moved:
        info(f"已归位到模型目录根部：{', '.join(moved)}")

    renamed = _normalize_model_files(model_dir)
    if renamed:
        info("已按 wake.py 约定重命名模型文件（优先 int8）：")
        for item in renamed:
            print(f"    {item}")

    still_missing = missing_files(model_dir)
    if still_missing:
        fail(f"模型仍不完整，缺少：{', '.join(still_missing)}")
        info("请检查压缩包内容，或用 --archive 指定正确的 KWS 模型包。")
        return 1

    # 2) keywords.txt
    if not ensure_keywords(model_dir, args.keyword, force=args.force):
        return 1

    # 3) 自测
    load_ok = self_test(
        model_dir,
        args.keyword,
        Path(args.test_wav).expanduser().resolve() if args.test_wav else None,
        args.record,
    )

    # 4) 收尾提示
    print()
    if load_ok:
        info("模型已就绪。下一步：")
        print("  1) 启动总控台：.venv\\Scripts\\python.exe -m jarvis_console")
        print(f"  2) 对着麦克风说：「{args.keyword}」")
        print("  3) 唤醒不灵敏/误唤醒多，见 docs/唤醒词自训教程.md 第 5 节调参")
        return 0
    info("模型文件已就位，但加载自测未通过（详见上方告警/错误）。")
    print("  常见原因：sherpa-onnx 未安装、模型文件损坏、或模型与 sherpa-onnx 版本不匹配。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
