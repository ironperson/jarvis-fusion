"""QA 独立攻击测试：写入白名单绕过。

设计说明：
1. 不用 pytest 的 tmp_path —— 沙箱禁止批量删除，pytest 回收旧临时目录会触发拦截。
   本模块自建 tempfile 目录且不做清理（pytest 结束后由用户自行删除）。
2. 工程师的测试把 JARVIS_MOCK_DIR 指向 tmp_path/mock_workspace，而
   PathPolicy._effective_write_roots() 会把该目录一并加进白名单，等于把整个
   tmp_path 开成白名单，测不出逃逸。本模块显式把 JARVIS_MOCK_DIR 收敛到
   allowed 目录内部，保证 outside 一定不在白名单里。
"""
from __future__ import annotations

import itertools
import os
import tempfile
from pathlib import Path

import pytest

from jarvis_console.core.errors import PathDenied
from jarvis_console.core.safety import ConfirmationGate, PathPolicy, SafetyGate
from jarvis_console.core.types import TurnContext

_SANDBOX_ROOT = Path(tempfile.mkdtemp(prefix="qa_whitelist_"))
_COUNTER = itertools.count()


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch):
    """每次调用分配一对全新目录：allowed（唯一白名单根）与 outside（必在白名单外）。"""
    slot = _SANDBOX_ROOT / f"case{next(_COUNTER)}"
    allowed = slot / "allowed"
    outside = slot / "outside"
    allowed.mkdir(parents=True)
    outside.mkdir(parents=True)
    (outside / "evil.txt").write_text("evil", encoding="utf-8")
    monkeypatch.setenv("JARVIS_MOCK_DIR", str(allowed))
    policy = PathPolicy(allowed_roots=[allowed], sensitive_dirs=[])
    return allowed, outside, policy


# (用例名, 路径构造, 是否必须被拒绝)
BYPASS_CASES = [
    ("plain_outside", lambda a, o: str(o / "evil.txt"), True),
    ("dotdot_escape", lambda a, o: str(a / ".." / "outside" / "evil.txt"), True),
    ("deep_dotdot", lambda a, o: str(a / "x" / "y" / ".." / ".." / ".." / "outside" / "evil.txt"), True),
    ("prefix_sibling", lambda a, o: str(a.parent / (a.name + "X") / "evil.txt"), True),
    ("trailing_dot", lambda a, o: str(o / "evil.txt."), True),
    ("trailing_space", lambda a, o: str(o / "evil.txt "), True),
    ("trailing_mixed", lambda a, o: str(o / "evil.txt. . "), True),
    ("unc_device_question", lambda a, o: "\\\\?\\" + str(o / "evil.txt"), True),
    ("unc_dot_prefix", lambda a, o: "\\\\.\\" + str(o / "evil.txt"), True),
    ("unc_admin_share", lambda a, o: "\\\\127.0.0.1\\c$\\evil.txt", True),
    ("forward_slashes", lambda a, o: str(o / "evil.txt").replace("\\", "/"), True),
    ("double_backslashes", lambda a, o: str(o) + "\\\\evil.txt", True),
    ("slash_dotdot_mix", lambda a, o: str(a).replace("\\", "/") + "/../outside/evil.txt", True),
    ("ads_stream", lambda a, o: str(o / "evil.txt") + ":stream", True),
    # 环境变量不会被展开：%TEMP% 变成白名单内的字面目录名，无法逃逸
    ("env_var_not_expanded", lambda a, o: "%TEMP%\\outside\\evil.txt", False),
    ("relative_escape", lambda a, o: "../outside/evil.txt", True),
    ("absolute_inside", lambda a, o: str(a / "good.txt"), False),
    ("dotdot_returns_inside", lambda a, o: str(a / "sub" / ".." / "good.txt"), False),
]


@pytest.mark.parametrize("label,builder,must_deny", BYPASS_CASES, ids=[c[0] for c in BYPASS_CASES])
def test_write_whitelist_bypass_attempts(sandbox, label: str, builder, must_deny: bool) -> None:
    allowed, outside, policy = sandbox
    raw = builder(allowed, outside)
    verdict = policy.is_allowed(raw, write=True)
    if must_deny:
        assert verdict is False, f"白名单被绕过：{label} -> {raw}，规范化为 {policy.normalize(raw)}"
    else:
        assert verdict is True, f"白名单内路径被误拒：{label} -> {raw}"


def test_symlink_inside_whitelist_fails_closed(sandbox) -> None:
    """白名单内的软链接指向白名单外时，必须解析后被拒（失败关闭，而不是指向哪里就写哪里）。"""
    allowed, outside, policy = sandbox
    link = allowed / "link.txt"
    try:
        os.symlink(outside / "evil.txt", link)
    except (OSError, NotImplementedError, PermissionError) as exc:
        pytest.skip(f"本机无法创建符号链接：{exc}")
    assert policy.normalize(link) == (outside / "evil.txt").resolve(), "resolve() 未穿透软链接"
    assert policy.is_allowed(link, write=True) is False, "软链接逃逸未被拦截"


