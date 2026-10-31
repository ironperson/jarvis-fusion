"""首启设置向导：选择引擎模式、配置 DeepSeek Key、启用语音与声纹、挑选皮肤。

- 首次运行（~/.jarvis/.env 不存在且无已配置密钥）由 app.py 调用 run_first_run()。
- 仅写两处：密钥追加到 ~/.jarvis/.env；偏好合并写回 config/console.yaml 的 ui/llm 段。
- 所有写操作均有备份与降级：缺 PyYAML 时跳过 yaml 写回并提示，不影响主链路。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from ..config import get_config, has_secret
from ..logging_setup import get_logger
from ..paths import CONFIG_FILE, HOME_JARVIS_ENV
from .skins import SkinPicker

log = get_logger(__name__)


def is_first_run() -> bool:
    """判断是否为首次运行：尚未配置任何密钥即视为首启。"""
    if HOME_JARVIS_ENV.is_file() and has_secret("DEEPSEEK_API_KEY"):
        return False
    return not has_secret("DEEPSEEK_API_KEY")


def _write_secret(key: str, value: str) -> None:
    """把 KEY=VALUE 写/更新进 ~/.jarvis/.env，保留其它行。"""
    HOME_JARVIS_ENV.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if HOME_JARVIS_ENV.is_file():
        lines = HOME_JARVIS_ENV.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped or "=" not in stripped:
            kept.append(line)
            continue
        existing_key = stripped.split("=", 1)[0].strip()
        if existing_key == key:
            kept.append(f"{key}={value}")
            replaced = True
        else:
            kept.append(line)
    if not replaced:
        kept.append(f"{key}={value}")
    HOME_JARVIS_ENV.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _apply_config_block(updates: dict[str, Any]) -> bool:
    """把 {段: {键: 值}} 合并写回 console.yaml，写前先备份。"""
    if not CONFIG_FILE.is_file():
        log.warning("配置文件缺失，跳过偏好写回：%s", CONFIG_FILE)
        return False
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("读取配置失败，跳过偏好写回：%s", exc)
        return False
    for section, fields in updates.items():
        if not isinstance(fields, dict):
            continue
        section_data = data.get(section)
        if not isinstance(section_data, dict):
            section_data = {}
            data[section] = section_data
        section_data.update(fields)
    backup = CONFIG_FILE.with_suffix(".yaml.bak")
    try:
        backup.write_text(CONFIG_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        CONFIG_FILE.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("写回配置失败：%s", exc)
        return False
    return True


class IntroPage(QWizardPage):
    """步骤一：选择引擎模式并（可选）填入 DeepSeek Key。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setTitle("① 选择大脑引擎")
        self.setSubTitle("推荐云端（需 DeepSeek Key），也可先离线体验。")
        layout = QVBoxLayout(self)
        self.radio_cloud = QRadioButton("云端（DeepSeek，推荐）")
        self.radio_ollama = QRadioButton("本地 Ollama（需自行部署）")
        self.radio_mock = QRadioButton("离线模拟（无需联网，立即可玩）")
        self.radio_cloud.setChecked(True)
        layout.addWidget(self.radio_cloud)
        layout.addWidget(self.radio_ollama)
        layout.addWidget(self.radio_mock)
        form = QFormLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("粘贴 DEEPSEEK_API_KEY（可稍后在设置里填）")
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Key：", self.key_edit)
        layout.addLayout(form)

    def collect(self) -> dict[str, Any]:
        if self.radio_mock.isChecked():
            mode = "mock"
        elif self.radio_ollama.isChecked():
            mode = "ollama"
        else:
            mode = "auto"
        return {"mode": mode, "api_key": self.key_edit.text().strip()}


class VoicePage(QWizardPage):
    """步骤二：启用语音、声纹校验与默认皮肤。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setTitle("② 语音与外观")
        self.setSubTitle("语音为可选项；声纹开启后仅回应主人。")
        from PySide6.QtWidgets import QCheckBox, QPushButton

        layout = QVBoxLayout(self)
        self.speech_box = QCheckBox("启用语音输入与播报")
        self.speech_box.setChecked(True)
        layout.addWidget(self.speech_box)
        self.voiceprint_box = QCheckBox("开启声纹校验（仅回应已注册的主人）")
        self.voiceprint_box.setChecked(True)
        layout.addWidget(self.voiceprint_box)
        from .voiceprint_dialog import VoiceprintEnrollDialog

        self.enroll_button = QPushButton("注册主人声纹…")
        self.enroll_button.clicked.connect(lambda: VoiceprintEnrollDialog(self).exec())
        layout.addWidget(self.enroll_button)

        form = QFormLayout()
        self.skin_picker = SkinPicker(current=get_config().ui.default_skin, parent=self)
        form.addRow("默认皮肤：", self.skin_picker)
        layout.addLayout(form)

    def collect(self) -> dict[str, Any]:
        return {
            "speech_enabled": self.speech_box.isChecked(),
            "voiceprint_required": self.voiceprint_box.isChecked(),
            "skin": self.skin_picker.current_skin(),
        }


class FinishPage(QWizardPage):
    """步骤三：确认并应用。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setTitle("③ 完成")
        self.setSubTitle("点击“完成”保存设置并进入主界面。")

    def collect(self) -> dict[str, Any]:
        return {}


class SetupWizard(QWizard):
    """首启设置向导；accept() 时落地配置。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("JARVIS 首启设置")
        self.setMinimumWidth(460)
        self.intro = IntroPage(self)
        self.voice = VoicePage(self)
        self.finish = FinishPage(self)
        self.addPage(self.intro)
        self.addPage(self.voice)
        self.addPage(self.finish)

    def accept(self) -> None:
        intro = self.intro.collect()
        voice = self.voice.collect()

        # 1) 密钥写 .env
        if intro.get("api_key"):
            _write_secret("DEEPSEEK_API_KEY", str(intro["api_key"]))

        # 2) 偏好写回 console.yaml
        llm_updates: dict[str, Any] = {"mode": intro.get("mode", "auto")}
        ui_updates: dict[str, Any] = {
            "speech_enabled": voice.get("speech_enabled", True),
            "voiceprint_required": voice.get("voiceprint_required", True),
            "default_skin": voice.get("skin", "jarvis"),
        }
        ok = _apply_config_block({"llm": llm_updates, "ui": ui_updates})
        if not ok:
            QMessageBox.information(
                self,
                "部分设置未保存",
                "配置写回失败（可能缺少 PyYAML 或文件只读）。\n"
                "其余功能不受影响；可在设置里手动调整 console.yaml。",
            )
        # 让缓存失效，使后续 get_config 重新读取。
        try:
            from ..config import get_config

            get_config(reload=True)
        except Exception:  # noqa: BLE001
            pass
        super().accept()


def run_first_run(parent: object | None = None) -> bool:
    """首次运行展示向导；用户完成返回 True，取消返回 False。"""
    wizard = SetupWizard(parent)
    return bool(wizard.exec() == QWizard.DialogCode.Accepted)
