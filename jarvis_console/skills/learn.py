"""学会新能力闭环：生成 → 静态校验 → 主人确认 → 受控写入并注册。

安全边界（落实 PRD 的 R8 / 共享知识第 6 条）：
- SkillValidator 静态黑名单：拒 eval/exec/os.system/subprocess/__import__/socket 等危险调用；
  限制 import 白名单；所有文件写入必须经 PathPolicy（限 PACKAGE_ROOT/skills）。
- 任何技能代码只有展示代码 + 影响范围、主人确认后才落盘并动态注册。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.safety import PathPolicy
from ..core.tool_registry import REGISTRY, tool
from ..logging_setup import get_logger
from ..paths import PACKAGE_ROOT

log = get_logger(__name__)

SKILLS_DIR = PACKAGE_ROOT / "skills"

# import 白名单：只允许安全、无副作用的标准库。
_ALLOWED_IMPORTS = {
    "json", "math", "datetime", "re", "pathlib", "time", "collections", "itertools",
    "statistics", "random", "string", "textwrap", "typing", "fractions", "decimal",
    "hashlib", "base64", "unicodedata", "calendar", "enum", "dataclasses",
}
# 危险调用名 / 危险模块属性（命中即拒）。
_DANGEROUS_NAMES = {"eval", "exec", "compile", "input", "__import__", "open", "exit", "quit"}
_DANGEROUS_ATTR = {
    "system", "popen", "spawn", "Popen", "call", "check_output", "check_call",
    "getoutput", "run", "connect", "create_connection", "socket", "send", "sendall",
    "put", "post", "delete", "patch", "write", "writelines", "truncate",
}


@dataclass(slots=True)
class SkillSpec:
    """一条待安装/已安装技能的描述。"""

    name: str
    description: str
    code: str = ""
    code_path: str = ""
    status: str = "trial"
    sha256: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_code_block(text: str) -> str:
    """从模型输出里抽取首个 ```python 代码块。"""
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


class SkillValidator:
    """静态黑名单 + import 白名单 + PathPolicy 的三重审查。"""

    @staticmethod
    def validate(code: str, write_root: Path | None = None) -> tuple[bool, str]:
        """返回 (是否通过, 说明)。"""
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return False, f"代码存在语法错误：{exc}"
        # 1) import 白名单
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    modules = [node.module or ""]
                for module in modules:
                    top = module.split(".")[0]
                    if top not in _ALLOWED_IMPORTS:
                        return False, f"禁止导入非白名单模块：{module}"
            # 2) 危险调用名
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in _DANGEROUS_NAMES:
                    return False, f"禁止调用危险函数：{func.id}()"
                if isinstance(func, ast.Attribute) and func.attr in _DANGEROUS_ATTR:
                    return False, f"禁止调用危险方法：.{func.attr}()"
        # 3) 必须装饰 @tool 且定义至少一个函数（显式遍历，避免嵌套生成式歧义）
        has_tool = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "tool":
                    has_tool = True
                    break
            if has_tool:
                break
        if not has_tool:
            return False, "技能代码必须定义至少一个 @tool 装饰的函数。"
        # 4) 写入根限制（PathPolicy 允许根内）
        root = write_root or SKILLS_DIR
        if not PathPolicy().is_allowed(root, write=True):
            return False, f"写入根不在白名单内：{root}"
        return True, "通过静态审查。"

    @staticmethod
    def validate_spec(spec: SkillSpec, write_root: Path | None = None) -> tuple[bool, str]:
        return SkillValidator.validate(spec.code, write_root)


class SkillGenerator:
    """让大脑产出符合 @tool 约定的技能代码。"""

    SYSTEM_PROMPT = (
        "你负责为贾维斯编写一个全新的本地技能。请只输出一个 ```python 代码块，"
        "其中用如下方式定义工具：\n"
        "from jarvis_console.core.tool_registry import tool\n\n"
        "@tool(name='your_tool', description='一句话说明用途', category='skill')\n"
        "def your_tool(arg1: str, arg2: int = 1) -> dict[str, Any]:\n"
        "    '''用中文写明用途。'''\n"
        "    return {'ok': True, 'text': '...'}\n\n"
        "约束：\n"
        "1. 仅使用白名单标准库（json/math/datetime/re/...），禁止 os.system/eval/exec/subprocess/网络写入。\n"
        "2. 函数必须有清晰类型注解，返回 dict（含 ok 与 text/data）。\n"
        "3. 不要写文件到白名单外，不要做任何破坏性或联网写操作。\n"
        "4. 只输出代码，不要解释。"
    )

    def __init__(self, router: Any) -> None:
        self.router = router

    def generate(self, intent: str, timeout: int = 120) -> SkillSpec:
        """把自然语言意图交给大脑，产出代码并抽取为 SkillSpec。"""
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": f"请为以下能力编写技能代码：{intent}"},
        ]
        try:
            response = self.router.chat(messages, [], timeout=timeout)
            code = _extract_code_block(response.content)
        except Exception as exc:  # noqa: BLE001
            return SkillSpec(name="", description=intent, code="", status="failed",
                            sha256="", parameters={"error": str(exc)})
        # 从代码中尝试解析 tool name/description。
        name, description = self._parse_meta(code)
        return SkillSpec(
            name=name or "generated_skill",
            description=description or intent,
            code=code,
            status="trial",
            sha256=_sha256(code),
        )

    @staticmethod
    def _parse_meta(code: str) -> tuple[str, str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return "", ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "tool":
                        name = ""
                        description = ast.get_docstring(node) or ""
                        for kw in dec.keywords:
                            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                                name = str(kw.value.value)
                            if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                                description = str(kw.value.value)
                        return name, description
        return "", ""


class SkillInstaller:
    """把通过校验的技能写入 skills/ 并动态导入注册。"""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self.skills_dir = Path(skills_dir or SKILLS_DIR)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def install(self, spec: SkillSpec) -> tuple[bool, str]:
        """写入文件并经 PathPolicy 落在 PACKAGE_ROOT 内，再动态导入注册。"""
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", spec.name) or "generated_skill"
        code_path = self.skills_dir / f"{safe_name}.py"
        if not PathPolicy().is_allowed(code_path, write=True):
            return False, f"技能写入路径不在白名单内：{code_path}"
        ok, reason = SkillValidator.validate(spec.code, self.skills_dir)
        if not ok:
            return False, reason
        try:
            code_path.write_text(spec.code, encoding="utf-8")
        except OSError as exc:
            return False, f"写入技能文件失败：{exc}"
        spec.code_path = str(code_path)
        spec.sha256 = _sha256(spec.code)
        spec.status = "installed"
        loaded = self._dynamic_import(safe_name, code_path)
        if not loaded:
            return False, "技能文件已写入，但动态注册失败（见日志）。"
        log.info("技能已安装并注册：%s -> %s", safe_name, code_path)
        return True, f"已学会并安装技能：{safe_name}"

    def _dynamic_import(self, module_name: str, code_path: Path) -> bool:
        try:
            spec = importlib.util.spec_from_file_location(
                f"jarvis_console.skills.{module_name}", str(code_path)
            )
            if spec is None or spec.loader is None:
                return False
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # 触发 @tool 注册
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("动态导入技能失败：%s", exc)
            return False


class SkillManager:
    """把生成 / 校验 / 安装三件套编排给 UI 调用。"""

    def __init__(self, router: Any, skills_dir: Path | None = None) -> None:
        self.generator = SkillGenerator(router)
        self.installer = SkillInstaller(skills_dir)

    def generate(self, intent: str, timeout: int = 120) -> SkillSpec:
        return self.generator.generate(intent, timeout)

    def validate(self, code: str) -> tuple[bool, str]:
        return SkillValidator.validate(code, self.installer.skills_dir)

    def install(self, spec: SkillSpec) -> tuple[bool, str]:
        return self.installer.install(spec)


@tool(
    name="learn_skill",
    description=(
        "学习一个全新的本地技能：传入技能 name、description 与完整 python 代码（必须含 @tool 装饰函数）。"
        "代码会先做静态安全审查；审查通过后返回提案，由主人在弹窗中确认后才安装。禁止写白名单外文件、"
        "禁止 os.system/eval/exec/subprocess/联网写。"
    ),
    category="skill",
)
def learn_skill(name: str, description: str, code: str) -> dict[str, Any]:
    """模型触发的学会新能力入口；只做校验与提案，不在工具内直接落盘。"""
    spec = SkillSpec(name=name, description=description, code=code, sha256=_sha256(code))
    ok, reason = SkillValidator.validate(spec.code)
    if not ok:
        return {"ok": False, "error": f"技能未通过安全审查：{reason}"}
    return {
        "ok": True,
        "needs_confirm": True,
        "skill_proposal": {
            "name": spec.name,
            "description": spec.description,
            "code": spec.code,
            "sha256": spec.sha256,
        },
        "text": f"已生成技能「{spec.name}」并通过静态审查，等待主人在弹窗中确认安装。",
    }
