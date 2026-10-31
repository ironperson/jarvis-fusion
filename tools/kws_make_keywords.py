#!/usr/bin/env python3
"""从 KWS 模型的 tokens.txt 生成 keywords.txt（供 sherpa-onnx 1.12.40 KWS 使用）。

用法：
    python tools/kws_make_keywords.py --model-dir jarvis_console/models/wakeword --keyword "Hey Jarvis"
    python tools/kws_make_keywords.py --model-dir ... --keyword "Hey Jarvis" --dry-run
    python tools/kws_make_keywords.py --model-dir ... --keyword "Hey Jarvis" --force

分词逻辑复用 ``jarvis_console.speech.kws_tokens``（与 ``speech/wake.py`` 同一实现：
动态规划取 token 数最少），避免两处漂移。

⚠️ 格式铁律（sherpa-onnx 1.12.40 实测）：keywords.txt **只写 token 序列**，
    不要加 ``@显示文本`` 后缀——那是 Porcupine 的语法。sherpa 会把整行按空格拆开
    逐个查 tokens.txt，``@Hey Jarvis`` 里的 "Jarvis" 会直接报
    ``Cannot find ID for token Jarvis`` 并导致进程退出（exit 127）。
    官方 KWS 包自带的 keywords.txt 同样是纯 token 行（如 ``▁HE LL O ▁WORLD``）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis_console.speech.kws_tokens import build_keywords_line, read_tokens  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 sherpa-onnx KWS 的 keywords.txt")
    parser.add_argument("--model-dir", required=True, help="包含 tokens.txt 的模型目录")
    parser.add_argument("--keyword", default="Hey Jarvis", help="唤醒词，默认 'Hey Jarvis'")
    parser.add_argument("--out", default=None, help="输出路径，默认 <model-dir>/keywords.txt")
    parser.add_argument("--dry-run", action="store_true", help="只打印分词结果，不写文件")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 keywords.txt")
    args = parser.parse_args(argv)

    model_dir = Path(args.model_dir).expanduser().resolve()
    tokens_path = model_dir / "tokens.txt"
    if not tokens_path.is_file():
        print(f"[错误] 找不到 {tokens_path}", file=sys.stderr)
        return 2

    try:
        line = build_keywords_line(args.keyword, read_tokens(tokens_path))
    except ValueError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 3

    print(f"分词结果（写入 keywords.txt 的内容）：{line}")
    print(f"对应唤醒词：{args.keyword}")
    if args.dry_run:
        print("（--dry-run：未写文件）")
        return 0

    out = Path(args.out).expanduser() if args.out else model_dir / "keywords.txt"
    if out.exists() and not args.force:
        print(f"[提示] {out} 已存在；如需覆盖请加 --force。当前未改动。")
        return 0
    out.write_text(line + "\n", encoding="utf-8")
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
