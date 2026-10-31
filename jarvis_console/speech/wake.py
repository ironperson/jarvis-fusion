"""离线唤醒词检测（sherpa-onnx 关键词 spotting，KWS）。

“Hey Jarvis” 离线唤醒，全程本地推理、无需密钥。严格遵守：
- sherpa-onnx 锁 1.12.40；
- 中文路径加载纪律（chdir → 纯文件名 → finally 恢复），复用 ``load_model_safely``。

缺库/缺模型时优雅降级：``wake_status()`` 返回 ``enabled=False``；AudioService 据此跳过唤醒线程。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np

from ..core.errors import ConfigError, DependencyMissing
from ..logging_setup import get_logger
from ..paths import WAKE_MODEL
from . import load_model_safely
from .kws_tokens import build_keywords_line, invalid_tokens, read_tokens

log = get_logger(__name__)

SAMPLE_RATE = 16000
DEFAULT_KEYWORD = "Hey Jarvis"
DEFAULT_THRESHOLD = 0.25
# KWS 模型标准文件布局（sherpa-onnx kws 模型）。
_KWS_FILES = ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")
# 官方 KWS 包里 onnx 的长命名主干（本模块按 encoder.onnx 这类短名加载）。
ONNX_STEMS = ("encoder", "decoder", "joiner")


def normalize_model_dir(directory: Path) -> list[str]:
    """把官方 KWS 包的长命名 onnx 归一成 ``encoder/decoder/joiner.onnx``（优先 int8）。

    官方发布包里的文件名形如 ``encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx``，
    与本模块的加载约定不一致。这里做一次改名，让解压后的目录直接可用；
    同名短文件已存在时跳过。返回改名描述列表（空列表 = 无需改动）。

    首启向导（``download_models.py`` 的 ``post_extract`` 钩子）与
    ``tools/kws_setup.py`` 都复用本函数，避免两套改名逻辑漂移。
    """
    directory = Path(directory)
    renamed: list[str] = []
    for stem in ONNX_STEMS:
        target = directory / f"{stem}.onnx"
        if target.is_file():
            continue
        candidates = sorted(
            directory.glob(f"{stem}-*.onnx"),
            key=lambda p: (0 if p.name.endswith(".int8.onnx") else 1, len(p.name)),
        )
        if not candidates:
            continue
        source = candidates[0]
        try:
            shutil.move(str(source), str(target))
        except OSError:  # 跨盘/权限问题：退化为复制
            shutil.copy2(source, target)
        renamed.append(f"{source.name} -> {target.name}")
    return renamed


def _require_kws_files(model_dir: Path) -> None:
    missing = [name for name in _KWS_FILES if not (model_dir / name).is_file()]
    if missing:
        raise ConfigError(f"唤醒模型不完整，缺少：{', '.join(missing)}（目录：{model_dir}）")


def _ensure_keywords_file(directory: Path, keyword: str) -> str:
    """确保模型目录存在 keywords.txt（sherpa-onnx 1.12.40 KWS 必需），且包含当前唤醒词。

    格式铁律：**只写 token 序列**（如 ``▁HE Y ▁JA R VI S``）。sherpa-onnx 1.12.40 会把
    整行按空格拆开、逐个查 ``tokens.txt``，因此**不能加 ``@显示文本`` 后缀**——
    ``@Hey Jarvis`` 里的 "Jarvis" 会报 ``Cannot find ID for token Jarvis`` 并令进程退出
    （exit 127）。官方 KWS 包自带的 keywords.txt 同样是纯 token 行。

    分词逻辑统一走 :mod:`jarvis_console.speech.kws_tokens`（按词切分、词首带词界
    token、词内取 token 数最少，与 ``tools/kws_make_keywords.py`` 同一实现）。

    复用条件：已存在的文件里**含有期望行、且所有 token 都合法**。否则（官方示例词 /
    旧版带 ``@`` 的文件 / 含非法 token 的手写文件）先备份为
    ``keywords.official.txt`` 再重建——含非法 token 时必须重建，否则 sherpa 加载时
    会直接 abort（exit 127）。
    """
    tokens = read_tokens(directory / "tokens.txt")
    try:
        expected = build_keywords_line(keyword, tokens)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    keywords_path = directory / "keywords.txt"
    if keywords_path.is_file() and keywords_path.stat().st_size > 0:
        try:
            existing = [
                line.strip()
                for line in keywords_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip()
            ]
        except OSError:
            existing = []
        if expected in existing and not invalid_tokens(existing, tokens):
            return "keywords.txt"  # 已包含当前唤醒词且全部 token 合法，复用
        reason = "不含当前唤醒词" if expected not in existing else "含非法 token"
        backup = directory / "keywords.official.txt"
        if not backup.is_file():
            try:
                shutil.copy2(keywords_path, backup)
            except OSError:
                pass
        log.warning(
            "keywords.txt %s「%s」（可能是官方示例词或旧格式），已备份为 keywords.official.txt 并重建。",
            reason,
            keyword,
        )

    keywords_path.write_text(expected + "\n", encoding="utf-8")
    log.info("已生成唤醒词 keywords.txt：%s", expected)
    return "keywords.txt"


class WakeEngine:
    """常驻监听的离线关键词 spotting 引擎。"""

    def __init__(
        self,
        model_dir: Path | str = WAKE_MODEL,
        sample_rate: int = SAMPLE_RATE,
        keyword: str = DEFAULT_KEYWORD,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        """构造唤醒引擎。

        ``threshold`` 为 ``keywords_threshold``：越高越难触发、误唤醒越少。
        默认 0.25 与 sherpa-onnx 官方一致（实测 0.35 会漏检官方测试音频）。
        """
        self.model_dir = Path(model_dir)
        self.sample_rate = sample_rate
        self.keyword = keyword
        self.threshold = threshold
        self.spotter = self._load(model_dir, keyword)
        self._stream = self._new_stream()

    def _load(self, model_dir: Path | str, keyword: str) -> Any:
        try:
            import sherpa_onnx  # type: ignore
        except ImportError as exc:
            raise DependencyMissing(
                "未安装 sherpa-onnx==1.12.40，无法加载唤醒模型；"
                "请执行 pip install sherpa-onnx==1.12.40（严格锁版本）。"
            ) from exc
        directory = Path(model_dir).expanduser().resolve(strict=True)
        _require_kws_files(directory)

        def loader(_filename: str) -> Any:
            # 已 chdir 到模型目录：模型文件全部用纯文件名。
            # sherpa-onnx 1.12.40 的 KeywordSpotter 为直接传参构造（无 KeywordSpotterConfig），
            # 且 keywords_file 为必需参数，故先确保 keywords.txt 存在。
            keywords_file = _ensure_keywords_file(directory, keyword)
            return sherpa_onnx.KeywordSpotter(
                tokens="tokens.txt",
                encoder="encoder.onnx",
                decoder="decoder.onnx",
                joiner="joiner.onnx",
                keywords_file=keywords_file,
                num_threads=1,
                sample_rate=16000,
                feature_dim=80,
                max_active_paths=4,
                keywords_score=1.0,
                # 阈值取 sherpa 官方默认 0.25（实测 0.35 会漏检官方测试音频）。
                keywords_threshold=self.threshold,
                num_trailing_blanks=4,
                provider="cpu",
            )

        return load_model_safely(directory, "encoder.onnx", loader)

    def _new_stream(self) -> Any:
        return self.spotter.create_stream()

    def feed(self, samples: np.ndarray) -> tuple[bool, float]:
        """喂入一段 16k 单声道 float32 音频，返回 (是否命中唤醒词, 置信度)。

        注意两个 1.12.40 实测细节：
        1. 必须**分块**喂入（约 0.1s 一块）；整段一次性 ``accept_waveform`` 不会触发命中；
        2. ``get_result()`` 返回的是**字符串**（命中时即关键词文本，未命中为 ``''``），
           不是带 ``.keyword`` 属性的对象。
        """
        try:
            self._stream.accept_waveform(self.sample_rate, np.asarray(samples, dtype=np.float32))
            # 可能有多个待解码块，循环解完再统一取结果。
            while self.spotter.is_ready(self._stream):
                self.spotter.decode_stream(self._stream)
            result = self.spotter.get_result(self._stream)
            text = result if isinstance(result, str) else getattr(result, "keyword", "")
            if text:
                # 命中后重置流，避免同一句话重复触发。
                if hasattr(self.spotter, "reset_stream"):
                    self.spotter.reset_stream(self._stream)
                else:  # pragma: no cover - 老版本没有 reset_stream 时的兜底
                    self._stream = self._new_stream()
                # sherpa KWS 命中即返回关键词文本；置信度按阈值映射为 0.9 固定估计。
                return True, 0.9
        except Exception as exc:  # 单帧异常不应中断主监听
            log.warning("唤醒检测单帧异常：%s", exc)
        return False, 0.0

    def reset(self) -> None:
        """重置当前识别流，准备下一轮。"""
        try:
            self._stream = self._new_stream()
        except Exception:  # noqa: BLE001 - 重置失败忽略
            pass


def wake_status() -> dict[str, object]:
    """返回唤醒能力状态；缺库/缺模型时明确降级。"""
    try:
        import sherpa_onnx  # type: ignore
    except ImportError:
        return {
            "enabled": False,
            "message": "未安装 sherpa-onnx==1.12.40，离线唤醒未启用。",
            "required_sherpa_version": "1.12.40",
        }
    if not (Path(WAKE_MODEL).is_dir() and all((Path(WAKE_MODEL) / name).is_file() for name in _KWS_FILES)):
        return {
            "enabled": False,
            "message": "唤醒模型尚未下载，离线唤醒未启用；可在首启向导中下载。",
            "required_sherpa_version": "1.12.40",
        }
    return {"enabled": True, "message": "离线唤醒就绪。", "required_sherpa_version": "1.12.40"}
