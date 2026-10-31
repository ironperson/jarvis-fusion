"""受严格写入白名单保护的文件工具。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.safety import PathPolicy
from ..core.tool_registry import tool
from ..paths import system_desktop

PATH_POLICY = PathPolicy()


def authorize_path(path: str) -> str:
    """把用户在文件选择弹窗中手动选中的目录加入本次运行白名单。"""
    return str(PATH_POLICY.add_allowed_root(path))


def _is_sensitive_read_target(path: Path) -> bool:
    """判断是否命中读取拒绝清单（配置化，见 console.yaml 的 safety.read_denylist）。"""
    return PATH_POLICY.is_sensitive_read(path)


def describe_read_deny(path: Path) -> str:
    """读取拒绝的统一说明文案。"""
    return (
        f"拒绝读取密钥/凭据类文件：{path}。"
        "这类文件一旦读进上下文就会随请求发往云端；确认是否配置 Key 只看界面上的"
        "“已配置/未配置”指示。除 ~/.ssh（不可关闭的安全底线）外，其余条目可在 "
        "config/console.yaml 的 safety.read_denylist 中移除对应条目后放开。"
    )


@tool(
    name="find_files",
    description=(
        "按文件名模糊查找本机文件；pattern='*' 时列出 root 目录第一层。"
        "需要先确认完整路径、定位将要读取或删除的具体文件时使用。"
    ),
    category="file",
)
def find_files(pattern: str, root: str | None = None, limit: int = 12) -> str:
    """模糊找文件；星号模式合并了目录列举能力。"""
    bounded_limit = min(100, max(1, int(limit)))
    if root:
        root_path = PATH_POLICY.normalize(root)
    else:
        mock_root = os.environ.get("JARVIS_MOCK_DIR", "").strip()
        root_path = Path(mock_root).resolve() if mock_root else system_desktop()
    if pattern.strip() in {"", "*"}:
        if not root_path.exists() or not root_path.is_dir():
            return f"目录不存在：{root_path}"
        entries = sorted(root_path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:bounded_limit]
        if not entries:
            return f"目录为空：{root_path}"
        visible = [item for item in entries if not _is_sensitive_read_target(item)]
        hidden = len(entries) - len(visible)
        suffix = f"（已隐藏 {hidden} 个密钥/凭据类文件）" if hidden else ""
        return f"{root_path} 下共列出 {len(visible)} 项{suffix}：\n" + "\n".join(
            str(item.resolve()) for item in visible
        )
    from jarvis.skills.system import find_files as existing_find

    raw = existing_find(pattern.strip(), str(root_path), bounded_limit)
    return _filter_secret_hits(raw)


def _filter_secret_hits(raw: str) -> str:
    """过滤既有模糊查找结果中的密钥/凭据类路径，并如实说明隐藏数量。"""
    header = "找到 "
    if not raw.startswith(header):
        return raw
    try:
        count_text, remainder = raw[len(header):].split(" 个", 1)
        total = int(count_text)
    except ValueError:
        return raw
    paths = [line.strip() for line in remainder.splitlines() if line.strip()]
    visible = [item for item in paths if not _is_sensitive_read_target(Path(item))]
    hidden = len(paths) - len(visible)
    if not visible:
        return f"没有找到匹配的非密钥类文件（已隐藏 {hidden} 个密钥/凭据类文件）"
    suffix = f"（已隐藏 {hidden} 个密钥/凭据类文件）" if hidden else ""
    return f"找到 {len(visible)} 个{suffix}：\n" + "\n".join(visible)


@tool(
    name="read_file",
    description=(
        "读取一个已知完整路径的 UTF-8/常见中文编码文本文件。需要核对报告或笔记原文时使用；"
        "不要用它读可执行文件、二进制 Office 文件。.env、*.key、*.pem、私钥、"
        ".npmrc/.htpasswd 等凭据类文件会被拒绝（清单可在 console.yaml 配置）。"
    ),
    category="file",
)
def read_file(path: str, max_chars: int = 8000) -> dict[str, Any]:
    """读取文本并回报完整绝对路径和截断状态。"""
    target = PATH_POLICY.require_allowed(path, write=False)
    if not target.exists() or not target.is_file():
        return {"ok": False, "reason": f"文件不存在：{target}", "path": str(target)}
    if _is_sensitive_read_target(target):
        return {"ok": False, "reason": describe_read_deny(target), "path": str(target)}
    bounded_chars = min(100000, max(100, int(max_chars)))
    data = target.read_bytes()
    if b"\x00" in data[:2048]:
        return {"ok": False, "reason": f"拒绝把二进制文件当文本读取：{target}", "path": str(target)}
    text = ""
    used_encoding = ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = data.decode(encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
    if not used_encoding:
        return {"ok": False, "reason": f"无法识别文本编码：{target}", "path": str(target)}
    truncated = len(text) > bounded_chars
    content = text[:bounded_chars]
    return {
        "ok": True,
        "path": str(target),
        "content": content,
        "encoding": used_encoding,
        "truncated": truncated,
        "text": f"已读取 {target}，返回 {len(content)} 字" + ("（已截断）" if truncated else ""),
    }


@tool(
    name="write_file",
    description=(
        "把文本写到知识库、系统桌面、总控台工作目录或用户手动授权目录。"
        "写入白名单外一律拒绝，即使信任模式开启；成功必须回报完整绝对路径与行数。"
    ),
    category="file",
)
def write_file(path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
    """原子写入文本文件，防止中途失败留下半文件。"""
    target = PATH_POLICY.require_allowed(path, write=True)
    if target.exists() and target.is_dir():
        return {"ok": False, "reason": f"目标是目录，不能写成文件：{target}", "path": str(target)}
    if target.exists() and not overwrite:
        return {"ok": False, "reason": f"文件已存在且 overwrite=false：{target}", "path": str(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(str(content), encoding="utf-8", newline="\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    line_count = len(str(content).splitlines())
    return {
        "ok": True,
        "path": str(target),
        "lines": line_count,
        "bytes": target.stat().st_size,
        "text": f"已写入完整路径：{target}（{line_count} 行，{target.stat().st_size} 字节）",
    }


@tool(
    name="delete_file",
    description=(
        "删除一个具体文件。仅在用户明确要求删除且已经定位到完整路径时使用；不能删目录、不能批量删。"
        "执行前必经确认门，弹窗逐行显示完整绝对路径。"
    ),
    category="file",
    destructive=True,
)
def delete_file(path: str) -> dict[str, Any]:
    """删除白名单内单个文件并回报完整路径。"""
    target = PATH_POLICY.require_allowed(path, write=True)
    if not target.exists():
        return {"ok": False, "reason": f"文件不存在，未删除：{target}", "path": str(target)}
    if not target.is_file():
        return {"ok": False, "reason": f"拒绝删除目录或非普通文件：{target}", "path": str(target)}
    target.unlink()
    return {"ok": True, "path": str(target), "text": f"已删除文件：{target}"}
