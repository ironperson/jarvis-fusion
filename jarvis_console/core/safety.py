"""破坏性操作确认门与文件路径白名单。"""
from __future__ import annotations

import fnmatch
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from ..paths import (
    HOME_SSH_DIR,
    KNOWLEDGE_DIR,
    PACKAGE_ROOT,
    system_desktop,
    system_documents,
    system_downloads,
)
from .errors import PathDenied
from .types import ConfirmRequest, TurnContext


class ConfirmationGate:
    """使用 threading.Event 在 worker 与 UI 之间完成确认握手。"""

    def __init__(self, notifier: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._event = threading.Event()
        self._result: object = False
        self.pending: ConfirmRequest | None = None
        self.notifier = notifier
        self._lock = threading.Lock()

    def ask(self, req: ConfirmRequest, timeout: float = 300.0) -> object:
        """通知主线程并等待结果；超时按拒绝处理。"""
        with self._lock:
            self.pending = req
            self._result = False
            self._event.clear()
        if self.notifier is not None:
            self.notifier(req.to_dict())
        completed = self._event.wait(max(0.01, timeout))
        with self._lock:
            result = self._result if completed else False
            self.pending = None
        return result

    def resolve(self, result: object) -> None:
        """由主线程写入用户选择并唤醒 worker。"""
        with self._lock:
            self._result = result
            self._event.set()


class PathPolicy:
    """写入严格白名单；读取允许存在的普通文件路径，但拒绝密钥/凭据类文件。"""

    def __init__(
        self,
        allowed_roots: list[Path | str] | None = None,
        sensitive_dirs: list[Path | str] | None = None,
        read_denylist: list[str] | None = None,
    ) -> None:
        defaults = [KNOWLEDGE_DIR, system_desktop(), PACKAGE_ROOT]
        self.allowed_roots = [self._resolved(item) for item in (allowed_roots or defaults)]
        self.sensitive_dirs = [
            self._resolved(item)
            for item in (sensitive_dirs or [system_desktop(), system_documents(), system_downloads()])
        ]
        self.read_denylist = self._load_read_denylist(read_denylist)
        self._manual_roots: list[Path] = []

    @staticmethod
    def _load_read_denylist(read_denylist: list[str] | None) -> list[str]:
        """优先用显式入参，其次读 console.yaml，最后用内置默认值。

        必须区分「未配置」与「显式置空」，因此用 ``is not None`` 判断而不能用真值判断：
        ``if configured`` 会把空列表 ``[]`` 当成"没配"，静默回落到默认清单，
        使 console.yaml 注释里承诺的「置空即可放开」失效。

        - 显式入参 ``[]``，或配置里 ``read_denylist: []`` → 放开清单内规则；
        - 配置值为 ``None``（键缺失 / 写成空值）→ 回落到 ``DEFAULT_READ_DENYLIST``；
        - 配置模块不可用 → 同样回落到默认清单（更安全的一侧）。

        注意：放开清单不等于放开一切，``~/.ssh`` 这条硬编码底线仍然拒绝，见
        ``_denylist_rules``。
        """
        if read_denylist is not None:
            return [str(item) for item in read_denylist]
        try:
            from .. import config as console_config
        except Exception:
            return list(_BUILTIN_READ_DENYLIST)
        configured = getattr(console_config.get_config().safety, "read_denylist", None)
        if configured is None:
            return list(console_config.DEFAULT_READ_DENYLIST)
        return [str(item) for item in configured]

    @staticmethod
    def _resolved(path: Path | str) -> Path:
        return Path(path).expanduser().resolve(strict=False)

    def normalize(self, path: Path | str) -> Path:
        """规范化路径；相对路径默认落到桌面，Mock 模式落到隔离工作区。"""
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            mock_root = os.environ.get("JARVIS_MOCK_DIR", "").strip()
            base = Path(mock_root) if mock_root else system_desktop()
            candidate = base / candidate
        return candidate.resolve(strict=False)

    @staticmethod
    def _inside(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return path == root

    def add_allowed_root(self, path: Path | str) -> Path:
        """添加由用户在弹窗中亲自点选的临时白名单目录。"""
        root = self.normalize(path)
        if root.is_file():
            root = root.parent
        if root not in self._manual_roots:
            self._manual_roots.append(root)
        return root

    def _effective_write_roots(self) -> list[Path]:
        roots = list(self.allowed_roots) + list(self._manual_roots)
        mock_root = os.environ.get("JARVIS_MOCK_DIR", "").strip()
        if mock_root:
            roots.append(self._resolved(mock_root))
        return roots

    def is_allowed(self, path: Path | str, *, write: bool = True) -> bool:
        """检查路径；写操作只允许四类根，读操作拒绝设备/空路径但可跨目录。"""
        normalized = self.normalize(path)
        if not write:
            return bool(str(normalized)) and not str(normalized).startswith("\\\\.\\")
        return any(self._inside(normalized, root) for root in self._effective_write_roots())

    def require_allowed(self, path: Path | str, *, write: bool = True) -> Path:
        """返回规范路径或抛出带修复提示的 PathDenied。"""
        normalized = self.normalize(path)
        if not self.is_allowed(normalized, write=write):
            raise PathDenied(self.violation_hint(normalized))
        return normalized

    def _denylist_rules(self) -> tuple[list[str], list[Path], set[str]]:
        """把拒绝清单拆成：文件名模式、目录规则、（目录内）文件名模式。

        `~/.ssh` 与 `in_dir_patterns` 是**不可配置的安全底线**：即使
        `console.yaml` 的 read_denylist 被置空，`~/.ssh` 下所有文件仍会被拒绝，
        `~/.jarvis/` 等目录内的凭据类文件名也仍会被拒绝。配置项只能收窄危害面，
        不能把这条底线关掉。
        """
        name_patterns: list[str] = []
        directories: list[Path] = [HOME_SSH_DIR]
        in_dir_patterns: set[str] = {".env", "*.key", "*.pem", "*.p12", "*.pfx"}
        for pattern in self.read_denylist:
            text = pattern.strip()
            if not text:
                continue
            if text.endswith(("/", "\\")) or "/" in text or "\\" in text:
                candidate = self._resolved(Path(text).expanduser())
                if candidate not in directories:
                    directories.append(candidate)
            else:
                name_patterns.append(text.lower())
        return name_patterns, directories, in_dir_patterns

    def is_sensitive_read(self, path: Path | str) -> bool:
        """判断读取目标是否命中密钥/凭据拒绝清单。

        规则要窄而准：只按文件名模式（fnmatch）与显式目录前缀匹配，
        避免误伤知识库 md、桌面文档等正常读取。
        """
        normalized = self.normalize(path)
        name = normalized.name.lower()
        name_patterns, directories, in_dir_patterns = self._denylist_rules()
        for pattern in name_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        for directory in directories:
            if not self._inside(normalized, directory):
                continue
            # ~/.ssh 一律拒绝；其余目录（如 ~/.jarvis）只拒绝凭据类文件名，
            # 以免误伤长期记忆库等正常数据文件。
            if directory == HOME_SSH_DIR:
                return True
            if any(fnmatch.fnmatch(name, pattern) for pattern in in_dir_patterns):
                return True
        return False

    def is_sensitive(self, path: Path | str) -> bool:
        """判断目标是否位于桌面、文档或下载目录。"""
        normalized = self.normalize(path)
        return any(self._inside(normalized, root) for root in self.sensitive_dirs)

    def violation_hint(self, path: Path | str) -> str:
        """给出白名单违规的中文说明。"""
        normalized = self.normalize(path)
        roots = "、".join(str(item) for item in self._effective_write_roots())
        return (
            f"拒绝写入白名单外路径：{normalized}。允许位置：{roots}。"
            "如确需写入其他位置，请在总控台里手动选择该目录授权；信任模式也不会绕过此限制。"
        )


class SafetyGate:
    """为 close_app/delete_file 解析完整目标并实施确认策略。"""

    def __init__(
        self,
        path_policy: PathPolicy | None = None,
        confirm_gate: ConfirmationGate | None = None,
        trust_mode: bool = False,
        confirmation_timeout: float = 300.0,
    ) -> None:
        self.trust_mode = trust_mode
        self.destructive_tools = {"close_app", "delete_file"}
        self.path_policy = path_policy or PathPolicy()
        self.sensitive_dirs = self.path_policy.sensitive_dirs
        self.confirm_gate = confirm_gate or ConfirmationGate()
        self.confirmation_timeout = confirmation_timeout

    def is_destructive(self, name: str) -> bool:
        """返回工具是否属于当前版本的破坏性集合。"""
        return name in self.destructive_tools

    @staticmethod
    def _resolve_executable_path(command: str) -> str:
        """把裸命令解析为完整绝对路径；解析不到时如实说明，绝不回显裸文件名。

        jarvis.skills.system.resolve_app 对未命中 APP_MAP 的名字原样返回输入
        （如 "notepad.exe"），而 PRD 要求弹窗逐行显示完整绝对路径，故在此兜底。
        """
        raw = str(command or "").strip()
        if not raw:
            return "未能定位可执行文件"
        if raw.startswith(("ms-settings:", "http://", "https://")):
            return raw
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return str(candidate.resolve(strict=False))
        located = shutil.which(raw)
        if located:
            return str(Path(located).resolve(strict=False))
        return f"未能定位可执行文件（{raw}）"

    def resolve_targets(self, name: str, args: dict[str, Any]) -> list[Any]:
        """在弹窗前解析完整文件路径或全部命中进程。"""
        if name == "delete_file":
            path = self.path_policy.require_allowed(str(args.get("path", "")), write=True)
            return [str(path)]
        if name == "close_app":
            try:
                import psutil
                from jarvis.skills.system import resolve_app
            except ImportError:
                return [{"name": str(args.get("name", "")), "pid": "未知", "path": "依赖不可用"}]
            command, display = resolve_app(str(args.get("name", "")))
            target = Path(command).name.lower()
            targets: list[dict[str, Any]] = []
            for process in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    process_name = str(process.info.get("name") or "")
                    if process_name.lower() == target:
                        targets.append({
                            "name": process_name or display,
                            "pid": int(process.info.get("pid") or 0),
                            "pid_display": str(process.info.get("pid") or 0),
                            "path": str(process.info.get("exe") or self._resolve_executable_path(command)),
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if targets:
                return targets
            # pid 统一为 int：-1 表示「未找到匹配进程」，展示串另放 pid_display。
            return [{
                "name": display,
                "pid": -1,
                "pid_display": "未找到",
                "path": self._resolve_executable_path(command),
            }]
        return [str(value) for value in args.values()]

    def describe(self, name: str, args: dict[str, Any]) -> str:
        """生成面向用户的操作说明。"""
        targets = self.resolve_targets(name, args)
        if name == "delete_file":
            return f"将删除文件：{targets[0]}"
        if name == "close_app":
            return f"将结束 {len(targets)} 个命中进程"
        return f"将执行破坏性工具 {name}"

    def request(self, name: str, args: dict[str, Any], ctx: TurnContext) -> bool:
        """按信任模式与敏感目录规则决定是否弹窗并返回审批结果。"""
        if not self.is_destructive(name):
            return True
        targets = self.resolve_targets(name, args)
        warnings: list[str] = []
        sensitive = False
        if name == "delete_file":
            sensitive = self.path_policy.is_sensitive(str(args.get("path", "")))
        elif name == "close_app":
            for target in targets:
                if isinstance(target, dict):
                    path = str(target.get("path") or "")
                    if path and self.path_policy.is_sensitive(path):
                        sensitive = True
        if sensitive:
            warnings.append("目标涉及 Desktop/Documents/Downloads 等个人目录，请再次核对。")
        trust_enabled = bool(ctx.trust_mode or self.trust_mode)
        if trust_enabled and not sensitive:
            return True
        if name == "delete_file":
            action = f"删除这 {len(targets)} 个文件"
        elif name == "close_app":
            action = f"结束这 {len(targets)} 个进程"
        else:
            action = f"执行 {name}"
        request = ConfirmRequest(
            tool_name=name,
            action_text=action,
            targets=targets,
            warnings=warnings,
            detail=self.describe(name, args),
        )
        answer = self.confirm_gate.ask(request, timeout=self.confirmation_timeout)
        if isinstance(answer, dict):
            return bool(answer.get("approved", False))
        return bool(answer)
