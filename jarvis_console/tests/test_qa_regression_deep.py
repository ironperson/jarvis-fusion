"""QA 第 2 轮深度回归：按 team-lead 清单逐项验证 5 条修复，并攻击配置开关。

重点不是"断言变绿"，而是走真实代码路径 + 双向验证（拦得住 且 不误伤）。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from jarvis_console.core.llm_client import DeepSeekClient, MockLLMClient, OllamaClient, _parse_tool_calls
from jarvis_console.core.safety import PathPolicy, SafetyGate
from jarvis_console.core.tool_registry import REGISTRY
from jarvis_console.core.types import ToolCall, TurnContext


class _FakeResponse:
    def __init__(self, data: Any) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._data


class _FakeSession:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.cursor = 0

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        del url, kwargs
        item = self.script[self.cursor]
        self.cursor += 1
        return _FakeResponse(item)

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        del url, kwargs
        return _FakeResponse({"models": []})


# ===================================================== ① close_app 口径一致性


@pytest.mark.parametrize("name", ["记事本", "计算器", "python", "不存在的软件xyz", "edge", "cmd"])
def test_close_app_tool_and_dialog_share_identical_path_resolution(name: str) -> None:
    """工程师声称「工具返回值与弹窗显示口径一致」。实测：
    tools/systemtools.py 用 SafetyGate._resolve_executable_path(command)，
    core/safety.py 兜底用同一个函数 —— 两边必须产出完全相同的字符串。
    （不敢真杀进程，故只比对解析结果，不调用 close_app 本体。）"""
    from jarvis.skills.system import resolve_app

    command, _display = resolve_app(name)
    tool_side = SafetyGate._resolve_executable_path(command)
    dialog_target = SafetyGate().resolve_targets("close_app", {"name": name})[0]
    dialog_side = str(dialog_target["path"])

    assert isinstance(dialog_target["pid"], int), f"{name}: pid 不是 int"
    assert isinstance(dialog_target.get("pid_display"), str), f"{name}: 缺 pid_display"

    # 未命中进程（pid == -1）时两边都走 _resolve_executable_path，必须完全一致
    if dialog_target["pid"] == -1:
        assert dialog_side == tool_side, (
            f"{name}: 弹窗显示 {dialog_side!r} 与工具返回 {tool_side!r} 口径不一致"
        )
    # 契约：两侧都不允许出现裸文件名
    for label, side in (("弹窗", dialog_side), ("工具", tool_side)):
        assert Path(side).is_absolute() or side.startswith(("未能定位可执行文件", "路径不可读")), (
            f"{name}: {label}侧出现裸文件名 {side!r}"
        )


def test_resolve_executable_path_protocol_passthrough() -> None:
    """ms-settings: / http(s) 必须原样返回，不能被 shutil.which 污染。"""
    assert SafetyGate._resolve_executable_path("ms-settings:display") == "ms-settings:display"
    assert SafetyGate._resolve_executable_path("https://x/app") == "https://x/app"
    assert SafetyGate._resolve_executable_path("http://x/app") == "http://x/app"


# ===================================================== ③ tool_call 过滤


def test_ghost_tool_calls_all_shapes_filtered() -> None:
    """非 dict 元素、无 function、function 非 dict、name 仅空白 —— 全部滤掉且不误杀。"""
    calls = _parse_tool_calls({
        "tool_calls": [
            None,
            "not-a-dict",
            123,
            {"id": "no_function"},
            {"id": "fn_not_dict", "function": "oops"},
            {"id": "blank", "function": {"name": "   ", "arguments": "{}"}},
            {"id": "none_name", "function": {"name": None}},
            {"id": "good", "function": {"name": "ping", "arguments": '{"a": 1}'}},
        ]
    })
    assert [c.name for c in calls] == ["ping"], f"过滤结果不符：{calls}"


# ===================================================== ④ usage 夹紧（三客户端）


def test_usage_clamped_on_deepseek() -> None:
    payload = {"choices": [{"message": {"content": "x"}}],
               "usage": {"prompt_tokens": -5, "completion_tokens": None}}
    r = DeepSeekClient("k", session=_FakeSession([payload])).chat([{"role": "user", "content": "x"}], [])
    assert r.prompt_tokens == 0 and r.completion_tokens == 0


def test_usage_clamped_on_ollama() -> None:
    payload = {"message": {"role": "assistant", "content": "x"},
               "prompt_eval_count": -3, "eval_count": "oops"}
    r = OllamaClient(session=_FakeSession([payload])).chat([{"role": "user", "content": "x"}], [])
    assert r.prompt_tokens == 0, f"Ollama 负数未夹紧：{r.prompt_tokens}"
    assert r.completion_tokens == 0, f"Ollama 非数字未兜底：{r.completion_tokens}"


def test_usage_clamped_on_mock() -> None:
    r = MockLLMClient(script=[{"message": {"role": "assistant", "content": "ok"},
                               "usage": {"prompt_tokens": -5, "completion_tokens": -9}}]
                      ).chat([{"role": "user", "content": "x"}], [])
    assert r.prompt_tokens == 0 and r.completion_tokens == 0


# ===================================================== ⑤ read_file 拒绝清单


DENY_NAMES = [
    ".env", ".env.local", ".ENV", "id_rsa", "id_ed25519", "id_ecdsa",
    ".npmrc", ".pypirc", ".netrc", "credentials", "secrets.json", "token.json",
    "server.pem", "private.key", "bundle.p12", "client.pfx", ".htpasswd",
]


@pytest.mark.parametrize("name", DENY_NAMES)
def test_denylisted_files_are_refused_with_explicit_reason(name: str) -> None:
    """拒绝侧：必须 ok=False，且给出明确中文原因（不能静默返回空内容）。"""
    import jarvis_console.tools  # noqa: F401

    slot = Path(tempfile.mkdtemp(prefix="qa_deny_"))
    target = slot / name
    target.write_text("SECRET=plain-text-should-never-leak", encoding="utf-8")
    result = REGISTRY.invoke(ToolCall("c", "read_file", {"path": str(target)}), TurnContext("read"))
    assert result.ok is False, f"{name} 未被拒绝"
    assert "plain-text-should-never-leak" not in result.text, f"{name} 内容泄漏"
    reason = f"{result.error}{result.text}"
    assert "拒绝读取" in reason, f"{name} 未给出明确原因：{reason[:120]}"
    assert reason.strip(), f"{name} 原因是空的（静默失败）"


@pytest.mark.parametrize("name", ["笔记.md", "清单.txt", "report.csv", "settings.json",
                                  "README.markdown", "config.yaml", "environment.md",
                                  "my_credentials.md", "token笔记.txt"])
def test_normal_files_still_readable(name: str) -> None:
    """误伤侧（关键）：正常文件必须照常可读，不得被清单子串误伤。"""
    import jarvis_console.tools  # noqa: F401

    slot = Path(tempfile.mkdtemp(prefix="qa_ok_"))
    target = slot / name
    body = "这是一份普通笔记，不含任何密钥。"
    target.write_text(body, encoding="utf-8")
    result = REGISTRY.invoke(ToolCall("c", "read_file", {"path": str(target)}), TurnContext("read"))
    assert result.ok is True, f"正常文件被误伤：{name} -> {result.error}"
    assert "普通笔记" in result.data["content"], f"{name} 内容读取异常"


def test_find_files_hides_secrets_and_reports_count() -> None:
    """find_files 必须隐藏密钥文件并如实提示「已隐藏 N 个」。"""
    import jarvis_console.tools  # noqa: F401

    slot = Path(tempfile.mkdtemp(prefix="qa_list_"))
    for name in (".env", "id_rsa", "secrets.json"):
        (slot / name).write_text("SECRET", encoding="utf-8")
    (slot / "报告.md").write_text("正文", encoding="utf-8")

    result = REGISTRY.invoke(
        ToolCall("c", "find_files", {"pattern": "*", "root": str(slot), "limit": 20}),
        TurnContext("list"),
    )
    assert result.ok
    for name in (".env", "id_rsa", "secrets.json"):
        assert name not in result.text, f"{name} 未被隐藏：{result.text}"
    assert "已隐藏 3 个" in result.text, f"未如实提示隐藏数量：{result.text}"
    assert "报告.md" in result.text, "正常文件被误隐藏"


def test_config_denylist_key_exists_and_narrowing_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置键存在，且改成更窄的清单后真的生效（不是摆设）。"""
    from jarvis_console import config as cfg
    from jarvis_console.config import ConsoleConfig, SafetyConfig

    real = cfg.get_config()
    assert real.safety.read_denylist, "console.yaml 的 safety.read_denylist 未解析出来"

    monkeypatch.setattr(cfg, "_CONFIG_CACHE", ConsoleConfig(safety=SafetyConfig(read_denylist=[".env"])))
    policy = PathPolicy()
    assert policy.is_sensitive_read(Path(".env")) is True, "窄清单未生效"
    assert policy.is_sensitive_read(Path("id_rsa")) is False, "窄清单应放开 id_rsa"


