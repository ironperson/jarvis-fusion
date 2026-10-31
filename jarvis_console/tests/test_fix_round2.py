"""team-lead 指派的 5 项修复的回归测试。

原则（针对 QA 指出的「只测自己造的假对象」毛病）：
- 一律走**真实代码路径**：真实 REGISTRY.invoke、真实 SafetyGate、真实 DeepSeekClient/OllamaClient
  的 chat 与序列化管线，不用绕过解析层的私有捷径。
- 断言「真实暴露问题」的现象：弹窗文本、抛出的错误消息、幽灵调用是否消失、
  负数是否被夹紧、密钥内容是否泄漏。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from jarvis_console.core.errors import LLMError
from jarvis_console.core.llm_client import DeepSeekClient, MockLLMClient, OllamaClient
from jarvis_console.core.safety import ConfirmationGate, PathPolicy, SafetyGate
from jarvis_console.core.tool_registry import REGISTRY
from jarvis_console.core.types import ToolCall, TurnContext


# ───────────────────────── 1. close_app 兜底必须是绝对路径 ─────────────────────────
def test_close_app_fallback_target_is_absolute_and_pid_is_int() -> None:
    """真实路径：SafetyGate.resolve_targets → 弹窗 targets。

    缺陷现象：未命中进程时 path 是裸文件名（notepad.exe/python），pid 是字符串。
    """
    # 短超时：避免真实确认门在无人应答时长期阻塞
    safety = SafetyGate(PathPolicy(), ConfirmationGate(), confirmation_timeout=0.2)
    targets = safety.resolve_targets("close_app", {"name": "jarvis_missing_app_993847.exe"})
    assert targets, "未命中进程时也要有可展示的目标"
    first = targets[0]
    assert isinstance(first, dict)
    path_text = str(first["path"])
    # 要么拿到真实绝对路径，要么明确说明未能定位；绝不回显裸文件名
    assert Path(path_text).is_absolute() or "未能定位" in path_text, f"仍是裸文件名：{path_text}"
    assert not path_text.endswith("jarvis_missing_app_993847.exe")
    assert isinstance(first["pid"], int), "pid 必须统一为 int"
    assert isinstance(first["pid_display"], str)

    # 真实工具返回体同样不得出现裸文件名（走真实 REGISTRY.invoke 与确认门）
    result = REGISTRY.invoke(
        ToolCall("c1", "close_app", {"name": "jarvis_missing_app_993847.exe"}),
        TurnContext("close", gate=safety),
    )
    assert result.ok is False and result.cancelled is True, "无人确认时必须按取消处理而非执行"
    assert "jarvis_missing_app_993847.exe PID" not in result.text


def test_close_app_real_mapped_app_still_absolute_or_explicit() -> None:
    """对 APP_MAP 里的裸文件名（如 记事本 → notepad.exe）同样不得回显裸名。"""
    safety = SafetyGate(PathPolicy(), ConfirmationGate(), confirmation_timeout=0.2)
    targets = safety.resolve_targets("close_app", {"name": "记事本"})
    path_text = str(targets[0]["path"])
    assert path_text.endswith("notepad.exe") is False or Path(path_text).is_absolute()


# ───────────────────────── 2. error 信封必须透传 ─────────────────────────
def test_deepseek_error_envelope_surfaces_real_reason() -> None:
    """真实路径：DeepSeekClient.chat → _post → _openai_response。"""
    class FakeResponse:
        def __init__(self, data: dict[str, Any]) -> None:
            self._data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._data

    class FakeSession:
        def __init__(self, data: dict[str, Any]) -> None:
            self._data = data
            self.payload: dict[str, Any] = {}

        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            del url
            self.payload = dict(kwargs.get("json") or {})
            return FakeResponse(self._data)

    session = FakeSession({"error": {"message": "Insufficient balance", "type": "insufficient_quota"}})
    client = DeepSeekClient("unit-test-key", session=session)
    with pytest.raises(LLMError) as caught:
        client.chat([{"role": "user", "content": "帮我查资料"}], [])
    message = str(caught.value)
    assert "Insufficient balance" in message
    assert "缺少 choices" not in message


def test_ollama_error_envelope_surfaces_real_reason() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"error": "model 'qwen3-14b' not found"}

    class FakeSession:
        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            del url, kwargs
            return FakeResponse()

    with pytest.raises(LLMError) as caught:
        OllamaClient(session=FakeSession()).chat([{"role": "user", "content": "x"}], [])
    assert "not found" in str(caught.value)


# ───────────────────────── 3. 空名 tool_call 必须被过滤 ─────────────────────────
def test_ghost_tool_call_does_not_reach_registry() -> None:
    """真实路径：MockLLMClient.chat（走 _parse_tool_calls）→ AgentLoop 不会拿它去 invoke。"""
    client = MockLLMClient(script=[{
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "ghost"},
                {"id": "blank", "function": {"name": "  ", "arguments": "{}"}},
                {"id": "real", "function": {"name": "system_status", "arguments": "{}"}},
            ],
        },
        "finish_reason": "tool_calls",
    }])
    response = client.chat([{"role": "user", "content": "x"}], REGISTRY.openai_schemas())
    names = [call.name for call in response.tool_calls]
    assert names == ["system_status"], f"幽灵调用未过滤：{names}"
    assert all(call.name.strip() for call in response.tool_calls)


def test_ghost_tool_call_would_have_become_tool_not_found() -> None:
    """反证：未过滤时空名调用确实会白耗一轮（保证上面的断言有意义）。"""
    result = REGISTRY.invoke(ToolCall("ghost", "", {}), TurnContext("x"))
    assert result.ok is False
    assert "不存在或未启用" in result.error


# ───────────────────────── 4. usage 负数必须夹紧 ─────────────────────────
def test_negative_usage_clamped_on_deepseek_path() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": -5, "completion_tokens": -9},
            }

    class FakeSession:
        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            del url, kwargs
            return FakeResponse()

    response = DeepSeekClient("k", session=FakeSession()).chat([{"role": "user", "content": "x"}], [])
    assert response.prompt_tokens == 0 and response.completion_tokens == 0


def test_negative_usage_clamped_on_ollama_and_mock_paths() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"message": {"role": "assistant", "content": "ok"}, "prompt_eval_count": -3, "eval_count": 7}

    class FakeSession:
        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            del url, kwargs
            return FakeResponse()

    ollama = OllamaClient(session=FakeSession()).chat([{"role": "user", "content": "x"}], [])
    assert ollama.prompt_tokens == 0 and ollama.completion_tokens == 7

    mock = MockLLMClient(script=[{
        "message": {"role": "assistant", "content": "ok"},
        "usage": {"prompt_tokens": -1, "completion_tokens": -2},
    }])
    mocked = mock.chat([{"role": "user", "content": "x"}], [])
    assert mocked.prompt_tokens == 0 and mocked.completion_tokens == 0


# ───────────────────────── 5. 读取拒绝清单（配置化） ─────────────────────────
def test_read_file_refuses_every_denylisted_secret(tmp_path: Path) -> None:
    """真实路径：REGISTRY.invoke(real read_file)；覆盖 team-lead 点名的全部条目。"""
    import jarvis_console.tools  # noqa: F401

    secret_material = "DEEPSEEK_API_KEY=sk-plain-text-should-never-leak"
    denied_names = [
        ".env", ".env.local", "server.key", "cert.pem", "bundle.p12", "client.pfx",
        "id_rsa", "id_rsa.pub", "id_ed25519", "id_ed25519.pub", "id_dsa", "id_ecdsa",
        ".npmrc", ".pypirc", ".netrc", ".htpasswd", "credentials", "credentials.json",
        "secrets.json", "token.json",
    ]
    for name in denied_names:
        target = tmp_path / name
        target.write_text(secret_material, encoding="utf-8")
        result = REGISTRY.invoke(ToolCall(f"r-{name}", "read_file", {"path": str(target)}), TurnContext("read"))
        assert result.ok is False, f"{name} 未被拒绝"
        assert "sk-plain-text-should-never-leak" not in result.text
        assert "拒绝读取密钥/凭据类文件" in result.text or "拒绝读取密钥/凭据类文件" in result.error


def test_read_file_refuses_everything_under_real_ssh_dir() -> None:
    """真实路径：真实 HOME 下的 ~/.ssh/（不伪造、不落盘，纯读策略断言）。

    ~/.ssh 下任意文件都拒绝（含 config / known_hosts）；同名文件放在普通目录应放行。
    """
    import tempfile

    from jarvis_console.paths import HOME_SSH_DIR

    policy = PathPolicy()
    for name in ("config", "known_hosts", "id_rsa"):
        assert policy.is_sensitive_read(HOME_SSH_DIR / name) is True, f"~/.ssh/{name} 必须被拒绝"
    with tempfile.TemporaryDirectory() as td:
        for name in ("config", "known_hosts"):
            normal = Path(td) / name
            normal.write_text("Host x\n", encoding="utf-8")
            assert policy.is_sensitive_read(normal) is False, f"普通目录的 {name} 被误伤"


def test_normal_files_still_readable_no_false_positive(tmp_path: Path) -> None:
    """拒清单必须窄而准：知识库 md、桌面文档、报告一律照常可读。"""
    import jarvis_console.tools  # noqa: F401

    normal = {
        "笔记.md": "# 知识管理\n正文",
        "清单.txt": "AI 视频清单",
        "report.csv": "a,b\n1,2",
        "README.markdown": "说明",
        "settings.yaml": "ui: {}",
    }
    for name, body in normal.items():
        target = tmp_path / name
        target.write_text(body, encoding="utf-8")
        result = REGISTRY.invoke(ToolCall(f"n-{name}", "read_file", {"path": str(target)}), TurnContext("read"))
        assert result.ok is True, f"正常文件被误伤：{name} -> {result.text}"
        expected = body.replace("\n", os.linesep) if os.linesep != "\n" else body
        assert expected in result.data["content"] or body in result.data["content"], (
            f"{name} 正文不一致：{result.data['content'][:80]!r}"
        )


def test_find_files_hides_denylisted_secrets(tmp_path: Path) -> None:
    import jarvis_console.tools  # noqa: F401

    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-leak", encoding="utf-8")
    (tmp_path / "id_rsa").write_text("PRIVATE KEY", encoding="utf-8")
    (tmp_path / "报告.md").write_text("# 报告", encoding="utf-8")
    result = REGISTRY.invoke(
        ToolCall("f1", "find_files", {"pattern": "*", "root": str(tmp_path), "limit": 20}),
        TurnContext("list"),
    )
    assert result.ok
    assert str(tmp_path / ".env") not in result.text
    assert str(tmp_path / "id_rsa") not in result.text
    assert "已隐藏" in result.text
    assert str(tmp_path / "报告.md") in result.text


def test_denylist_is_configurable(tmp_path: Path) -> None:
    """清单可配置：显式传空清单即可放开（用户自救通道）。"""
    policy = PathPolicy(read_denylist=[])
    assert policy.is_sensitive_read(tmp_path / ".env") is False
    default_policy = PathPolicy(read_denylist=[".env", "*.pem", "~/.ssh/"])
    assert default_policy.is_sensitive_read(tmp_path / ".env") is True
    assert default_policy.is_sensitive_read(tmp_path / "server.pem") is True
    assert default_policy.is_sensitive_read(tmp_path / "笔记.md") is False
