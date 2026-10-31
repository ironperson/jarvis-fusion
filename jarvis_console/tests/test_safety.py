from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_console.core.errors import PathDenied
from jarvis_console.core.safety import ConfirmationGate, PathPolicy, SafetyGate
from jarvis_console.core.types import ConfirmRequest, TurnContext


def test_path_policy_strict_write_whitelist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    policy = PathPolicy(allowed_roots=[allowed], sensitive_dirs=[])
    assert policy.is_allowed(allowed / "report.md", write=True)
    assert not policy.is_allowed(outside / "report.md", write=True)
    with pytest.raises(PathDenied):
        policy.require_allowed(outside / "report.md", write=True)
    policy.add_allowed_root(outside)
    assert policy.is_allowed(outside / "report.md", write=True)


def test_confirmation_gate_resolve_and_cancel() -> None:
    holder: dict[str, ConfirmationGate] = {}

    def approve(payload: dict) -> None:
        assert payload["tool_name"] == "delete_file"
        holder["gate"].resolve({"approved": True})

    gate = ConfirmationGate(approve)
    holder["gate"] = gate
    result = gate.ask(ConfirmRequest("delete_file", "删除文件", ["C:/a.txt"]), timeout=0.2)
    assert result == {"approved": True}

    cancel_gate = ConfirmationGate(lambda payload: holder.update(last=payload))
    assert cancel_gate.ask(ConfirmRequest("delete_file", "删除", ["C:/b.txt"]), timeout=0.01) is False


def test_safety_user_rejection_returns_false(tmp_path: Path) -> None:
    target = tmp_path / "allowed" / "a.txt"
    policy = PathPolicy(allowed_roots=[target.parent], sensitive_dirs=[])
    holder: dict[str, ConfirmationGate] = {}

    def reject(payload: dict) -> None:
        assert str(target.resolve()) in payload["targets"]
        holder["gate"].resolve(False)

    confirmation = ConfirmationGate(reject)
    holder["gate"] = confirmation
    safety = SafetyGate(policy, confirmation, trust_mode=False, confirmation_timeout=0.2)
    assert safety.request("delete_file", {"path": str(target)}, TurnContext("delete")) is False


def test_trust_mode_never_bypasses_path_whitelist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    policy = PathPolicy(allowed_roots=[allowed], sensitive_dirs=[])
    safety = SafetyGate(policy, ConfirmationGate(), trust_mode=True)
    context = TurnContext("delete", trust_mode=True)
    assert safety.request("delete_file", {"path": str(allowed / "a.txt")}, context) is True
    with pytest.raises(PathDenied):
        safety.request("delete_file", {"path": str(tmp_path / "outside" / "a.txt")}, context)


def test_sensitive_target_still_confirms_in_trust_mode(tmp_path: Path) -> None:
    root = tmp_path / "personal"
    holder: dict[str, ConfirmationGate] = {}
    calls: list[dict] = []

    def confirm(payload: dict) -> None:
        calls.append(payload)
        holder["gate"].resolve(True)

    gate = ConfirmationGate(confirm)
    holder["gate"] = gate
    policy = PathPolicy(allowed_roots=[root], sensitive_dirs=[root])
    safety = SafetyGate(policy, gate, trust_mode=True, confirmation_timeout=0.2)
    assert safety.request("delete_file", {"path": str(root / "x.txt")}, TurnContext("x", trust_mode=True))
    assert calls and calls[0]["warnings"]
