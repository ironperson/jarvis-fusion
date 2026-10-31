from __future__ import annotations

from jarvis_console.core.tool_registry import REGISTRY, ToolRegistry
from jarvis_console.core.types import ToolCall, TurnContext


def test_schema_generated_from_type_hints_and_defaults() -> None:
    registry = ToolRegistry()

    @registry.register("demo", "demo tool", "test")
    def demo(name: str, count: int = 2, tags: list[str] | None = None) -> str:
        return f"{name}:{count}:{tags or []}"

    schema = registry.get("demo").to_openai_schema()["function"]["parameters"]
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["count"]["default"] == 2
    assert schema["required"] == ["name"]
    result = registry.invoke(ToolCall("c1", "demo", {"name": "x"}), TurnContext("test"))
    assert result.ok is True
    assert result.text.startswith("x:2")


def test_registry_converts_exceptions_to_result() -> None:
    registry = ToolRegistry()

    @registry.register("explode", "explode", "test")
    def explode() -> str:
        raise ValueError("boom")

    result = registry.invoke(ToolCall("c2", "explode", {}), TurnContext("test"))
    assert result.ok is False
    assert "boom" in result.error


def test_first_batch_has_exactly_twelve_tools_and_two_destructive() -> None:
    import jarvis_console.tools  # noqa: F401

    # 12 个核心工具 + 3 个日程/提醒工具（schedule 插件，融合版新增；落盘 ~/.jarvis/reminders.json）。
    expected = {
        "search_bilibili", "web_search", "fetch_page", "knowledge_search", "ingest_note",
        "system_status", "list_processes", "close_app", "find_files", "read_file",
        "write_file", "delete_file",
        "set_reminder", "list_reminders", "cancel_reminder",
    }
    assert set(REGISTRY.names()) == expected
    destructive = {spec.name for spec in REGISTRY.enabled_tools() if spec.destructive}
    assert destructive == {"close_app", "delete_file"}