def test_trust_mode_never_bypasses_whitelist(sandbox) -> None:
    """信任模式开启时白名单外路径仍必须抛 PathDenied —— 用户最关心的安全属性。"""
    _, outside, policy = sandbox
    for trust in (True, False):
        safety = SafetyGate(policy, ConfirmationGate(), trust_mode=trust)
        ctx = TurnContext("x", trust_mode=trust)
        with pytest.raises(PathDenied) as info:
            safety.request("delete_file", {"path": str(outside / "evil.txt")}, ctx)
        assert "信任模式也不会绕过此限制" in str(info.value)


def test_trust_mode_skips_prompt_for_whitelisted_nonsensitive(sandbox) -> None:
    allowed, _, policy = sandbox
    prompted: list[dict] = []
    safety = SafetyGate(policy, ConfirmationGate(prompted.append), trust_mode=True)
    assert safety.request("delete_file", {"path": str(allowed / "good.txt")}, TurnContext("x", trust_mode=True)) is True
    assert prompted == [], "信任模式下白名单内非敏感目标不应弹窗"


def test_case_variants_of_whitelisted_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows 大小写变形必须归一化后仍判为白名单内（不能因大小写误拒，也不能放行白名单外）。"""
    slot = _SANDBOX_ROOT / f"case{next(_COUNTER)}"
    root = slot / "Root"
    root.mkdir(parents=True)
    monkeypatch.setenv("JARVIS_MOCK_DIR", str(root))
    policy = PathPolicy(allowed_roots=[root], sensitive_dirs=[])
    inside_variants = [
        str(root / "a.txt"),
        str(root).upper() + "\\A.TXT",
        str(root).lower() + "/a.txt",
    ]
    for variant in inside_variants:
        assert policy.is_allowed(variant, write=True), f"大小写变形被误拒：{variant}"
        assert policy.normalize(variant).parent == root.resolve(), f"大小写未归一化：{variant}"
    assert policy.is_allowed(str(slot / "other" / "a.txt"), write=True) is False


def test_short_name_form_does_not_sneak_into_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    """8.3 短名不会被"意外展开"到白名单内；若未展开则按失败关闭拒绝。"""
    slot = _SANDBOX_ROOT / f"case{next(_COUNTER)}"
    root = slot / "LongRootName"
    root.mkdir(parents=True)
    monkeypatch.setenv("JARVIS_MOCK_DIR", str(root))
    policy = PathPolicy(allowed_roots=[root], sensitive_dirs=[])
    short_form = str(slot / "LONGRO~1" / "a.txt")
    normalized = policy.normalize(short_form)
    if normalized.parent != root.resolve():
        assert policy.is_allowed(short_form, write=True) is False, "8.3 短名被放行到白名单"


def test_empty_and_dot_paths_are_contained_by_type_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """空路径会被 normalize 成根目录；write_file/delete_file 必须靠类型检查兜住，不能删掉目录。"""
    from jarvis_console.tools import filetools

    slot = _SANDBOX_ROOT / f"case{next(_COUNTER)}"
    root = slot / "ws"
    root.mkdir(parents=True)
    monkeypatch.setenv("JARVIS_MOCK_DIR", str(root))
    monkeypatch.setattr(filetools, "PATH_POLICY", PathPolicy(allowed_roots=[root], sensitive_dirs=[]))

    for raw in ("", ".", str(root)):
        written = filetools.write_file(raw, "hello")
        assert written["ok"] is False, f"空/目录路径竟写入成功：{raw} -> {written}"
        deleted = filetools.delete_file(raw)
        assert deleted["ok"] is False, f"空/目录路径竟删除成功：{raw} -> {deleted}"
    assert root.exists(), "工作区目录被误删"
    assert not list(root.iterdir()), "工作区被写出了残留文件"


def test_nul_byte_path_raises_but_is_contained(sandbox) -> None:
    """NUL 字节路径触发 ValueError（非 JarvisError 族），但不得静默放行。"""
    _, outside, policy = sandbox
    with pytest.raises(ValueError):
        policy.is_allowed(str(outside / "evil.txt") + "\x00", write=True)


def test_read_policy_is_unrestricted_by_design(sandbox) -> None:
    """风险观察项（记录事实，非"通过项"）：读路径不做白名单，任意绝对路径可读，
    包括 ~/.jarvis/.env。一旦 Key 落盘，模型可调用 read_file 读出并随上下文外发到云端。"""
    _, _, policy = sandbox
    assert policy.is_allowed("C:/Windows/win.ini", write=False) is True
    assert policy.is_allowed(str(Path.home() / ".jarvis" / ".env"), write=False) is True
    assert policy.is_allowed("\\\\.\\PhysicalDrive0", write=False) is False