@pytest.mark.xfail(
    strict=False,
    reason="源码 Bug：core/safety.py::_load_read_denylist 用 `if configured` 判空，"
           "配置里把 read_denylist 置为 [] 会静默回落到 DEFAULT_READ_DENYLIST，放开通道失效",
)
def test_config_denylist_can_be_emptied_to_open_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """team-lead 拍板：保持直接拒绝，但 config/console.yaml 必须留一个能放开的键。
    实测：键存在、非空清单生效，但**置空不生效** —— 用户把清单清空后仍然被拒。"""
    from jarvis_console import config as cfg
    from jarvis_console.config import ConsoleConfig, SafetyConfig, DEFAULT_READ_DENYLIST

    monkeypatch.setattr(cfg, "_CONFIG_CACHE", ConsoleConfig(safety=SafetyConfig(read_denylist=[])))
    assert cfg.get_config().safety.read_denylist == []
    policy = PathPolicy()
    assert policy.read_denylist != list(DEFAULT_READ_DENYLIST), (
        "置空后仍回落到默认清单，放开通道失效"
    )
    assert policy.is_sensitive_read(Path(".env")) is False, "置空后 .env 仍被拒绝"


def test_constructor_level_empty_denylist_works() -> None:
    """构造参数级置空是生效的，可作为用户自救通道（config 级失效时的兜底）。"""
    policy = PathPolicy(read_denylist=[])
    assert policy.is_sensitive_read(Path(".env")) is False
