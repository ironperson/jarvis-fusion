"""已安装技能加载器：动态 import + @tool 注册（限 PACKAGE_ROOT/skills 内）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from ..core.safety import PathPolicy
from ..core.tool_registry import REGISTRY
from ..logging_setup import get_logger
from ..paths import PACKAGE_ROOT

log = get_logger(__name__)

SKILLS_DIR = PACKAGE_ROOT / "skills"


def load_installed_skills() -> int:
    """导入 skills/ 下所有 .py，触发其 @tool 注册；返回成功数量。

    仅加载落在 PathPolicy 白名单内的文件；任何异常单文件失败不影响整体。
    """
    if not SKILLS_DIR.is_dir():
        return 0
    if not PathPolicy().is_allowed(SKILLS_DIR, write=True):
        log.warning("skills 目录不在写入白名单内，跳过技能加载：%s", SKILLS_DIR)
        return 0
    count = 0
    for path in sorted(SKILLS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"jarvis_console.skills.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            count += 1
        except Exception as exc:  # noqa: BLE001 - 单文件失败隔离
            log.warning("加载技能失败（已跳过）：%s -> %s", path.name, exc)
    # 确保已加载的技能工具处于启用状态（configure 白名单可能未包含动态名）。
    for spec_item in REGISTRY._tools.values():
        if getattr(spec_item, "category", "") == "skill":
            spec_item.enabled = True
    return count
