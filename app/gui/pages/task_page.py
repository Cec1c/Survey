import json
from typing import Any, Callable, Dict, List, Optional
from urllib import error, request
from threading import Thread

from PyQt5.QtCore import pyqtSignal, QObject, pyqtSlot, Qt
from PyQt5.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget, QFrame
from qfluentwidgets import (
    BodyLabel, LineEdit, PrimaryPushButton, TextEdit,
    ComboBox, CardWidget, StrongBodyLabel, SubtitleLabel,
    PushButton, InfoBar, InfoBarPosition
)
from PyQt5.QtGui import QFont

from app.gui.state.llm_config import LLMConfig

# 主流LLM提供商和模型配置（模型列表仅作初始 fallback，连接测试成功后将从 API 动态拉取）
PROVIDERS = {
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"name": "GPT-4o", "id": "gpt-4o"},
            {"name": "GPT-4o Mini", "id": "gpt-4o-mini"},
            {"name": "GPT-4 Turbo", "id": "gpt-4-turbo"},
            {"name": "GPT-3.5 Turbo", "id": "gpt-3.5-turbo"},
        ]
    },
    "Anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "models": [
            {"name": "Claude 3.5 Sonnet", "id": "claude-3-5-sonnet-20241022"},
            {"name": "Claude 3 Opus", "id": "claude-3-opus-20240229"},
            {"name": "Claude 3 Haiku", "id": "claude-3-haiku-20240307"},
        ]
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com",
        "models": [
            {"name": "DeepSeek V4 Flash", "id": "deepseek-v4-flash"},
            {"name": "DeepSeek V4 Pro", "id": "deepseek-v4-pro"},
        ]
    },
    "Google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": [
            {"name": "Gemini 2.0 Pro", "id": "gemini-2.0-pro-exp-02-05"},
            {"name": "Gemini 1.5 Pro", "id": "gemini-1.5-pro"},
            {"name": "Gemini 1.5 Flash", "id": "gemini-1.5-flash"},
        ]
    },
    "Azure OpenAI": {
        "base_url": "https://your-resource.openai.azure.com/openai/deployments/your-deployment",
        "models": [
            {"name": "GPT-4o (Azure)", "id": "gpt-4o"},
            {"name": "GPT-4 Turbo (Azure)", "id": "gpt-4-turbo"},
        ]
    },
    "自定义": {
        "base_url": "",
        "models": [
            {"name": "自定义模型", "id": ""},
        ]
    }
}

_PAGE_QSS = """
QWidget {
    color: #efe9ff;
}
QLabel {
    color: #efe9ff;
}
QLineEdit, QTextEdit, QComboBox {
    color: #efe9ff;
    selection-background-color: #5e35b1;
}
CardWidget {
    background-color: rgba(94, 53, 177, 0.3);
    border-radius: 8px;
}
"""


