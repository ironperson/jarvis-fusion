"""HTML 正文抽取（stdlib 兜底版）。

优先用 trafilatura（质量好、抗噪声强）；若运行环境未安装 trafilatura，
自动回退到基于标准库 html.parser 的轻量抽取，保证 Web 采集器在任何环境
都能跑起来。生产机装好 trafilatura 后无需改代码即自动切换为更优解析器。
"""
from __future__ import annotations
import re
from html.parser import HTMLParser
from urllib.parse import urlparse


# ---------- 标准库兜底抽取器 ----------
class _TextExtractor(HTMLParser):
    """剥离标签、抽取可见正文的极简解析器。"""

    _SKIP = {"script", "style", "noscript", "head", "meta", "link", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            t = data.strip()
            if t:
                self._parts.append(t)

    def text(self) -> str:
        raw = " ".join(self._parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _title_from_html(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def _keywords_from_html(html: str) -> list[str]:
    m = re.search(r'<meta[^>]+name=["\']keywords["\'][^>]+content=["\'](.*?)["\']',
                  html, re.IGNORECASE | re.DOTALL)
    if not m:
        m = re.search(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']keywords["\']',
                      html, re.IGNORECASE | re.DOTALL)
    if m:
        return [k.strip() for k in m.group(1).split(",") if k.strip()][:10]
    return []


def _links_from_html(html: str, base_url: str, max_links: int = 10) -> list[str]:
    """抽取同域名内的站内链接，用于浅层发现（可选）。"""
    try:
        base_host = urlparse(base_url).netloc
    except Exception:
        base_host = ""
    out: list[str] = []
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = m.group(1).strip()
        if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        if href.startswith("/"):
            try:
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            except Exception:
                continue
        try:
            if urlparse(href).netloc == base_host:
                out.append(href)
        except Exception:
            continue
        if len(out) >= max_links:
            break
    return out


def extract(html: str, url: str = "") -> dict:
    """返回 {title, text, keywords}。优先 trafilatura，否则 stdlib 兜底。"""
    try:
        import trafilatura

        downloaded = html  # trafilatura.extract 也能直接吃 HTML 字符串
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        meta = trafilatura.extract_metadata(downloaded)
        title = (meta.title if meta else "") or _title_from_html(html)
        kw = []
        if meta and getattr(meta, "as_dict", None):
            kw = (meta.as_dict().get("keywords") or [])[:10]
        kw = [k for k in kw if isinstance(k, str)]
        if text and text.strip():
            return {"title": title, "text": text.strip(), "keywords": kw}
    except Exception as e:
        print(f"[html_extract] trafilatura 不可用，使用 stdlib 兜底：{e}")

    # 兜底
    p = _TextExtractor()
    try:
        p.feed(html)
        text = p.text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
    return {"title": _title_from_html(html), "text": text, "keywords": _keywords_from_html(html)}
