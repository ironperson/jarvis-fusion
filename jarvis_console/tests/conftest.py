"""pytest 全局隔离：Mock 模式、临时知识库、禁止真实网络。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ROOT = REPO_ROOT / "pipeline"
for candidate in (REPO_ROOT, PIPELINE_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import jarvis_console  # noqa: E402,F401


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """每个测试使用独立 Mock 工作区，且所有 requests 真出网立即失败。"""
    monkeypatch.setenv("JARVIS_MOCK", "1")
    monkeypatch.setenv("JARVIS_MOCK_DIR", str(tmp_path / "mock_workspace"))

    def block_network(self: requests.Session, method: str, url: str, *args: object, **kwargs: object) -> object:
        del self, method, args, kwargs
        raise requests.ConnectionError(f"测试禁止真实网络：{url}")

    monkeypatch.setattr(requests.sessions.Session, "request", block_network)
    try:
        from jarvis_console.tools import knowledge

        knowledge._STORE_CACHE.clear()
    except ImportError:
        pass
    return tmp_path


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """强制 pipeline 无参 init_db 指向临时库，绝不触碰真实 pipeline.db。"""
    from core import db as core_db

    path = tmp_path / "pipeline" / "pipeline.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core_db, "DEFAULT_DB", path)
    return path
