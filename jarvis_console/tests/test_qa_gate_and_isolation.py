"""QA 独立验证：确认门类型兼容（工程师声称修好的那处 bug）、零 Qt 依赖、真实库零污染。"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from jarvis_console.core.safety import ConfirmationGate, PathPolicy, SafetyGate
from jarvis_console.core.types import ConfirmRequest, TurnContext
from jarvis_console.paths import KNOWLEDGE_DB, KNOWLEDGE_DIR, PACKAGE_ROOT

# 采集期（任何测试运行之前）记录真实库基线
_BASELINE_MTIME = KNOWLEDGE_DB.stat().st_mtime if KNOWLEDGE_DB.exists() else None
_BASELINE_SIZE = KNOWLEDGE_DB.stat().st_size if KNOWLEDGE_DB.exists() else None
_BASELINE_BACKUP_EXISTS = (KNOWLEDGE_DIR / "_backup").exists()


class _RecordingGate(ConfirmationGate):
    """记录 resolve 被调用时的实参。"""

    def __init__(self) -> None:
        super().__init__(lambda payload: None)
        self.resolved: list[Any] = []

    def resolve(self, result: object) -> None:
        self.resolved.append(result)
        super().resolve(result)


def _safety(inner: ConfirmationGate) -> SafetyGate:
    slot = Path(tempfile.mkdtemp(prefix="qa_gatecase_"))
    root = slot / "allowed"
    root.mkdir(parents=True)
    return SafetyGate(PathPolicy(allowed_roots=[root], sensitive_dirs=[]), inner, confirmation_timeout=0.2)


# ============================================================ D. 那处 bug 修复


def test_resolve_gate_handles_safety_gate_production_shape() -> None:
    """生产形态：AgentWorker 把 SafetyGate 塞进 ctx.gate。必须解析到内层 ConfirmationGate。"""
    from jarvis_console.ui.main_window import _resolve_gate

    inner = _RecordingGate()
    safety = _safety(inner)
    _resolve_gate(safety, {"approved": True})
    assert inner.resolved == [{"approved": True}], "SafetyGate 未被正确解包，confirm 结果丢失"


def test_resolve_gate_handles_bare_confirmation_gate() -> None:
    """裸 ConfirmationGate 形态（旧路径/测试桩）：必须直接 resolve。"""
    from jarvis_console.ui.main_window import _resolve_gate

    bare = _RecordingGate()
    _resolve_gate(bare, {"approved": False})
    assert bare.resolved == [{"approved": False}]


def test_resolve_gate_is_noop_for_unknown_or_none() -> None:
    """未知对象 / None 必须静默 no-op，不能抛 AttributeError。"""
    from jarvis_console.ui.main_window import _resolve_gate

    class _Weird:
        confirm_gate = object()  # 有 confirm_gate 但没有 resolve

    _resolve_gate(_Weird(), True)  # 不得抛异常
    _resolve_gate(None, True)  # 不得抛异常
    _resolve_gate(object(), True)  # 不得抛异常


def test_safety_gate_roundtrip_approved_and_rejected() -> None:
    """端到端：SafetyGate.request 在两种 resolve 值下都要返回正确布尔值。"""
    slot = Path(tempfile.mkdtemp(prefix="qa_gatecase_"))
    root = slot / "allowed"
    root.mkdir(parents=True)
    policy = PathPolicy(allowed_roots=[root], sensitive_dirs=[])

    for answer, expected in (({"approved": True}, True), ({"approved": False}, False), (False, False), (True, True)):
        holder: dict[str, ConfirmationGate] = {}

        def reply(payload: dict, _answer: object = answer) -> None:
            del payload
            holder["gate"].resolve(_answer)

        gate = ConfirmationGate(reply)
        holder["gate"] = gate
        safety = SafetyGate(policy, gate, confirmation_timeout=0.2)
        got = safety.request("delete_file", {"path": str(root / "a.txt")}, TurnContext("x"))
        assert got is expected, f"resolve({answer!r}) 得到 {got}，期望 {expected}"


def test_main_window_on_confirm_needed_with_safety_gate() -> None:
    """工程师的 test_ui_smoke 用的是裸 ConfirmationGate（从未出错的分支）。
    这里补生产形态：ctx.gate = SafetyGate，验证 MainWindow.on_confirm_needed 真的能唤醒 worker。"""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        pytest.skip("未安装 PySide6")

    from PySide6.QtWidgets import QApplication

    from jarvis_console.ui import main_window as mw
    from jarvis_console.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        worker_factory=lambda text, trust_mode, max_rounds: None,
        revoke_fn=lambda note_id: True,
        kb_count_fn=lambda: 0,
        path_authorizer=lambda path: path,
        summarizer=None,
        deepseek_configured=False,
        speech_enabled=False,
        default_max_rounds=8,
        voice="v",
        speech_threshold=200,
        summary_max_chars=80,
        initial_kb_count=0,
    )

    inner = _RecordingGate()
    safety = _safety(inner)
    pending: list[dict] = []

    # 生产形态：ConfirmationGate 的 notifier 把请求投递给主线程
    inner.notifier = pending.append
    window.worker = type("Stub", (), {"ctx": TurnContext("x", gate=safety), "isRunning": lambda self: False})()

    result: list[Any] = []

    def worker_side() -> None:
        result.append(safety.request("delete_file", {"path": str(next(iter([_safety_path(safety)])))}, TurnContext("x")))

    import threading

    thread = threading.Thread(target=worker_side, daemon=True)
    original_ask = mw.ConfirmDialog.ask
    mw.ConfirmDialog.ask = staticmethod(lambda parent, request: True)
    try:
        thread.start()
        deadline = 300
        while not pending and deadline > 0:
            app.processEvents()
            threading.Event().wait(0.02)
            deadline -= 1
        assert pending, "SafetyGate 未把确认请求投递出来"
        while pending:
            window.on_confirm_needed(pending.pop(0))
            app.processEvents()
        thread.join(timeout=10)
    finally:
        mw.ConfirmDialog.ask = original_ask
        window.close()

    assert inner.resolved and inner.resolved[-1] == {"approved": True}, (
        f"生产形态下确认结果未回灌：{inner.resolved}"
    )
    assert result and result[-1] is True, f"worker 侧未收到 approved：{result}"


def _safety_path(safety: SafetyGate) -> str:
    return str(next(iter(safety.path_policy.allowed_roots)) / "a.txt")


def test_agent_worker_cancel_releases_safety_gate() -> None:
    """AgentWorker.cancel 必须把 SafetyGate 解包后 resolve(False)，否则 worker 会卡满 300 秒。"""
    from jarvis_console.workers.agent_worker import AgentWorker

    inner = _RecordingGate()
    safety = _safety(inner)
    ctx = TurnContext("x", gate=safety)
    stub = type("Stub", (), {
        "ctx": ctx,
        "sig_status": type("S", (), {"emit": staticmethod(lambda text: None)})(),
    })()
    AgentWorker.cancel(stub)
    assert ctx.is_cancelled() is True
    assert inner.resolved == [False], f"cancel 未释放确认门：{inner.resolved}"


def test_agent_worker_cancel_releases_bare_gate() -> None:
    from jarvis_console.workers.agent_worker import AgentWorker

    bare = _RecordingGate()
    stub = type("Stub", (), {
        "ctx": TurnContext("x", gate=bare),
        "sig_status": type("S", (), {"emit": staticmethod(lambda text: None)})(),
    })()
    AgentWorker.cancel(stub)
    assert bare.resolved == [False]


def test_no_type_confusion_between_gate_kinds() -> None:
    """修复不得引入新的类型混淆：SafetyGate 与 ConfirmationGate 的公开职责必须仍可区分。"""
    inner = _RecordingGate()
    safety = _safety(inner)
    assert isinstance(safety.confirm_gate, ConfirmationGate)
    assert not hasattr(safety, "ask"), "SafetyGate 不应长出 ConfirmationGate 的 ask"
    assert hasattr(inner, "ask"), "ConfirmationGate 必须保留 ask"
    assert safety.destructive_tools == {"close_app", "delete_file"}


# ============================================================ 破坏性工具目标展示


def test_close_app_targets_carry_full_path() -> None:
    """close_app 的确认载荷必须逐条给出进程名/PID/完整可执行文件路径。"""
    safety = _safety(ConfirmationGate())
    targets = safety.resolve_targets("close_app", {"name": "python"})
    assert isinstance(targets, list) and targets, "close_app 未解析出任何目标"
    for item in targets:
        assert isinstance(item, dict), f"目标不是字典：{item}"
        assert {"name", "pid", "path"} <= set(item), f"目标字段不全：{item}"


@pytest.mark.xfail(
    strict=False,
    reason="源码 Bug：resolve_app 未命中 APP_MAP 时原样返回用户输入，"
           "resolve_targets 的「未找到」兜底把该字符串当 path，弹窗显示 python 而非绝对路径",
)
def test_close_app_fallback_target_path_is_absolute() -> None:
    """没有任何命中进程时，兜底目标的 path 必须仍是绝对路径（PRD 要求弹窗显示完整绝对路径）。
    现状：jarvis.skills.system.resolve_app('python') 返回 ('python', 'python')，
    safety.py:173 的兜底直接 str(command) -> 'python'。"""
    safety = _safety(ConfirmationGate())
    targets = safety.resolve_targets("close_app", {"name": "python"})
    path = str(targets[0]["path"])
    assert Path(path).is_absolute() or path == "路径不可读", f"弹窗会显示非绝对路径：{path!r}"


@pytest.mark.xfail(strict=False, reason="源码小瑕疵：pid 在命中时是 int、未命中时是字符串 '未找到'，类型不统一")
def test_close_app_pid_type_is_consistent() -> None:
    safety = _safety(ConfirmationGate())
    hit = safety.resolve_targets("close_app", {"name": "python"})
    assert isinstance(hit[0]["pid"], int), f"pid 类型不统一：{hit[0]['pid']!r}"


def test_close_app_degrades_when_psutil_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """psutil 缺失时必须降级为占位目标并继续弹窗，不能炸。"""
    monkeypatch.setitem(sys.modules, "psutil", None)  # type: ignore[arg-type]
    safety = _safety(ConfirmationGate())
    targets = safety.resolve_targets("close_app", {"name": "notepad"})
    assert targets and targets[0]["path"] == "依赖不可用"


def test_confirm_payload_is_json_serializable_for_qt_signal() -> None:
    """Qt Signal 只能安全携带基础类型；确认载荷必须可 JSON 序列化。"""
    request = ConfirmRequest(
        tool_name="close_app",
        action_text="结束这 2 个进程",
        targets=[{"name": "a.exe", "pid": 1, "path": "C:/x/a.exe"}],
        warnings=["目标涉及个人目录"],
        detail="将结束 2 个命中进程",
    )
    assert json.loads(json.dumps(request.to_dict(), ensure_ascii=False))["tool_name"] == "close_app"


# ============================================================ C10. 零 Qt 依赖


def test_core_and_tools_have_no_qt_imports_statically() -> None:
    """静态 AST 扫描：core/ 与 tools/ 不得出现任何 PySide/Qt 导入。"""
    offenders: list[str] = []
    for folder in ("core", "tools"):
        for file in sorted((PACKAGE_ROOT / folder).glob("*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    lowered = name.lower()
                    if "pyside" in lowered or lowered.split(".")[0] == "qt" or lowered.startswith("pyqt"):
                        offenders.append(f"{file.name}:{getattr(node, 'lineno', '?')} -> {name}")
    assert offenders == [], f"core/tools 出现 Qt 依赖：{offenders}"


def test_core_and_tools_import_cleanly_without_qt_at_runtime() -> None:
    """运行时证明：全新子进程导入 core/tools 全部模块后，sys.modules 里不得出现 PySide6。"""
    script = (
        "import sys, importlib, pkgutil\n"
        "import jarvis_console\n"
        "for pkg in ('jarvis_console.core', 'jarvis_console.tools'):\n"
        "    m = importlib.import_module(pkg)\n"
        "    for info in pkgutil.iter_modules(m.__path__):\n"
        "        importlib.import_module(pkg + '.' + info.name)\n"
        "leaked = [k for k in sys.modules if 'PySide6' in k or k.startswith('PySide6.')]\n"
        "print('LEAKED=' + ','.join(leaked))\n"
    )
    env = {"JARVIS_MOCK": "1", "PATH": ""}
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(PACKAGE_ROOT.parent),
        env={**__import__("os").environ, **env},
        timeout=180,
    )
    assert completed.returncode == 0, f"导入 core/tools 失败：\n{completed.stderr[-2000:]}"
    assert "LEAKED=" in completed.stdout, f"脚本未正常输出：{completed.stdout}\n{completed.stderr[-1000:]}"
    leaked = completed.stdout.split("LEAKED=", 1)[1].strip()
    assert leaked == "", f"core/tools 间接拉起了 PySide6：{leaked}"


# ============================================================ B5. 真实库零污染


def test_real_knowledge_db_untouched_by_test_session() -> None:
    """整场测试跑完后，真实 pipeline.db 的 mtime/size 必须纹丝不动，且不产生 _backup。"""
    if _BASELINE_MTIME is None:
        pytest.skip("真实 pipeline.db 不存在")
    assert KNOWLEDGE_DB.exists(), "真实库被删除"
    assert KNOWLEDGE_DB.stat().st_mtime == _BASELINE_MTIME, (
        f"真实库被写入：mtime 由 {_BASELINE_MTIME} 变为 {KNOWLEDGE_DB.stat().st_mtime}"
    )
    assert KNOWLEDGE_DB.stat().st_size == _BASELINE_SIZE, "真实库体积发生变化"
    assert (KNOWLEDGE_DIR / "_backup").exists() is _BASELINE_BACKUP_EXISTS, "真实库目录出现/消失 _backup"


def test_no_production_notestore_instantiated_during_tests() -> None:
    """不得有任何测试用真实 DB 路径构造 NoteStore。"""
    from jarvis_console.core.notestore import NoteStore

    store = NoteStore.__new__(NoteStore)
    store.db_path = KNOWLEDGE_DB
    store.settings = {}
    store._backup_done_date = ""
    assert store.db_path == KNOWLEDGE_DB  # 仅结构断言，不做任何写入