class TaskPage(QWidget):
    """
    模型配置页面 —— 管理 LLM API 连接和系统提示词。
    ────────────────────────────────────────────────────
    功能：
      - 选择 LLM 提供商（OpenAI / Anthropic / DeepSeek / Google / Azure / 自定义）
      - 配置 API 密钥、模型 ID、Base URL
      - 测试连接（异步，成功后自动拉取模型列表）
      - 编辑系统提示词（MCP 开启/关闭两种模式）

    信号：
      config_saved(object)           — 保存后通知主窗口同步
      _test_result_signal(bool, str, list) — 内部信号，后台线程测试结果回调
    """

    config_saved = pyqtSignal(object)
    # 内部跨线程信号：(成功/失败, 消息, 模型列表)
    _test_result_signal = pyqtSignal(bool, str, list)

    def __init__(self, config: LLMConfig, config_path: str, parent=None):
        super().__init__(parent)
        self._config = config
        self._config_path = config_path
        self._loading = False           # 加载标志，防止 _on_provider_changed 误触发
        self._testing_connection = False  # 测试中标志，防止重复点击
        self._test_thread = None

        # 后台测试完成后，回调 _show_test_result 更新 UI
        self._test_result_signal.connect(self._show_test_result)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = SubtitleLabel("模型配置")
        layout.addWidget(title)
        layout.addSpacing(8)

        model_card = self._create_model_card()
        layout.addWidget(model_card)

        prompt_card = self._create_prompt_card()
        layout.addWidget(prompt_card)

        row = QHBoxLayout()
        self.save_btn = PrimaryPushButton("保存配置", self)
        self.save_btn.setFixedWidth(120)
        self.save_btn.clicked.connect(self._on_save)

        self.test_btn = PushButton("测试连接", self)
        self.test_btn.setFixedWidth(120)
        self.test_btn.clicked.connect(self._on_test_connection)

        row.addWidget(self.save_btn)
        row.addWidget(self.test_btn)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)

        self.setStyleSheet(_PAGE_QSS)
        self._load_to_ui()

    def _create_model_card(self) -> CardWidget:
        """
        创建模型设置卡片。
        ──────────────────
        包含 4 个表单字段：API 密钥、提供商下拉框、模型下拉框、Base URL
        联动逻辑：选择提供商 → 自动填充 Base URL + 更新模型列表
        """
        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        title = StrongBodyLabel("模型设置")
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        card_layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        # API 密钥 —— Password 模式显示为圆点
        self.api_key_input = LineEdit(self)
        self.api_key_input.setPlaceholderText("输入API密钥")
        self.api_key_input.setEchoMode(LineEdit.Password)
        form.addRow("API 密钥:", self.api_key_input)

        # 提供商下拉框 —— 切换时自动更新 Base URL 和模型列表
        self.provider_combo = ComboBox(self)
        self.provider_combo.addItems(list(PROVIDERS.keys()))
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        form.addRow("提供商:", self.provider_combo)

        # 模型下拉框 —— itemData 存储模型 ID，currentText 存储显示名
        self.model_combo = ComboBox(self)
        form.addRow("模型:", self.model_combo)

        # Base URL —— 选择提供商后自动填充，也可手动修改
        self.base_url_input = LineEdit(self)
        self.base_url_input.setPlaceholderText("自动生成或手动输入")
        form.addRow("Base URL:", self.base_url_input)

        card_layout.addLayout(form)
        return card

    def _create_prompt_card(self) -> CardWidget:
        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        title = StrongBodyLabel("系统提示词")
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        card_layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self.system_prompt_input = TextEdit(self)
        self.system_prompt_input.setFixedHeight(100)
        self.system_prompt_input.setPlaceholderText("MCP工具启用时的系统提示词...")
        form.addRow("System Prompt (MCP开启):", self.system_prompt_input)

        self.plain_system_prompt_input = TextEdit(self)
        self.plain_system_prompt_input.setFixedHeight(80)
        self.plain_system_prompt_input.setPlaceholderText("纯文本模式时的系统提示词...")
        form.addRow("System Prompt (MCP关闭):", self.plain_system_prompt_input)

        card_layout.addLayout(form)
        return card

    def _on_provider_changed(self, provider_name: str) -> None:
        """
        提供商下拉框切换的槽函数。
        ─────────────────────────────
        联动逻辑：
          1. 清空模型下拉框，填入该提供商的预设模型
          2. 自动填充 Base URL（"自定义"提供商除外）
          3. _loading 标志防止页面初始化加载时误触发
        """
        self.model_combo.clear()
        if provider_name in PROVIDERS:
            provider = PROVIDERS[provider_name]
            models = provider.get("models", [])
            for model in models:
                # addItem(text, userData) —— text=显示名, userData=模型 ID
                self.model_combo.addItem(model["name"], model["id"])

            # 非自定义提供商且非初始化加载时，自动填充 Base URL
            if provider_name != "自定义" and not self._loading:
                self.base_url_input.setText(provider["base_url"])
            elif provider_name == "自定义" and not self._loading:
                self.base_url_input.clear()

    def _load_to_ui(self) -> None:
        self._loading = True
        self.api_key_input.setText(self._config.api_key)
        self.system_prompt_input.setPlainText(self._config.system_prompt)
        self.plain_system_prompt_input.setPlainText(self._config.plain_system_prompt)
        self.base_url_input.setText(self._config.base_url)

        # 尝试匹配提供商和模型
        self._match_provider_and_model()

        # 如果没有找到匹配，直接使用当前模型名称
        if not self.model_combo.currentData():
            self.model_combo.addItem(self._config.model, self._config.model)
            self.model_combo.setCurrentIndex(self.model_combo.count() - 1)

        self._loading = False

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        text = url.strip().rstrip("/")
        if text.endswith("/v1"):
            text = text[:-3]
        return text

    def _match_provider_and_model(self) -> None:
        model_id = self._config.model
        current_base = self._normalize_base_url(self._config.base_url)

        matched_provider = None
        matched_model_name = None

        for provider_name, provider_data in PROVIDERS.items():
            provider_base = self._normalize_base_url(provider_data["base_url"])
            if provider_base == current_base and provider_base:
                matched_provider = provider_name
                for model in provider_data["models"]:
                    if model["id"] == model_id:
                        matched_model_name = model["name"]
                        break
                break

        if matched_provider:
            index = self.provider_combo.findText(matched_provider)
            if index >= 0:
                self.provider_combo.setCurrentIndex(index)

                if matched_model_name:
                    model_index = self.model_combo.findText(matched_model_name)
                    if model_index >= 0:
                        self.model_combo.setCurrentIndex(model_index)

    def _on_save(self) -> None:
        """
        保存按钮槽函数。
        ──────────────────
        将 UI 控件的值写回 self._config，然后持久化到 JSON 文件。
        模型 ID 优先取 ComboBox 的 itemData（即模型的真实 ID），
        如果为空则 fallback 到 currentText（用户手动输入的情况）。
        """
        self._config.api_key = self.api_key_input.text().strip()
        self._config.base_url = self.base_url_input.text().strip()

        # 优先用 itemData (model ID)，空则 fallback 到 currentText
        idx = self.model_combo.currentIndex()
        model_id = self.model_combo.itemData(idx) if idx >= 0 else None
        self._config.model = model_id if model_id else self.model_combo.currentText()

        self._config.system_prompt = self.system_prompt_input.toPlainText().strip()
        self._config.plain_system_prompt = self.plain_system_prompt_input.toPlainText().strip()

        self._config.save(self._config_path)
        self.config_saved.emit(self._config)

        InfoBar.success(
            title="保存成功",
            content="模型配置已更新",
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self
        )

    def _on_test_connection(self) -> None:
        """
        测试连接按钮槽函数（异步）。
        ─────────────────────────────
        流程：
          1. 校验 API 密钥和 Base URL 是否填写
          2. 禁用按钮，启动后台线程发送一个最小化 API 请求
          3. 成功后自动调用 /v1/models 拉取可用模型列表
          4. 通过 _test_result_signal 回调主线程更新 UI
        """
        if self._testing_connection:
            return

        api_key = self.api_key_input.text().strip()
        base_url = self.base_url_input.text().strip()
        idx = self.model_combo.currentIndex()
        model = (self.model_combo.itemData(idx) if idx >= 0 else None) or self.model_combo.currentText()

        if not api_key:
            InfoBar.error(
                title="测试失败",
                content="请先输入API密钥",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self
            )
            return

        if not base_url:
            InfoBar.error(
                title="测试失败",
                content="请先设置Base URL",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self
            )
            return

        self._testing_connection = True
        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中...")

        endpoint = self._get_chat_endpoint(base_url)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "测试连接"}],
            "max_tokens": 10
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        def test_connection_thread():
            success = False
            message = ""
            models = []

            try:
                req = request.Request(endpoint, data=body, headers=headers, method="POST")
                with request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        success = True
                        message = f"成功连接到 {model}"
                    else:
                        success = False
                        message = f"HTTP {resp.status}: 连接失败"
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                message = f"HTTP {exc.code}: {detail[:200]}"
            except Exception as exc:
                message = f"连接错误: {str(exc)}"

            # 连接成功后自动拉取模型列表
            if success:
                try:
                    models = self._fetch_models(base_url, api_key)
                except Exception:
                    models = []

            self._test_result_signal.emit(success, message, models)

        try:
            self._test_thread = Thread(target=test_connection_thread, daemon=True)
            self._test_thread.start()
        except Exception as e:
            self._testing_connection = False
            self.test_btn.setEnabled(True)
            self.test_btn.setText("测试连接")
            InfoBar.error(
                title="测试失败",
                content=f"线程启动失败: {str(e)}",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self
            )

    @pyqtSlot(bool, str, list)
    def _show_test_result(self, success: bool, message: str, models: list):
        """
        测试结果回调（主线程）。
        ────────────────────────
        由 _test_result_signal 信号触发：
        - 成功：显示成功提示，用 API 返回的模型列表更新下拉框
        - 失败：显示错误提示
        """
        if success:
            InfoBar.success(
                title="测试成功",
                content=message,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self
            )
            # 用 API 返回的模型列表更新下拉框（覆盖预设列表）
            if models:
                self.model_combo.clear()
                current_model_id = self._config.model
                matched_index = -1
                for mid in models:
                    self.model_combo.addItem(mid, mid)  # 显示名和 ID 相同
                    if mid == current_model_id:
                        matched_index = self.model_combo.count() - 1
                # 自动选中之前配置的模型
                if matched_index >= 0:
                    self.model_combo.setCurrentIndex(matched_index)
        else:
            InfoBar.error(
                title="测试失败",
                content=message,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self
            )

        # 恢复按钮状态
        self.test_btn.setEnabled(True)
        self.test_btn.setText("测试连接")
        self._testing_connection = False

    def _fetch_models(self, base_url: str, api_key: str) -> list:
        """从 API 动态拉取可用模型列表"""
        models_url = self._get_models_endpoint(base_url)
        req = request.Request(
            models_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        resp = request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        model_list = data.get("data", data.get("models", []))
        result = []
        for m in model_list:
            mid = m.get("id", m.get("name", str(m)))
            result.append(mid)
        return sorted(result)

    @staticmethod
    def _get_chat_endpoint(base_url: str) -> str:
        text = base_url.strip().rstrip("/")
        if text.endswith("/chat/completions"):
            return text
        if text.endswith("/v1"):
            return text + "/chat/completions"
        return text + "/v1/chat/completions"

    @staticmethod
    def _get_models_endpoint(base_url: str) -> str:
        text = base_url.strip().rstrip("/")
        if text.endswith("/v1"):
            return text + "/models"
        if text.endswith("/v1/chat/completions"):
            return text.rsplit("/", 2)[0] + "/models"
        return text + "/v1/models"
