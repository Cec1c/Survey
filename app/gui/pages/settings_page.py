from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from PyQt5.QtGui import QFont
from qfluentwidgets import (
    BodyLabel, CardWidget, InfoBar, InfoBarPosition,
    LineEdit, PrimaryPushButton, PushButton,
    StrongBodyLabel, SubtitleLabel, SwitchButton,
)

from app.gui.state.llm_config import LLMConfig

# ============================================================================
# 设置页面 (SettingsPage)
# ============================================================================
# 功能：MCP 桥接配置、Skills 启用/禁用、高级参数（Temperature）
# 布局：垂直排列 3 个 CardWidget 卡片 + 底部按钮行
#
# 添加新卡片的步骤：
#   1. 写一个 _create_xxx_card() 方法，返回 CardWidget
#   2. 在 __init__ 中调用并 layout.addWidget()
#   3. 在 _load_to_ui() 中加载数据到控件
#   4. 在 _on_save() 中从控件读回数据到 self._config
# ============================================================================

_PAGE_QSS = """
QWidget {
    color: #efe9ff;
}
QLabel {
    color: #efe9ff;
}
QLineEdit {
    color: #efe9ff;
    selection-background-color: #5e35b1;
}
CardWidget {
    background-color: rgba(94, 53, 177, 0.3);
    border-radius: 8px;
}
"""


