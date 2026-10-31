"""小红书采集器（M3）。

职责：用 Playwright 真实浏览器 + 登录态 Cookie，抓取小红书笔记（标题/正文/标签/作者），
返回 RawItem 列表。对应 Aivy OS「子智能体后台并行」的单个采集器实例。

反爬策略（诚实说明）：小红书 web 端有强风控（签名参数、设备指纹、登录墙）。本采集器采用
「个人自用、低频、单并发、带登录态」的保守策略，并支持：
- 无 Cookie 时交互式登录（打开浏览器让用户扫码/登录，自动保存 Cookie 到 cookie_path）；
- 多套解析回退（__INITIAL_STATE__ JSON → <meta> → 正则），应对页面结构变动；
- 随机延时、固定 UA、复用持久上下文、隐藏 webdriver，尽量模拟真人；
- 默认采用「指定笔记 URL 列表」模式（最稳）；关键词搜索为可选高风险模式。
注意：批量搜索/Feed 抓取风险更高，如启用 keywords 请知悉可能触发风控。

离线可测：parse_note_page(html, url) 是纯函数，可用保存的 HTML 验证解析，无需浏览器。
"""
from __future__ import annotations
import json
import random
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

try:
    from .base import BaseCollector, RawItem
except ImportError:
    sys.path.insert(0, Path(__file__).resolve().parent.parent)
    from collectors.base import BaseCollector, RawItem

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
LOGIN_URL = "https://www.xiaohongshu.com"


def _norm_url(url: str) -> str:
    """归一化小红书笔记 URL：提取 note id，生成稳定链接（保证幂等）。"""
    m = re.search(r"/(?:explore|discovery/item|search_result|user)/([0-9a-zA-Z]+)", url)
    if m:
        return f"https://www.xiaohongshu.com/explore/{m.group(1)}"
    return url


def _extract_balanced_json(text: str, marker: str) -> str | None:
    """从 marker 之后提取配平的花括号 JSON（处理嵌套、字符串与转义）。

    小红书把笔记数据塞进 window.__INITIAL_STATE__ = {...};，其中有多层嵌套，
    普通正则非贪婪匹配会提前截断，故用字符级括号配平提取。
    """
    idx = text.find(marker)
    if idx < 0:
        return None
    i = text.find("{", idx)
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(text)):
        ch = text[j]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return None


def parse_note_page(html: str, url: str) -> dict:
    """从笔记页 HTML 抽取结构化字段（纯函数，离线可测）。

    返回 {title, text, tags, author}；任意字段缺失不报错。
    优先级：① 页面内嵌 __INITIAL_STATE__ JSON（最可靠）→ ② <meta> 兜底 → ③ 正则兜底。
    """
    result = {"title": "", "text": "", "tags": [], "author": ""}

    # ① 页面内嵌 JSON（小红书把笔记数据塞进 window.__INITIAL_STATE__）
    raw = _extract_balanced_json(html, "window.__INITIAL_STATE__")
    if raw:
        try:
            data = json.loads(raw)
            note_data = data.get("noteData", {})
            notes = note_data.get("noteData", {}) if isinstance(note_data, dict) else {}
            note = next(iter(notes.values()), {}) if notes else {}
            if not note and isinstance(note_data, dict):
                note = note_data  # 结构可能因版本而异，做一次兜底
            result["title"] = (note.get("title") or "").strip()
            result["text"] = (note.get("desc") or note.get("content") or "").strip()
            for t in note.get("tagList", []) or []:
                name = t.get("name") if isinstance(t, dict) else str(t)
                if name:
                    result["tags"].append(name.strip().lstrip("#"))
            user = note.get("user", {}) or {}
            result["author"] = (user.get("nickname") or user.get("name") or "").strip()
        except Exception as e:
            print(f"[xhs-parse] __INITIAL_STATE__ 解析失败，转兜底：{e}")

    # ② <meta> 兜底（仅当 JSON 未拿到关键字段）
    if not result["title"] or not result["text"]:
        for prop, key in (("og:title", "title"), ("description", "text"),
                          ("og:description", "text")):
            if result[key]:
                continue
            mm = re.search(
                rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.I)
            if not mm:
                mm = re.search(
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(prop)}["\']',
                    html, re.I)
            if mm:
                result[key] = mm.group(1).strip()

    # ③ 标签正则兜底（#话题）
    if not result["tags"]:
        result["tags"] = re.findall(r"#([^\s#@]+)", result.get("text", ""))

    result["tags"] = [t for t in result["tags"] if t][:20]
    return result


def _cookie_path(cfg: dict) -> Path:
    p = cfg.get("cookie_path", "data/cookies/xhs.json")
    return Path(p)


def _load_cookies(path: Path) -> list | None:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_cookies(path: Path, cookies: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")


def _interactive_login(context, cookie_path: Path) -> bool:
    """打开浏览器让用户手动登录，成功后保存 Cookie。返回是否成功。"""
    page = context.new_page()
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"[xhs] 打开登录页失败：{e}")
        page.close()
        return False
    print("[xhs] 浏览器已打开，请在页面中完成登录（扫码 / 手机号）。登录成功后，")
    input("[xhs] 回到此处按回车继续（未完成登录直接回车将视为失败）...")
    try:
        cookies = context.cookies()
    except Exception:
        cookies = []
    page.close()
    if cookies:
        _save_cookies(cookie_path, cookies)
        print(f"[xhs] 登录态已保存至 {cookie_path}")
        return True
    return False


