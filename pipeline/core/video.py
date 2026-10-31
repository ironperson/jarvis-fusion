"""视频处理核心（M4）。

职责：把一条本地视频变成「可入库的文本 + 关键帧」：
  ① 抽音频（ffmpeg，来自 imageio-ffmpeg，零额外安装）
  ② 语音转写 ASR（faster-whisper，本地推理；模型缺失则优雅降级）
  ③ 提取关键帧（opencv 优先，ffmpeg 兜底）
  ④ 关键帧多模态：可选 caption_fn 用本地视觉模型给帧配文（未配置则仅留附件）

设计原则（与 bge-m3 嵌入器、M3 小红书一致）：
- 所有重依赖（ffmpeg/whisper/opencv）懒加载 + 优雅降级，缺哪个都能跑通路径、不崩溃。
- 沙箱无法下载 whisper 模型权重时，transcribe 抛 ASRUnavailable，由采集器降级为
  “仅保留元数据 + 标记待转写”，用户本机装好模型即可补全。

抖音反爬说明：抖音 web 端强风控（签名参数、设备指纹、登录墙），"视频下载"用通用
工具极不稳定。M4 主路径采用「本地视频文件模式」——你把抖音视频存到 local_dir，
我们负责转写+关键帧+入库，不依赖破解抖音；分享链接自动抓取作为进阶接口预留。
"""
from __future__ import annotations
import shutil
import subprocess
import tempfile
from pathlib import Path


class ASRUnavailable(Exception):
    """whisper 依赖或模型不可用，无法转写。"""


class VideoProcessingError(Exception):
    """视频处理通用错误（ffmpeg 缺失、文件损坏等）。"""


def ffmpeg_exe() -> str:
    """返回可用的 ffmpeg 可执行文件路径（优先 imageio-ffmpeg 自带的）。"""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    # 系统 PATH 中的 ffmpeg
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise VideoProcessingError(
        "未找到 ffmpeg。请安装：pip install imageio-ffmpeg（推荐，自带二进制）"
        "，或系统安装 ffmpeg 并加入 PATH。"
    )


def extract_audio(video_path: str | Path, out_wav: str | Path,
                  sample_rate: int = 16000) -> Path:
    """用 ffmpeg 把视频中的音轨抽成 16k 单声道 wav（whisper 友好格式）。"""
    video_path = Path(video_path)
    out_wav = Path(out_wav)
    if not video_path.exists():
        raise VideoProcessingError(f"视频文件不存在：{video_path}")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    exe = ffmpeg_exe()
    cmd = [
        exe, "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "wav",
        str(out_wav),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=600)
    except subprocess.CalledProcessError as e:
        raise VideoProcessingError(f"ffmpeg 抽音频失败（{video_path.name}）：{e}")
    except subprocess.TimeoutExpired:
        raise VideoProcessingError(f"ffmpeg 抽音频超时（{video_path.name}）")
    if not out_wav.exists() or out_wav.stat().st_size == 0:
        raise VideoProcessingError(f"抽音频产出为空：{out_wav}")
    return out_wav


