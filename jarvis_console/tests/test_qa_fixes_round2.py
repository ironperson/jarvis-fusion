"""QA 第 2 轮：验证工程师对 4 条缺陷的修复，并攻击**新增代码**引入的新问题。

原则：修复本身是否正确 ≠ 修复有没有带来新问题。本模块两者都查。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from jarvis_console.core.llm_client import DeepSeekClient, OllamaClient
from jarvis_console.core.safety import ConfirmationGate, PathPolicy, SafetyGate
from jarvis_console.core.types import TurnContext


class _FakeResponse:
    def __init__(self, data: Any, ok: bool = True) -> None:
        self._data = data
        self.ok = ok

    def raise_for_status(self) -> None:
        if not self.ok:
            import requests

            raise requests.HTTPError("http error")

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


# ======================================================== 修复① close_app 绝对路径


@pytest.mark.parametrize("raw", [
    "notepad.exe",
    "cmd.exe",
    "C:/Windows/System32/notepad.exe",
    "ms-settings:display",
    "https://example.com/app",
    "",
    "definitely-not-a-real-app-xyz.exe",
])
def test_resolve_executable_path_never_returns_bare_filename(raw: str) -> None:
    """核心契约：返回值要么是绝对路径 / 协议串，要么是诚实的"未能定位"，绝不回显裸文件名。"""
    result = SafetyGate._resolve_executable_path(raw)
    if raw.startswith(("ms-settings:", "http://", "https://")):
        assert result == raw
        return
    if not raw:
        assert result == "未能定位可执行文件", f"空输入返回了：{result!r}"
        return
    if result.startswith("未能定位可执行文件"):
        assert raw in result, "未定位提示未带上原输入"
        assert not Path(result).is_absolute()
        return
    assert Path(result).is_absolute(), f"返回了非绝对路径：{result!r}（输入 {raw!r}）"


def test_close_app_confirm_payload_path_is_absolute_or_honest() -> None:
    """close_app 弹窗载荷的 path 必须是绝对路径或明确说明，且 pid 统一为 int。"""
    safety = SafetyGate(PathPolicy(allowed_roots=[], sensitive_dirs=[]), ConfirmationGate())
    for name in ("记事本", "计算器", "notepad", "python", "不存在的软件xyz"):
        targets = safety.resolve_targets("close_app", {"name": name})
        assert targets, f"{name} 未返回任何目标"
        for item in targets:
            assert isinstance(item["pid"], int), f"{name}: pid 不是 int -> {item['pid']!r}"
            path = str(item["path"])
            assert Path(path).is_absolute() or path.startswith("未能定位可执行文件"), (
                f"{name}: 弹窗会显示非绝对路径 {path!r}"
            )


@pytest.mark.xfail(strict=False, reason="修复①引入的回归：弹窗渲染用 pid，未找到时显示 PID -1")
def test_confirm_dialog_shows_human_readable_pid_when_not_found() -> None:
    """修复把 pid 统一成 int(-1) 并新增 pid_display='未找到'，
    但 ui/confirm_dialog.py:_format_target 仍读 pid -> str(-1 or '未知') == '-1'。
    用户会看到「PID -1」，比修复前的「PID 未找到」更差。"""
    from jarvis_console.ui.confirm_dialog import _format_target

    target = {"name": "notepad.exe", "pid": -1, "pid_display": "未找到", "path": "C:/x/notepad.exe"}
    rendered = _format_target(target, 1)
    assert "-1" not in rendered, f"弹窗渲染出无意义的 PID：{rendered}"


# ======================================================== 修复② error 信封


def test_deepseek_error_envelope_is_surfaced() -> None:
    session = _FakeSession([{"error": {"message": "Insufficient balance", "code": "402"}}])
    with pytest.raises(Exception) as info:
        DeepSeekClient("k", session=session).chat([{"role": "user", "content": "x"}], [])
    assert "Insufficient balance" in str(info.value)


def test_ollama_error_envelope_is_surfaced() -> None:
    session = _FakeSession([{"error": "model 'qwen3-14b' not found"}])
    with pytest.raises(Exception) as info:
        OllamaClient(session=session).chat([{"role": "user", "content": "x"}], [])
    assert "qwen3-14b" in str(info.value)


def test_error_envelope_with_empty_message_falls_through() -> None:
    """error 存在但 message 为空时，必须回落到原有协议错误，不能抛空消息异常。"""
    session = _FakeSession([{"error": {}, "choices": []}])
    with pytest.raises(Exception) as info:
        DeepSeekClient("k", session=session).chat([{"role": "user", "content": "x"}], [])
    assert str(info.value).strip(), "抛出了空消息异常"


# ======================================================== 修复③ 幽灵 tool_call


def test_nameless_tool_call_is_dropped_but_valid_ones_kept() -> None:
    payload = {"choices": [{
        "message": {"content": "x", "tool_calls": [
            {"id": "ghost"},
            {"id": "ghost2", "function": {}},
            {"id": "ghost3", "function": {"name": "   "}},
            {"id": "good", "function": {"name": "ping", "arguments": "{}"}},
        ]},
        "finish_reason": "tool_calls",
    }]}
    response = DeepSeekClient("k", session=_FakeSession([payload])).chat([{"role": "user", "content": "x"}], [])
    assert [c.name for c in response.tool_calls] == ["ping"], f"过滤结果不符：{response.tool_calls}"


# ======================================================== 修复④ usage 夹紧


def test_negative_and_garbage_usage_are_clamped() -> None:
    payload = {"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": -5, "completion_tokens": "abc"}}
    response = DeepSeekClient("k", session=_FakeSession([payload])).chat([{"role": "user", "content": "x"}], [])
    assert response.prompt_tokens == 0, f"负数未夹紧：{response.prompt_tokens}"
    assert response.completion_tokens == 0, f"非数字未兜底：{response.completion_tokens}"


# ======================================================== 新增：密钥文件读拒绝


def test_env_files_are_blocked_case_insensitively() -> None:
    from jarvis_console.tools import filetools

    slot = Path(tempfile.mkdtemp(prefix="qa_secret_"))
    for name in (".env", ".ENV", ".env.local", ".env.production"):
        target = slot / name
        target.write_text("DEEPSEEK_API_KEY=sk-leak", encoding="utf-8")
        result = filetools.read_file(str(target))
        assert result["ok"] is False, f"{name} 未被拦截，密钥可被读入上下文"
        assert "sk-leak" not in str(result)


def test_other_private_key_formats_are_blocked() -> None:
    """SSH 私钥除 id_rsa/id_ed25519 还有 id_ecdsa/id_dsa；TLS 私钥常见 .pem/.key/.p12/.pfx。
    实测：拒绝清单为 fnmatch 文件名模式，这些均已覆盖。"""
    from jarvis_console.tools import filetools

    slot = Path(tempfile.mkdtemp(prefix="qa_secret_"))
    leaked = []
    for name in ("id_ecdsa", "id_dsa", "server.pem", "private.key", "cert.p12", "keystore.pfx", ".env.local"):
        target = slot / name
        target.write_text("PRIVATE KEY MATERIAL", encoding="utf-8")
        if filetools.read_file(str(target))["ok"] is True:
            leaked.append(name)
    assert not leaked, f"以下私钥文件可被读出并发往云端：{leaked}"


def test_benign_files_are_still_readable() -> None:
    """拒绝清单用 fnmatch 整名匹配而非子串匹配，正常文档不得被误伤。
    （若退化成 `token in name` 子串匹配，environment.md / my_credentials.md 就会被误拒。）"""
    from jarvis_console.tools import filetools

    slot = Path(tempfile.mkdtemp(prefix="qa_secret_"))
    blocked = []
    for name in ("environment.md", "dotenv_notes.md", "my_credentials.md", "notes_about_dotenv.txt", "token.json.md"):
        target = slot / name
        target.write_text("这是一篇关于环境变量配置的普通笔记。", encoding="utf-8")
        if filetools.read_file(str(target))["ok"] is False:
            blocked.append(name)
    assert not blocked, f"以下正常文件被误拒：{blocked}"


def test_ssh_directory_rule_blocks_unlisted_files() -> None:
    """~/.ssh/ 目录规则必须生效：即使文件名不在清单里（如 config），整个目录都拒绝读。"""
    from jarvis_console.core.safety import PathPolicy as _PP

    policy = _PP()
    assert policy.is_sensitive_read(Path.home() / ".ssh" / "config") is True, "~/.ssh 目录规则未生效"
    assert policy.is_sensitive_read(Path.home() / ".ssh" / "known_hosts") is True
    # 目录外同名文件不应被误伤
    outside = Path(tempfile.mkdtemp(prefix="qa_secret_")) / "config"
    assert policy.is_sensitive_read(outside) is False, "目录规则误伤了 ~/.ssh 之外的文件"


@pytest.mark.xfail(
    strict=False,
    reason="遗留缺口：.git-credentials / *.keystore / *.jks 未入清单，可被读出并发往云端",
)
def test_git_credentials_and_keystores_are_blocked() -> None:
    """`.git-credentials` 是纯文本存放 git 账号密码的标准文件；
    `*.keystore`/`*.jks` 是 Java 密钥库。当前 fnmatch 整名匹配均不命中。"""
    from jarvis_console.tools import filetools

    slot = Path(tempfile.mkdtemp(prefix="qa_secret_"))
    leaked = []
    for name in (".git-credentials", "app.keystore", "my.jks", "serviceAccountKey.json"):
        target = slot / name
        target.write_text("https://user:password@github.com", encoding="utf-8")
        if filetools.read_file(str(target))["ok"] is True:
            leaked.append(name)
    assert not leaked, f"以下凭据文件可被读出并发往云端：{leaked}"


@pytest.mark.xfail(
    strict=False,
    reason="新增代码不一致：find_files 只在 '*' 列举分支过滤，模糊搜索分支未过滤",
)
def test_find_files_fuzzy_search_also_hides_secrets() -> None:
    """工程师声称 find_files 隐藏密钥文件，但过滤只加在 pattern in {'', '*'} 的列举分支；
    走 existing_find 的模糊搜索分支会把 .env / id_rsa 的路径暴露给模型。"""
    from jarvis_console.tools import filetools

    slot = Path(tempfile.mkdtemp(prefix="qa_secret_"))
    (slot / ".env").write_text("KEY=1", encoding="utf-8")
    (slot / "id_rsa").write_text("KEY=2", encoding="utf-8")
    (slot / "notes.md").write_text("hello", encoding="utf-8")

    listing = filetools.find_files("*", root=str(slot))
    assert ".env" not in listing, f"列举分支未隐藏 .env：{listing}"

    fuzzy = filetools.find_files("env", root=str(slot))
    assert ".env" not in fuzzy, f"模糊搜索分支暴露了 .env：{fuzzy}"


def test_secret_block_does_not_affect_write_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """读拒绝不应波及写入/删除，否则用户没法管理自己白名单内的 .env。"""
    from jarvis_console.tools import filetools

    slot = Path(tempfile.mkdtemp(prefix="qa_secret_"))
    root = slot / "ws"
    root.mkdir()
    monkeypatch.setenv("JARVIS_MOCK_DIR", str(root))
    monkeypatch.setattr(filetools, "PATH_POLICY", PathPolicy(allowed_roots=[root], sensitive_dirs=[]))
    written = filetools.write_file(str(root / ".env"), "A=1")
    assert written["ok"] is True, f"写入被误伤：{written}"
    assert filetools.delete_file(str(root / ".env"))["ok"] is True, "删除被误伤"
