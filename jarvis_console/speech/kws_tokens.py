"""KWS 关键词分词：把 "Hey Jarvis" 切成 ``tokens.txt`` 里的 token 序列。

唯一事实源模块：``speech/wake.py``（运行时自动生成 keywords.txt）与
``tools/kws_make_keywords.py``（手动/预览）都走这里，避免两套分词逻辑漂移。

格式与切分规则（均由 sherpa-onnx 1.12.40 + 官方 KWS 模型实测得出）：
1. keywords.txt **只写 token 序列**，不要加 ``@显示文本`` 后缀（Porcupine 语法）。
   sherpa 会把整行按空格拆开逐个查 tokens.txt，``@Hey Jarvis`` 里的 "Jarvis"
   会报 ``Cannot find ID for token Jarvis`` 并令进程退出（exit 127）。
2. 切分必须**按词进行、且每个词的首 token 带词界标记**（``▁``）：
   官方 ``HELLO WORLD`` 切 ``▁HE LL O ▁WORLD``、``LIGHT UP`` 切 ``▁ L IGHT ▁UP``。
   实测：把整词揉成一串再切（``L IGHT ▁UP``，少了词首 ``▁``）**检不出**关键词——
   声学模型是按词界 token 输出音频的，少了它就对不上。
3. 同一词内取 **token 数最少**的切法（动态规划），与官方风格一致。
4. 写文件前校验每个 token 都真实存在于 tokens.txt：非法 token 会让
   sherpa 在加载时直接 abort（exit 127），必须挡在写入之前。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

# BPE 词界标记（sentencepiece 的 U+2581）。
WORD_BOUNDARY = "▁"


def read_tokens(tokens_path: Path) -> list[str]:
    """读取 tokens.txt，返回 token 列表（每行 ``token id``，取第一列；跳过 ``<...>`` 特殊符）。"""
    tokens: list[str] = []
    with Path(tokens_path).open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            token = line.split()[0]
            if token and not token.startswith("<"):
                tokens.append(token)
    return tokens


def _norm(token: str) -> str:
    """归一化查表 key：去词界/子词标记、转大写。"""
    return token.replace(WORD_BOUNDARY, "").replace("##", "").upper()


def build_indexes(tokens: Iterable[str]) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """返回 ``(词界索引, 普通索引, 原始 token 集合)``。

    - 词界索引：key -> 以 ``▁`` 开头的 token（如 ``HE`` -> ``▁HE``），词首专用；
    - 普通索引：key -> 不带 ``▁`` 的 token（如 ``IGHT`` -> ``IGHT``），词中专用；
    - 原始集合：用于校验 keywords.txt 里的 token 是否真实存在。
    """
    boundary: dict[str, str] = {}
    plain: dict[str, str] = {}
    raw: set[str] = set()
    for tok in tokens:
        raw.add(tok)
        key = _norm(tok)
        if not key:
            continue
        if tok.startswith(WORD_BOUNDARY):
            boundary.setdefault(key, tok)
        else:
            plain.setdefault(key, tok)
    return boundary, plain, raw


def _segment_word(word: str, boundary: dict[str, str], plain: dict[str, str], has_bare: bool) -> list[str] | None:
    """对单个词做 DP（token 数最少），首 token 必须带词界标记。

    两种合法的词首形态：
    a) 直接用一个 ``▁XXX`` token（如 ``▁HE`` / ``▁UP``）；
    b) 独立 ``▁`` token + 普通 token（如 ``▁`` + ``L``，词表里有裸 ``▁`` 时）。
    """
    n = len(word)
    if n == 0:
        return []
    inf = float("inf")
    best = [inf] * (n + 1)
    back: list[tuple[int, tuple[str, ...]] | None] = [None] * (n + 1)
    best[0] = 0
    for i in range(n):
        if best[i] == inf:
            continue
        first = i == 0
        for j in range(i + 1, n + 1):
            piece = word[i:j]
            if first:
                options: list[tuple[str, ...]] = []
                if piece in boundary:
                    options.append((boundary[piece],))
                if has_bare and piece in plain:
                    options.append((WORD_BOUNDARY, plain[piece]))
            else:
                options = [(plain[piece],)] if piece in plain else []
            for toks in options:
                if best[i] + len(toks) < best[j]:
                    best[j] = best[i] + len(toks)
                    back[j] = (i, toks)
    if best[n] == inf:
        return None
    out: list[str] = []
    j = n
    while j > 0:
        step = back[j]
        if step is None:  # pragma: no cover - 理论不可达
            return None
        i, toks = step
        out.extend(reversed(toks))
        j = i
    out.reverse()
    return out


def segment_keyword(keyword: str, tokens: Iterable[str]) -> list[str] | None:
    """把唤醒词切成官方风格的 token 序列；无法切分时返回 None。"""
    boundary, plain, raw = build_indexes(tokens)
    has_bare = WORD_BOUNDARY in raw
    out: list[str] = []
    for word in keyword.upper().split():
        cleaned = "".join(ch for ch in word if ch.isalnum())
        if not cleaned:
            continue
        piece = _segment_word(cleaned, boundary, plain, has_bare)
        if piece is None:
            return None
        out.extend(piece)
    return out or None


def build_keywords_line(keyword: str, tokens: Iterable[str]) -> str:
    """返回 keywords.txt 的一行内容（纯 token 序列，官方风格）。

    切不出时抛 :class:`ValueError`，消息里带可执行的下一步。
    """
    segment = segment_keyword(keyword, tokens)
    if not segment:
        raise ValueError(
            f"无法用该模型的 tokens.txt 切分唤醒词 “{keyword}”。\n"
            "请换用词表覆盖该词的 KWS 模型，或手写 keywords.txt"
            f"（每行纯 token 序列、按词切分且词首带 {WORD_BOUNDARY}，如 `▁HE Y ▁JA R VI S`）。"
        )
    return " ".join(segment)


def invalid_tokens(lines: Iterable[str], tokens: Iterable[str]) -> list[str]:
    """返回 keywords.txt 内容中**不存在于 tokens.txt** 的 token（空列表 = 全部合法）。

    非法 token 会让 sherpa-onnx 在加载 KeywordSpotter 时直接 abort（exit 127），
    因此在写入/复用前必须先过这道校验。
    """
    raw = set(tokens)
    bad: list[str] = []
    for line in lines:
        for tok in line.split():
            if tok not in raw:
                bad.append(tok)
    return bad