class SettingsPage(QWidget):
    """
    设置页面 —— 管理 MCP 桥接、Skills、高级参数。
    ──────────────────────────────────────────────────
    信号：
      config_saved(object)     — 保存配置后 emit，MainWindow 监听并同步到服务层
      _mcp_test_signal(bool)   — 内部信号，后台线程测试 MCP 连接后回调主线程更新 UI
    """

    # 通知主窗口：配置已保存，需要同步
    config_saved = pyqtSignal(object)
    # 内部跨线程信号：MCP 测试结果（后台 socket 测试 → 主线程更新 UI）
    _mcp_test_signal = pyqtSignal(bool)

    def __init__(self, config: LLMConfig, config_path: str, parent=None):
        super().__init__(parent)
        self._config = config         # 当前运行时配置（dataclass）
        self._config_path = config_path  # JSON 配置文件路径，保存时写入

        # 连接内部信号：后台线程完成 MCP 测试后，调用 _mcp_test_done 更新 UI
        self._mcp_test_signal.connect(self._mcp_test_done)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = SubtitleLabel("设置")
        layout.addWidget(title)
        layout.addSpacing(8)

        # ── MCP 桥接卡片 ──────────────────────────────
        mcp_card = self._create_mcp_card()
        layout.addWidget(mcp_card)

        # ── Skills 卡片 ────────────────────────────────
        skills_card = self._create_skills_card()
        layout.addWidget(skills_card)

        # ── 高级选项卡片 ──────────────────────────────
        advanced_card = self._create_advanced_card()
        layout.addWidget(advanced_card)

        # ── 按钮行 ─────────────────────────────────────
        row = QHBoxLayout()
        self.save_btn = PrimaryPushButton("保存配置", self)
        self.save_btn.setFixedWidth(120)
        self.save_btn.clicked.connect(self._on_save)

        self.mcp_test_btn = PushButton("测试 MCP 连接", self)
        self.mcp_test_btn.setFixedWidth(140)
        self.mcp_test_btn.clicked.connect(self._on_test_mcp)



        row.addWidget(self.save_btn)
        row.addWidget(self.mcp_test_btn)
        
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)

        self.setStyleSheet(_PAGE_QSS)
        self._load_to_ui()

    def _create_mcp_card(self) -> CardWidget:
        """
        创建 MCP 桥接配置卡片。
        ──────────────────────────
        MCP (Model Context Protocol) 桥接是 IDA Pro 插件与本程序的通信通道。
        这里配置连接参数：主机地址、端口号、超时时间。
        测试按钮在 __init__ 的按钮行中创建，点击后用 socket 探测 IDA 插件是否在线。
        """
        card = CardWidget(self)  # CardWidget 是 qfluentwidgets 的圆角卡片容器
        cl = QVBoxLayout(card)   # 卡片内部用垂直布局
        cl.setContentsMargins(16, 16, 16, 16)  # 内边距 16px
        cl.setSpacing(12)                       # 子控件间距 12px

        # 卡片标题
        header = StrongBodyLabel("MCP 桥接 (IDA Pro)")
        header.setFont(QFont("Segoe UI", 11, QFont.Bold))
        cl.addWidget(header)

        # 连接状态标签（点击"测试 MCP 连接"按钮后更新）
        self.mcp_status_label = BodyLabel("状态: 未检测")
        self.mcp_status_label.setStyleSheet("color: #94a3b8;")  # 灰色
        cl.addWidget(self.mcp_status_label)

        # 表单布局：左侧标签 + 右侧输入框
        form = QFormLayout()
        form.setSpacing(10)

        self.mcp_host_input = LineEdit(self)
        self.mcp_host_input.setPlaceholderText("127.0.0.1")
        form.addRow("MCP 主机:", self.mcp_host_input)

        self.mcp_port_input = LineEdit(self)
        self.mcp_port_input.setPlaceholderText("31337")
        form.addRow("MCP 端口:", self.mcp_port_input)

        self.mcp_timeout_input = LineEdit(self)
        self.mcp_timeout_input.setPlaceholderText("20.0")
        form.addRow("超时 (秒):", self.mcp_timeout_input)

        cl.addLayout(form)
        return card

    def _create_skills_card(self) -> CardWidget:
        card = CardWidget(self)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 16, 16, 16)
        cl.setSpacing(12)

        header = StrongBodyLabel("Skills")
        header.setFont(QFont("Segoe UI", 11, QFont.Bold))
        cl.addWidget(header)

        form = QFormLayout()
        form.setSpacing(10)

        self.skills_enabled_switch = SwitchButton(self)
        form.addRow("启用 Skills:", self.skills_enabled_switch)

        self.skills_dir_input = LineEdit(self)
        self.skills_dir_input.setPlaceholderText("skills")
        form.addRow("Skills 目录:", self.skills_dir_input)

        cl.addLayout(form)
        return card

    def _create_advanced_card(self) -> CardWidget:
        card = CardWidget(self)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 16, 16, 16)
        cl.setSpacing(12)

        header = StrongBodyLabel("高级选项")
        header.setFont(QFont("Segoe UI", 11, QFont.Bold))
        cl.addWidget(header)

        form = QFormLayout()
        form.setSpacing(10)

        self.temperature_input = LineEdit(self)
        self.temperature_input.setPlaceholderText("0.2")
        form.addRow("Temperature:", self.temperature_input)

        cl.addLayout(form)
        return card

    def _load_to_ui(self) -> None:
        """将配置数据加载到 UI 控件。页面初始化时调用一次。"""
        self.mcp_host_input.setText(self._config.mcp_host)
        self.mcp_port_input.setText(str(self._config.mcp_port))
        self.mcp_timeout_input.setText(str(self._config.mcp_timeout_seconds))
        self.skills_enabled_switch.setChecked(self._config.skills_enabled)
        self.skills_dir_input.setText(self._config.skills_directory)
        self.temperature_input.setText(str(self._config.temperature))

    def _on_save(self) -> None:
        """
        保存按钮的槽函数。
        ──────────────────
        流程：UI 控件 → self._config (dataclass) → 写入 JSON 文件 → emit 信号通知主窗口
        """
        # 读取 UI 值，带默认值兜底（用户输入非法时 fallback）
        self._config.mcp_host = self.mcp_host_input.text().strip() or "127.0.0.1"
        try:
            self._config.mcp_port = int(self.mcp_port_input.text().strip())
        except ValueError:
            self._config.mcp_port = 31337
        try:
            self._config.mcp_timeout_seconds = float(self.mcp_timeout_input.text().strip())
        except ValueError:
            self._config.mcp_timeout_seconds = 20.0
        try:
            self._config.temperature = float(self.temperature_input.text().strip())
        except ValueError:
            self._config.temperature = 0.2

        self._config.skills_enabled = self.skills_enabled_switch.isChecked()
        self._config.skills_directory = self.skills_dir_input.text().strip()

        # 持久化到 JSON 文件
        self._config.save(self._config_path)
        # 通知主窗口：配置已变更，需要同步到 AgentChatService 和 MCPService
        self.config_saved.emit(self._config)

        InfoBar.success(
            title="保存成功",
            content="设置已更新",
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_test_mcp(self) -> None:
        """
        测试 MCP 连接按钮的槽函数。
        ─────────────────────────────
        跨线程模式：
          主线程：禁用按钮，显示"测试中..."
          后台线程：socket.create_connection() 尝试连接 IDA 插件
          后台线程完成：emit _mcp_test_signal(bool) → 主线程 _mcp_test_done() 更新 UI
        """
        from threading import Thread
        import socket

        host = self.mcp_host_input.text().strip() or "127.0.0.1"
        try:
            port = int(self.mcp_port_input.text().strip())
        except ValueError:
            port = 31337

        # 禁用按钮，防止重复点击
        self.mcp_test_btn.setEnabled(False)
        self.mcp_test_btn.setText("测试中...")
        self.mcp_status_label.setText("状态: 检测中...")
        self.mcp_status_label.setStyleSheet("color: #facc15;")  # 黄色

        def test_thread():
            ok = False
            try:
                # 尝试 TCP 连接，5 秒超时
                sock = socket.create_connection((host, port), timeout=5)
                sock.close()
                ok = True
            except Exception:
                ok = False
            # 通过信号将结果传回主线程（Qt 要求 UI 操作在主线程）
            self._mcp_test_signal.emit(ok)

        # daemon=True 表示主窗口关闭时线程自动退出
        Thread(target=test_thread, daemon=True).start()

    def _mcp_test_done(self, ok: bool):
        """
        MCP 测试结果回调（主线程）。
        ──────────────────────────────
        由 _mcp_test_signal 信号触发，更新按钮状态和状态标签。
        InfoBar 是 qfluentwidgets 的顶部弹出提示条。
        """
        self.mcp_test_btn.setEnabled(True)
        self.mcp_test_btn.setText("测试 MCP 连接")
        if ok:
            # 连接成功：绿色状态 + 成功提示条
            self.mcp_status_label.setText("状态: ● 已连接")
            self.mcp_status_label.setStyleSheet("color: #4ade80;")  # 绿色
            InfoBar.success(
                title="MCP 连接成功",
                content="IDA Pro 桥接服务器响应正常",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
        else:
            # 连接失败：红色状态 + 错误提示条
            self.mcp_status_label.setText("状态: ○ 未连接")
            self.mcp_status_label.setStyleSheet("color: #f87171;")  # 红色
            InfoBar.error(
                title="MCP 连接失败",
                content="无法连接到 IDA Pro 桥接，请确认 IDA 已启动并加载插件",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