class XiaohongshuCollector(BaseCollector):
    source_name = "xiaohongshu"

    def collect(self) -> list[RawItem]:
        cfg = self.cfg
        cookie_path = _cookie_path(cfg)
        note_urls = list(cfg.get("note_urls") or []) + list(cfg.get("seeds") or [])
        keywords = list(cfg.get("keywords") or [])
        if not note_urls and not keywords:
            print("[xhs] 未配置 note_urls / seeds / keywords，跳过。")
            return []

        min_delay = float(cfg.get("min_delay", 5))
        max_delay = float(cfg.get("max_delay", 15))
        headless = bool(cfg.get("headless", True))

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[xhs] 未安装 playwright，无法运行。请先：pip install playwright && playwright install chromium")
            return [RawItem(url=u, title=u, content="", source="xiaohongshu", keywords=[])
                    for u in note_urls]

        cookies = _load_cookies(cookie_path)
        items: list[RawItem] = []

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as e:
                print(f"[xhs] 浏览器启动失败（多半是 Chromium 未下载）：{e}")
                print("[xhs] 请在本机执行：python -m playwright install chromium")
                return items

            context = browser.new_context(
                user_agent=DEFAULT_UA,
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            # 隐藏自动化特征
            context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            if cookies:
                try:
                    context.add_cookies(cookies)
                except Exception as e:
                    print(f"[xhs] Cookie 载入失败，尝试交互登录：{e}")
                    cookies = None
            if not cookies:
                print("[xhs] 未找到登录态，进入交互登录模式（需非 headless）。")
                if headless:
                    context.close()
                    browser.close()
                    # 以非 headless 重新拉起用于登录
                    browser = p.chromium.launch(headless=False,
                                                args=["--disable-blink-features=AutomationControlled"])
                    context = browser.new_context(user_agent=DEFAULT_UA,
                                                  viewport={"width": 1280, "height": 800},
                                                  locale="zh-CN")
                    context.add_init_script(
                        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                    )
                if not _interactive_login(context, cookie_path):
                    print("[xhs] 未获得登录态，无法采集。")
                    browser.close()
                    return items
                cookies = _load_cookies(cookie_path)

            # 单并发串行抓取（强反爬源，限频优先）
            for url in note_urls:
                try:
                    item = self._fetch_note(context, url)
                    if item:
                        items.append(item)
                except Exception as e:
                    print(f"[xhs] 抓取失败 {url}: {e}")
                    items.append(RawItem(url=_norm_url(url), title=url, content="",
                                         source="xiaohongshu", keywords=[]))
                time.sleep(random.uniform(min_delay, max_delay))

            # 关键词搜索（高风险，可关闭）
            if keywords:
                print("[xhs] 关键词搜索为高风险模式，注意频率。")
                for kw in keywords:
                    try:
                        found = self._search_keyword(context, kw)
                        for it in found:
                            items.append(it)
                            time.sleep(random.uniform(min_delay, max_delay))
                    except Exception as e:
                        print(f"[xhs] 关键词搜索失败 {kw}: {e}")

            browser.close()

        print(f"[xhs] 采集完成：{len(items)} 条。")
        return items

    def _fetch_note(self, context, url: str) -> RawItem | None:
        page = context.new_page()
        try:
            page.goto(_norm_url(url), wait_until="networkidle", timeout=60000)
            # 登录墙检测
            if page.query_selector("text=登录") and not context.cookies():
                raise RuntimeError("检测到登录墙，请删除 cookie 文件重新登录")
            html = page.content()
        finally:
            page.close()
        data = parse_note_page(html, url)
        if not data.get("text"):
            raise RuntimeError("解析为空（页面结构变动或需登录）")
        title = data.get("title") or _norm_url(url)
        tags = [t.lstrip("#") for t in (data.get("tags") or [])] or ["xiaohongshu"]
        content = f"# {title}\n\n{data['text']}"
        if data.get("author"):
            content += f"\n\n作者：{data['author']}"
        return RawItem(
            url=_norm_url(url),
            title=title,
            content=content,
            source="xiaohongshu",
            keywords=tags,
        )

    def _search_keyword(self, context, keyword: str) -> list[RawItem]:
        """搜索关键词，取前 N 条笔记链接再逐条抓取（高风险）。"""
        page = context.new_page()
        items: list[RawItem] = []
        try:
            page.goto(f"https://www.xiaohongshu.com/search_result?keyword={keyword}",
                      wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)
            links = page.eval_on_selector_all(
                "a[href*='/explore/']",
                "els => els.slice(0,10).map(e => e.href)")
        finally:
            page.close()
        for link in links[: int(self.cfg.get("search_limit", 5))]:
            try:
                it = self._fetch_note(context, link)
                if it:
                    items.append(it)
            except Exception as e:
                print(f"[xhs] 搜索结果抓取失败 {link}: {e}")
        return items


def load_xiaohongshu_cfg(sources: dict) -> dict:
    return sources.get("xiaohongshu", {})


if __name__ == "__main__":
    # 自检：仅验证 parse_note_page 纯函数（无需浏览器/网络）
    sample = """
    <html><head>
    <meta property="og:title" content="meta标题兜底">
    <meta property="og:description" content="meta描述兜底">
    </head><body>
    <script>window.__INITIAL_STATE__={"noteData":{"noteData":{"abc123":{
        "title":"如何搭建个人知识库","desc":"第一步明确目标。#知识管理 #效率工具",
        "tagList":[{"name":"知识管理"},{"name":"效率工具"}],
        "user":{"nickname":"小贾"}}}};</script>
    </body></html>
    """
    r = parse_note_page(sample, "https://www.xiaohongshu.com/explore/abc123")
    print("解析结果：", r)