def transcribe(wav_path: str | Path, model_size: str = "base",
              language: str = "zh", device: str = "cpu") -> tuple[str, list[dict]]:
    """用 faster-whisper 本地转写。

    返回 (全文, 分段列表[{start,end,text}])。
    依赖或模型不可用时抛 ASRUnavailable（由上层降级）。
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ASRUnavailable(
            "未安装 faster-whisper。请安装：pip install faster-whisper "
            "（本机用阿里云镜像：pip install -i https://mirrors.aliyun.com/pypi/simple/ faster-whisper）"
        )
    try:
        model = WhisperModel(model_size, device=device, compute_type="int8")
    except Exception as e:
        raise ASRUnavailable(
            f"whisper 模型加载失败（{model_size}）：{e}。"
            "首次使用会自动从 huggingface 下载权重；若网络不通，请手动下载或配置镜像。"
        )
    try:
        segments, _info = model.transcribe(
            str(wav_path), language=language, beam_size=5, vad_filter=True
        )
        parts: list[str] = []
        segs: list[dict] = []
        for seg in segments:
            txt = (seg.text or "").strip()
            if txt:
                parts.append(txt)
                segs.append({"start": round(seg.start, 2),
                             "end": round(seg.end, 2), "text": txt})
        return "\n".join(parts), segs
    except Exception as e:
        raise ASRUnavailable(f"whisper 转写失败：{e}")


def extract_keyframes(video_path: str | Path, out_dir: str | Path,
                      n: int = 4) -> list[Path]:
    """均匀提取 n 张关键帧（JPEG）。opencv 优先，ffmpeg 兜底。"""
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: list[Path] = []

    # ① opencv 优先
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError("cv2 无法打开视频")
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        if total <= 0:
            raise RuntimeError("无法读取帧数")
        for i in range(n):
            idx = int((i + 0.5) * total / n)
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                break
            fp = out_dir / f"frame_{i+1:03d}.jpg"
            cv2.imwrite(str(fp), frame)
            frames.append(fp)
        cap.release()
        if frames:
            return frames
    except Exception as e:
        print(f"[video] opencv 提取关键帧失败，转 ffmpeg：{e}")

    # ② ffmpeg 兜底：按时间均分抽帧
    exe = ffmpeg_exe()
    try:
        dur = float(subprocess.check_output(
            [exe, "-i", str(video_path)],
            stderr=subprocess.STDOUT, timeout=60
        ).decode(errors="ignore").split("Duration:")[1].split(",")[0]
         .strip().replace(":", " "))
        # 解析 "HH MM SS.ms" 为秒
        parts = dur.split()
        secs = sum(float(p) * 60 ** (2 - i) for i, p in enumerate(parts)) if parts else 0
    except Exception:
        secs = 0
    if secs <= 0:
        return frames
    for i in range(n):
        t = (i + 0.5) * secs / n
        fp = out_dir / f"frame_{i+1:003d}.jpg"
        try:
            subprocess.run(
                [exe, "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
                 "-frames:v", "1", "-q:v", "2", str(fp)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=60)
            if fp.exists() and fp.stat().st_size > 0:
                frames.append(fp)
        except Exception:
            pass
    return frames


def segment_text(segments: list[dict]) -> str:
    """把分段转成带时间戳的文稿（便于复核与切片）。"""
    lines = []
    for s in segments:
        mm_ss = _fmt_ts(s["start"])
        lines.append(f"[{mm_ss}] {s['text']}")
    return "\n".join(lines)


def _fmt_ts(sec: float) -> str:
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m:02d}:{s:02d}"


def process(video_path: str | Path, work_dir: str | Path,
            model_size: str = "base", language: str = "zh",
            n_keyframes: int = 4, keep_audio: bool = False,
            caption_fn=None) -> dict:
    """一条视频的完整处理：抽音频 → 转写 → 关键帧 →（可选）配文。

    返回 dict：
      {
        "video": str, "audio": str|None, "transcript": str,
        "segments": list, "keyframes": list[str],
        "captions": list[str], "asr_ok": bool, "error": str|None
      }
    asr_ok=False 仅表示转写不可用（依赖/模型缺失），不影响关键帧与入库。
    """
    video_path = Path(video_path)
    work_dir = Path(work_dir)
    stem = video_path.stem
    job = work_dir / stem
    job.mkdir(parents=True, exist_ok=True)

    result = {
        "video": str(video_path), "audio": None,
        "transcript": "", "segments": [], "keyframes": [],
        "captions": [], "asr_ok": False, "error": None,
    }

    # ① 抽音频
    wav = job / "audio.wav"
    try:
        extract_audio(video_path, wav)
        result["audio"] = str(wav)
    except VideoProcessingError as e:
        result["error"] = str(e)
        return result

    # ② 转写（优雅降级）
    try:
        text, segs = transcribe(wav, model_size=model_size, language=language)
        result["transcript"] = text
        result["segments"] = segs
        result["asr_ok"] = True
    except ASRUnavailable as e:
        print(f"[video] ASR 暂不可用：{e}（保留音频，转写留待本机补全）")
        result["asr_ok"] = False
    except Exception as e:
        print(f"[video] 转写异常：{e}")

    # ③ 关键帧
    frames = extract_keyframes(video_path, job / "frames", n=n_keyframes)
    result["keyframes"] = [str(f) for f in frames]

    # ④ 关键帧多模态配文（可选本地视觉模型）
    if caption_fn and frames:
        try:
            result["captions"] = [caption_fn(str(f)) for f in frames]
        except Exception as e:
            print(f"[video] 关键帧配文失败：{e}")

    # ⑤ 清理临时音频（除非保留）
    if not keep_audio and wav.exists():
        try:
            wav.unlink()
            result["audio"] = None
        except Exception:
            pass

    return result
