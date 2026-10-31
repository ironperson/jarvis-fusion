"""总控台配置与密钥加载。"""
from __future__ import annotations

import copy
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import (
    ASR_MODEL,
    CONFIG_FILE,
    HOME_JARVIS_ENV,
    MODELS_DIR,
    PIPER_MODEL,
    REPO_ENV,
    WAKE_MODEL,
)

try:
    import yaml
except ImportError:
    yaml = None


@dataclass(slots=True)
class LLMConfig:
    """大模型与自动降级配置。"""

    mode: str = "auto"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3-14b"
    disable_thinking: bool = True
    timeout_seconds: int = 120
    fail_threshold: int = 2


@dataclass(slots=True)
class SafetyConfig:
    """安全门配置。

    ``read_denylist`` 的取值语义（与 ``core/safety.py::_load_read_denylist`` 一致）：
    - 非空列表：只拒绝清单内的条目；
    - 空列表 ``[]``：**放开**清单内规则，是用户自救通道；
    - ``None``（console.yaml 里没有这个键，或写成 ``read_denylist:`` 空值）：
      「未配置」哨兵，回落到 ``DEFAULT_READ_DENYLIST``。
    ``~/.ssh`` 是不可关闭的安全底线，即使本清单置空也照样拒绝。
    """

    trust_mode: bool = False
    confirmation_timeout_seconds: int = 300
    read_denylist: list[str] | None = None


# 读取拒绝清单默认值。注意：这里只覆盖「可配置」的部分；
# ~/.ssh 目录由 PathPolicy._denylist_rules 恒定拒绝，不随本清单变化。
DEFAULT_READ_DENYLIST: list[str] = [
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "id_dsa",
    "id_ecdsa",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".htpasswd",
    ".git-credentials",
    "*.keystore",
    "*.jks",
    "*serviceaccount*.json",
    "*service_account*.json",
    "*service-account*.json",
    "*accountkey*.json",
    "credentials",
    "credentials.*",
    "secrets.json",
    "token.json",
    "~/.ssh/",
]


@dataclass(slots=True)
class MemoryConfig:
    """长期记忆配置。"""

    enabled: bool = True
    recall_top_k: int = 3
    auto_extract: bool = True


@dataclass(slots=True)
class UIConfig:
    """界面与播报配置。"""

    speech_enabled: bool = True
    voice: str = "en-GB-SoniaNeural"
    tts_backend: str = "edge"
    long_speech_threshold: int = 200
    summary_max_chars: int = 80
    max_rounds: int = 8
    default_skin: str = "jarvis"
    voiceprint_required: bool = True


@dataclass(slots=True)
class ProviderConfig:
    """单一云服务商的接入约定（统一 OpenAI 兼容）。"""

    id: str = ""
    label: str = ""
    base_url: str = ""
    model: str = ""
    env_key: str = ""
    priority: int = 100
    enabled_by_default: bool = True
    needs_key: bool = True


@dataclass(slots=True)
class ProvidersConfig:
    """多服务商路由配置；``default`` 为主引擎，``auto`` 决定云端不可达是否自动降级。"""

    default: str = "deepseek"
    auto: bool = True
    entries: dict[str, ProviderConfig] = field(default_factory=dict)

    def ordered(self) -> list[ProviderConfig]:
        """按优先级升序返回服务商列表（相同优先级保持声明顺序）。"""
        return sorted(self.entries.values(), key=lambda item: (item.priority, item.id))


