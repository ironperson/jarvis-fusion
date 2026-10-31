"""网页采集器（M2）。

职责：按 sources.yaml 中 web.seeds 的 URL 列表，并发抓取并抽取正文，
返回 RawItem 列表。对应 Aivy OS「子智能体后台并行」——多个种子 URL
用线程池并发抓取，互不阻塞。

设计要点（韧性）：
- 抓取用标准库 urllib（无需 aiohttp）；并发用 ThreadPoolExecutor。
- 解析优先 trafilatura，否则 stdlib 兜底（见 processors/html_extract）。
- 支持可选的浅层站内链接发现（discover_links + max_pages），默认关闭。
- 镜像回退：sources.yaml 的 mirror_map 配置「域名→镜像域名」，原域名抓取失败时自动换镜像重试（如 github.com→kkgithub.com）。
- 单条失败不影响整体；失败 url 由编排层记入 tasks 表。
"""
from __future__ import annotations
import concurrent.futures as cf
import urllib.request
import urllib.error
from pathlib import Path

try:  # 作为包导入
    from .base import BaseCollector, RawItem
    from processors.html_extract import extract, _links_from_html
except ImportError:  # 作为脚本直接运行（python collectors/web.py）
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from collectors.base import BaseCollector, RawItem
    from processors.html_extract import extract, _links_from_html


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _http_get(url: str, timeout: int, verify_ssl: bool) -> str:
    """网络抓取：优先 requests（自动走系统代理、编码处理更稳），缺失则回退 urllib。"""
    try:
        import requests

        r = requests.get(
            url, timeout=timeout, verify=verify_ssl,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        # 修正编码：中文站点常为 GBK/GB2312，requests 默认易误判为 Latin-1 产生乱码；
        # 用 apparent_encoding（chardet 猜测）兜底，确保中文正文正确解码。
        if r.apparent_encoding and r.apparent_encoding.lower() not in ("iso-8859-1", "ascii", "us-ascii"):
            r.encoding = r.apparent_encoding
        return r.text
    except ImportError:
        pass
    except Exception as e:
        # requests 报错时也回退 urllib 试一次
        print(f"[web] requests 抓取失败，回退 urllib：{e}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        charset = resp.headers.get_content_charset() or "utf-8"
        data = resp.read()
    return data.decode(charset, errors="ignore")


def _fetch(url: str, timeout: int, verify_ssl: bool = True) -> tuple[str, str | None]:
    """返回 (url, html_or_error)。抓取失败返回 (url, 错误串)。

    支持两类种子：
    - 本地文件 / file:// 路径：直接读盘（用于离线入库与测试）；
    - http(s) URL：走网络抓取（requests 优先，自动读取 HTTPS_PROXY 等系统代理）。
    verify_ssl=False 仅用于存在中间人代理的自建环境（如本沙箱），生产机保持 True。
    """
    if url.startswith("file://") or Path(url).exists():
        try:
            local = url[len("file://"):] if url.startswith("file://") else url
            return url, Path(local).read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return url, f"__ERR__:{e}"
    try:
        return url, _http_get(url, timeout, verify_ssl)
    except Exception as e:  # 网络/超时/解码错误都视为抓取失败
        return url, f"__ERR__:{e}"


def _build_candidates(url: str, mirror_map: dict) -> list[str]:
    """根据 mirror_map 生成候选 URL：原 URL 在前，镜像替换在后。

    例如 mirror_map={"github.com": "kkgithub.com"} 时，
    https://github.com/x → [原, https://kkgithub.com/x]。
    """
    cands = [url]
    for orig, mir in (mirror_map or {}).items():
        if orig and mir and orig in url:
            cands.append(url.replace(orig, mir, 1))
    return cands


def _fetch_with_fallback(primary_url: str, candidates: list[str], timeout: int, verify_ssl: bool) -> tuple[str, str | None]:
    """依次尝试候选 URL（原域名 + 镜像域名），返回 (primary_url, html_or_error)。

    primary_url 始终是种子原始地址——保证 note_id 与任务身份稳定（无论走哪个镜像成功）。
    """
    last_err = None
    for u in candidates:
        try:
            _, html = _fetch(u, timeout, verify_ssl)
        except Exception as e:
            html = f"__ERR__:{e}"
        if html and not html.startswith("__ERR__"):
            if u != primary_url:
                print(f"[web] 原域名失败，已通过镜像抓取成功：{primary_url} → {u}")
            return primary_url, html
        last_err = html
    return primary_url, last_err or "__ERR__:all candidates failed"


class WebCollector(BaseCollector):
    source_name = "web"

    def collect(self) -> list[RawItem]:
        seeds = [s for s in self.cfg.get("seeds", []) if s]
        if not seeds:
            print("[web] 没有配置 seeds，跳过。")
            return []

        timeout = int(self.cfg.get("timeout", 30))
        concurrency = int(self.cfg.get("concurrency", 4))
        discover = bool(self.cfg.get("discover_links", False))
        max_pages = int(self.cfg.get("max_pages", 20))
        mirror_map = self.cfg.get("mirror_map", {})  # 例：{"github.com": "kkgithub.com"}

        # 1) 抓取（并发）。先抓种子，必要时按站内链接浅层扩展。
        to_fetch = list(seeds)
        fetched: dict[str, str] = {}
        seen: set[str] = set(to_fetch)
        items: list[RawItem] = []  # 采集结果（含抓取失败项，便于任务看板记录失败）

        with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
            while to_fetch:
                batch = to_fetch
                to_fetch = []
                futs = {
                    ex.submit(_fetch_with_fallback, u, _build_candidates(u, mirror_map), timeout,
                              self.cfg.get("verify_ssl", True)): u
                    for u in batch
                }
                for fut in cf.as_completed(futs):
                    url, html = fut.result()
                    if html is None or html.startswith("__ERR__"):
                        print(f"[web] 抓取失败 {url}: {html}")
                        # 失败项也进入 items，由编排层记入任务表（对应 Aivy Mission 失败可见、可续跑）
                        items.append(RawItem(url=url, title=url, content="", source="web", keywords=[]))
                        continue
                    fetched[url] = html
                    if discover and len(fetched) < max_pages:
                        for link in _links_from_html(html, url, max_links=5):
                            if link not in seen and len(fetched) + len(to_fetch) < max_pages:
                                seen.add(link)
                                to_fetch.append(link)

        # 2) 抽取正文
        for url, html in fetched.items():
            try:
                res = extract(html, url)
                items.append(RawItem(
                    url=url,
                    title=res["title"],
                    content=res["text"],
                    source="web",
                    keywords=res["keywords"],
                ))
            except Exception as e:
                print(f"[web] 解析失败 {url}: {e}")
        print(f"[web] 采集完成：{len(items)} 条（来自 {len(fetched)} 个页面）。")
        return items


def load_web_cfg(sources: dict) -> dict:
    return sources.get("web", {})


if __name__ == "__main__":
    # 自检：用本地 HTML 字符串验证抽取（无需网络）
    sample = (
        '<html><head><title>示例文章</title><meta name="keywords" content="AI,知识库">'
        '</head><body><article><h1>示例文章</h1>'
        '<p>这是关于个人知识库的一段内容。向量检索让零散信息可被语义检索。</p>'
        '<p>第二段讨论原子笔记。</p></article></body></html>'
    )
    r = extract(sample, "https://example.com/a")
    print(r)
    c = WebCollector({"seeds": []})
    print("collector ok:", c.source_name)
