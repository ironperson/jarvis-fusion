"""抖音采集器（M4）。

主路径「本地视频文件模式」：你把抖音视频（或任何短视频）存到 local_dir，
采集器负责 抽音频→ASR 转写→关键帧提取→入库。不依赖破解抖音反爬，最稳。

进阶接口「分享链接自动抓取」：抖音强风控，通用下载工具极不稳定，本版本
默认不启用，仅留接口与提示（与 M3 小红书思路一致：代码先行、本机实测）。

对应 Aivy OS：单个采集器实例，对应一个"子智能体后台并行"节点。
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from datetime import datetime, timezone

try:
    from .base import BaseCollector, RawItem
except ImportError:
    from collectors.base import BaseCollector, RawItem

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".flv", ".wmv"}


def _norm_local_id(file_path: str) -> str:
    """本地视频用绝对路径做稳定 ID（保证重跑幂等）。"""
    h = hashlib.md5(str(Path(file_path).resolve()).encode("utf-8")).hexdigest()[:10]
    return f"douyin-{h}"


def _sidecar_desc(video_path: Path) -> str:
    """同名的 .txt 作为视频的文字说明/标题（可选）。"""
    txt = video_path.with_suffix(".txt")
    if txt.exists():
        try:
            return txt.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""


def _fmt_ts(sec: float) -> str:
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m:02d}:{s:02d}"


def process_video_to_item(video_path: str, cfg: dict) -> RawItem:
    """把单个视频处理成 RawItem（接入 core/video.py）。"""
    video_path = Path(video_path)
    if not video_path.exists():
        return RawItem(url=str(video_path), title=video_path.name,
                      content="", source="douyin", keywords=["douyin"])

    work_dir = Path(cfg.get("work_dir", "D:/Desktop/MyKnowledge/douyin_work"))
    model_size = cfg.get("asr_model_size", "base")
    language = cfg.get("language", "zh")
    n_keyframes = int(cfg.get("keyframes", 4))
    keep_audio = bool(cfg.get("keep_audio", False))

    try:
        from core.video import process as process_video
    except ImportError:
        from core.video import process as process_video

    res = process_video(
        video_path, work_dir,
        model_size=model_size, language=language,
        n_keyframes=n_keyframes, keep_audio=keep_audio,
    )

    stem = video_path.stem
    sidecar = _sidecar_desc(video_path)
    title = (sidecar.splitlines()[0] if sidecar else stem) or stem

    # 组装正文
    parts = [f"# {title}", ""]
    if sidecar:
        parts.append(sidecar)
        parts.append("")
    parts.append("来源文件：" + str(video_path))
    parts.append("")

    if res["asr_ok"] and res["transcript"]:
        parts.append("## 语音转写（ASR / faster-whisper）")
        parts.append("")
        # 优先用带时间戳的文稿；没有分段则直接用全文
        if res["segments"]:
            from core.video import segment_text
            parts.append(segment_text(res["segments"]))
        else:
            parts.append(res["transcript"])
        parts.append("")
    else:
        parts.append("## 语音转写")
        parts.append("")
        parts.append("（ASR 暂未生成：本机未安装 faster-whisper 或未下载模型权重。"
                     "音频已暂存，装好模型后重跑 `collect douyin` 即可补全转写。）")
        parts.append("")

    if res["keyframes"]:
        parts.append("## 关键帧（多模态附件）")
        parts.append("")
        for i, fp in enumerate(res["keyframes"], 1):
            cap = ""
            if i - 1 < len(res.get("captions", [])) and res["captions"][i - 1]:
                cap = "：" + res["captions"][i - 1]
            parts.append(f"- 帧{i} {Path(fp).name}{cap}")
        parts.append("")

    content = "\n".join(parts).strip() + "\n"

    keywords = ["douyin"] + list(cfg.get("keywords") or [])
    # 从文件名/说明里抽 #话题
    for m in re.findall(r"#([^\s#@]+)", title + " " + sidecar):
        keywords.append(m.strip().lstrip("#"))

    return RawItem(
        url=str(video_path.resolve()),
        title=title,
        content=content,
        source="douyin",
        keywords=list(dict.fromkeys(keywords)),
        attachments=res["keyframes"],
    )


class DouyinCollector(BaseCollector):
    source_name = "douyin"

    def collect(self) -> list[RawItem]:
        cfg = self.cfg

        # —— 进阶接口：分享链接自动抓取（默认关闭，仅提示）——
        share_links = list(cfg.get("share_links") or [])
        if share_links:
            print("[douyin] 检测到 share_links，但本版本「链接自动抓取」为进阶接口、默认未启用。"
                  "抖音强风控，建议改用「本地视频文件模式」：把视频存到 local_dir 后重跑。"
                  "如需启用，请参考 docs 中的链接抓取说明（需登录态 + Playwright）。")
            # 不在此实做自动抓取，避免脆弱路径在生产机误触发风控。

        items: list[RawItem] = []

        # —— 主路径：本地视频文件模式 ——
        explicit = list(cfg.get("videos") or [])
        local_dir = cfg.get("local_dir", "")
        files: list[str] = []
        for v in explicit:
            files.append(v)
        if local_dir:
            p = Path(local_dir)
            if p.exists():
                for f in sorted(p.rglob("*")):
                    if f.suffix.lower() in VIDEO_EXTS:
                        files.append(str(f))
            else:
                print(f"[douyin] local_dir 不存在：{local_dir}")

        if not files:
            print("[douyin] 未配置 local_dir / videos，或未发现视频文件，跳过。"
                  "请在 sources.yaml 的 douyin.local_dir 指向存放抖音视频的文件夹。")
            return items

        print(f"[douyin] 发现 {len(files)} 个视频，开始处理（ASR + 关键帧）……")
        for f in files:
            try:
                item = process_video_to_item(f, cfg)
                if item.ok:
                    items.append(item)
                    print(f"  ✓ 处理：{Path(f).name}")
                else:
                    print(f"  ✗ 跳过（处理失败）：{f}")
            except Exception as e:
                print(f"  ✗ 处理异常 {f}: {e}")

        print(f"[douyin] 采集完成：{len(items)} 条。")
        return items


def load_douyin_cfg(sources: dict) -> dict:
    return sources.get("douyin", {})


if __name__ == "__main__":
    # 自检：构造一个虚拟视频路径，验证 process_video_to_item 在缺少依赖时也能降级返回
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "demo.mp4"
    tmp.write_bytes(b"not-a-real-video")
    it = process_video_to_item(str(tmp), {"local_dir": "", "work_dir": str(Path(tempfile.mkdtemp()))})
    print("降级 RawItem：", it.title, "| content 长度:", len(it.content))