@dataclass(slots=True)
class ConsoleConfig:
    """总控台完整配置对象。"""

    llm: LLMConfig = field(default_factory=LLMConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    tools_enabled: list[str] = field(default_factory=list)
    tools_disabled: list[str] = field(default_factory=list)
    log_level: str = "INFO"
    raw: dict[str, Any] = field(default_factory=dict)

    # 模型路径常量（与 paths 对齐，供 UI/日志直接引用，不暴露密钥）
    models_dir: Path = MODELS_DIR
    wake_model: Path = WAKE_MODEL
    asr_model: Path = ASR_MODEL
    piper_model: Path = PIPER_MODEL


_CONFIG_LOCK = threading.RLock()
_CONFIG_CACHE: ConsoleConfig | None = None


def _load_env_file(path: Path) -> dict[str, str]:
    """解析简单 ``KEY=VALUE`` 文件；缺失文件返回空字典。"""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_secret(key: str, default: str = "") -> str:
    """按环境变量、用户配置、仓库配置的顺序读取密钥。"""
    environment_value = os.environ.get(key, "").strip()
    if environment_value:
        return environment_value
    home_value = _load_env_file(HOME_JARVIS_ENV).get(key, "").strip()
    if home_value:
        return home_value
    repository_value = _load_env_file(REPO_ENV).get(key, "").strip()
    return repository_value or default


def has_secret(key: str) -> bool:
    """仅返回密钥是否存在，绝不暴露密钥内容。"""
    return bool(get_secret(key))


def _mapping(data: Any) -> dict[str, Any]:
    return data if isinstance(data, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if yaml is None:
        raise RuntimeError("缺少 PyYAML；请按 README 的安装命令补齐依赖。")
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _mapping(parsed)


def _parse_providers(value: Any) -> ProvidersConfig:
    """解析 ``providers`` 段为 N 路优先级服务商配置。"""
    block = _mapping(value)
    config = ProvidersConfig(
        default=str(block.get("default", "deepseek")).strip().lower() or "deepseek",
        auto=bool(block.get("auto", True)),
    )
    for key, raw_entry in _mapping(block.get("entries")).items():
        entry = _mapping(raw_entry)
        config.entries[key.strip().lower()] = ProviderConfig(
            id=key.strip().lower(),
            label=str(entry.get("label", key)),
            base_url=str(entry.get("base_url", "")).rstrip("/"),
            model=str(entry.get("model", "")),
            env_key=str(entry.get("env_key", "")),
            priority=max(0, int(entry.get("priority", 100))),
            enabled_by_default=bool(entry.get("enabled_by_default", True)),
            needs_key=bool(entry.get("needs_key", True)),
        )
    return config


def _parse_read_denylist(value: Any) -> list[str] | None:
    """解析 ``safety.read_denylist``，严格区分「未配置」与「显式置空」。

    - ``[]``（console.yaml 写 ``read_denylist: []``）→ 返回 ``[]``，即放开清单内规则；
    - ``None``（键缺失或写成空值）→ 返回 ``None`` 作为「未配置」哨兵，由调用方回落到
      ``DEFAULT_READ_DENYLIST``；
    - 其它非数组取值（配置写错）→ 同样视为未配置，回落到默认清单（更安全的一侧）。
    """
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return None


def _build_config(raw: dict[str, Any]) -> ConsoleConfig:
    llm = _mapping(raw.get("llm"))
    deepseek = _mapping(llm.get("deepseek"))
    ollama = _mapping(llm.get("ollama"))
    safety = _mapping(raw.get("safety"))
    memory = _mapping(raw.get("memory"))
    ui = _mapping(raw.get("ui"))
    tools = _mapping(raw.get("tools"))
    providers = _mapping(raw.get("providers"))
    mode = str(llm.get("mode", "auto"))
    if os.environ.get("JARVIS_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}:
        mode = "mock"
    return ConsoleConfig(
        llm=LLMConfig(
            mode=mode,
            deepseek_base_url=str(deepseek.get("base_url", "https://api.deepseek.com")),
            deepseek_model=str(deepseek.get("model", "deepseek-flash")),
            ollama_base_url=str(ollama.get("base_url", "http://localhost:11434")),
            ollama_model=str(ollama.get("model", "qwen3-14b")),
            disable_thinking=bool(llm.get("disable_thinking", True)),
            timeout_seconds=max(1, int(llm.get("timeout_seconds", 120))),
            fail_threshold=max(1, int(llm.get("fail_threshold", 2))),
        ),
        safety=SafetyConfig(
            trust_mode=bool(safety.get("trust_mode", False)),
            confirmation_timeout_seconds=max(1, int(safety.get("confirmation_timeout_seconds", 300))),
            read_denylist=_parse_read_denylist(safety.get("read_denylist", None)),
        ),
        memory=MemoryConfig(
            enabled=bool(memory.get("enabled", True)),
            recall_top_k=max(1, int(memory.get("recall_top_k", 3))),
            auto_extract=bool(memory.get("auto_extract", True)),
        ),
        ui=UIConfig(
            speech_enabled=bool(ui.get("speech_enabled", True)),
            voice=str(ui.get("voice", "en-GB-SoniaNeural")),
            tts_backend=str(ui.get("tts_backend", "edge")).lower() or "edge",
            long_speech_threshold=max(20, int(ui.get("long_speech_threshold", 200))),
            summary_max_chars=max(20, int(ui.get("summary_max_chars", 80))),
            max_rounds=min(16, max(1, int(ui.get("max_rounds", 8)))),
            default_skin=str(ui.get("default_skin", "jarvis")),
            voiceprint_required=bool(ui.get("voiceprint_required", True)),
        ),
        tools_enabled=[str(item) for item in tools.get("enabled", [])],
        tools_disabled=[str(item) for item in tools.get("disabled", [])],
        providers=_parse_providers(providers),
        log_level=os.environ.get("JARVIS_LOG_LEVEL", str(raw.get("log_level", "INFO"))).upper(),
        raw=copy.deepcopy(raw),
    )


def get_config(*, reload: bool = False) -> ConsoleConfig:
    """懒加载并缓存配置；传 ``reload=True`` 可重新读取磁盘与环境变量。"""
    global _CONFIG_CACHE
    with _CONFIG_LOCK:
        if reload or _CONFIG_CACHE is None:
            _CONFIG_CACHE = _build_config(_load_yaml(CONFIG_FILE))
        return _CONFIG_CACHE
