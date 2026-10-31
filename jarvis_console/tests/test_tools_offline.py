from __future__ import annotations

from pathlib import Path

from jarvis_console.core.safety import ConfirmationGate, SafetyGate
from jarvis_console.core.tool_registry import REGISTRY
from jarvis_console.core.types import ToolCall, TurnContext


def _invoke(name: str, arguments: dict, context: TurnContext | None = None):
    return REGISTRY.invoke(ToolCall(f"call_{name}", name, arguments), context or TurnContext(name))


def test_twelve_tools_offline_with_fixtures(tmp_db: Path, tmp_path: Path) -> None:
    import jarvis_console.tools  # noqa: F401

    bili = _invoke("search_bilibili", {"keyword": "AI", "order": "click", "limit": 3})
    assert bili.ok and bili.data["results"][0]["play"] > 0

    web = _invoke("web_search", {"query": "AI 视频", "limit": 2})
    assert web.ok and len(web.data["results"]) == 2

    page = _invoke("fetch_page", {"url": "https://example.com/test", "max_chars": 2000})
    assert page.ok and "离线页面" in page.data["content"]

    ingested = _invoke("ingest_note", {
        "title": "离线工具测试",
        "content": "知识管理与 AI Agent 的离线测试内容",
        "source": "pytest",
        "tags": ["test"],
        "url": "",
    })
    assert ingested.ok and ingested.data["note_id"]

    searched = _invoke("knowledge_search", {"query": "知识管理", "top_k": 5})
    assert searched.ok and searched.data["count"] >= 1

    status = _invoke("system_status", {})
    assert status.ok and "CPU" in status.text

    processes = _invoke("list_processes", {"limit": 3})
    assert processes.ok and "正在运行" in processes.text

    root = Path(__import__("os").environ["JARVIS_MOCK_DIR"])
    written = _invoke("write_file", {"path": "sample.txt", "content": "line1\nline2", "overwrite": True})
    assert written.ok
    target = Path(written.data["path"])
    assert target == (root / "sample.txt").resolve()

    read = _invoke("read_file", {"path": str(target), "max_chars": 100})
    assert read.ok and "line1" in read.data["content"]

    found = _invoke("find_files", {"pattern": "*", "root": str(root), "limit": 10})
    assert found.ok and str(target) in found.text

    holder: dict[str, ConfirmationGate] = {}

    def approve(payload: dict) -> None:
        holder["gate"].resolve(True)

    confirm = ConfirmationGate(approve)
    holder["gate"] = confirm
    context = TurnContext("delete", gate=SafetyGate(confirm_gate=confirm, confirmation_timeout=0.2))
    deleted = _invoke("delete_file", {"path": str(target)}, context)
    assert deleted.ok and not target.exists()

    no_process_context = TurnContext("close", gate=SafetyGate(confirm_gate=confirm, confirmation_timeout=0.2))
    closed = _invoke("close_app", {"name": "__jarvis_no_such_process_993847.exe"}, no_process_context)
    assert closed.ok

    # 12 个核心工具 + 3 个日程/提醒工具（schedule 插件，融合版新增；见 test_tool_registry）。
    assert len(REGISTRY.names()) == 15
