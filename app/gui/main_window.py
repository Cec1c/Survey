import os

from PyQt5.QtGui import QIcon

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, NavigationItemPosition, setThemeColor

from app.gui.pages.chat_page import ChatPage
from app.gui.pages.settings_page import SettingsPage
from app.gui.pages.task_page import TaskPage
from app.gui.pages.skills_page import SkillsPage
from app.gui.services.chat_service import AgentChatService
from app.gui.services.mcp_service import MCPService
from app.gui.state.llm_config import LLMConfig


class MainWindow(FluentWindow):
    """
    主窗口 —— 继承 qfluentwidgets.FluentWindow
    ─────────────────────────────────────────────
    FluentWindow 自带左侧导航栏（sidebar），通过 addSubInterface() 注册子页面。
    子页面会自动出现在侧边栏中，点击即可切换。

    本窗口负责：
    1. 加载配置（LLMConfig）
    2. 创建共享服务（MCPService、AgentChatService）
    3. 实例化 4 个页面并注册到侧边栏
    4. 监听各页面的 config_saved 信号，统一同步配置到服务层
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Survey - Agent GUI (PyQt5)")
        self.setWindowIcon(QIcon())
        self.resize(1080, 760)           # 默认窗口大小
        self.setMinimumWidth(900)        # 最小宽度，防止布局挤压

        setThemeColor("#7a3ff2")         # 全局主题色（紫色），影响侧边栏高亮、按钮等

        try:
            # ── 1. 加载配置文件 ──────────────────────────────────────────
            # config_path 指向 app/config/llm_config.json
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "llm_config.json")
            self.llm_config = LLMConfig.load(config_path)  # 反序列化 JSON → dataclass

            # ── 2. 创建共享服务实例（所有页面共用） ─────────────────────
            # MCPService: 与 IDA Pro 插件通信的 TCP JSON-RPC 客户端
            self.mcp_service = MCPService(
                host=self.llm_config.mcp_host,
                port=self.llm_config.mcp_port,
                timeout_seconds=self.llm_config.mcp_timeout_seconds,
            )
            # AgentChatService: 核心服务，管理 LLM 调用、工具执行、流式输出
            self.chat_service = AgentChatService(self.llm_config, self.mcp_service)

            # ── 3. 实例化 4 个页面 ──────────────────────────────────────
            # 每个页面接收 config + config_path，以便读取和保存配置
            self.chat_page = ChatPage(self.chat_service, config_path, self)
            self.task_page = TaskPage(self.llm_config, config_path, self)
            self.skills_page = SkillsPage(self.llm_config, config_path, self)
            self.settings_page = SettingsPage(self.llm_config, config_path, self)

            # ── 4. 连接信号/槽 ─────────────────────────────────────────
            # chat_page 发出状态变更 → 主窗口状态栏显示
            self.chat_page.status_changed.connect(self._set_status)
            # 任何页面保存配置 → 主窗口统一同步到服务层
            self.settings_page.config_saved.connect(self._on_config_saved)
            self.task_page.config_saved.connect(self._on_config_saved)
            self.skills_page.config_saved.connect(self._on_config_saved)

            # ── 5. 设置 objectName（FluentWindow 路由匹配用） ───────────
            self.chat_page.setObjectName("chat")
            self.task_page.setObjectName("model")
            self.skills_page.setObjectName("skills")
            self.settings_page.setObjectName("settings")

            self._init_pages()
            self._init_status()
        except Exception as e:
            print(f"主窗口初始化时出错: {e}")
            import traceback
            traceback.print_exc()
            # 即使出错也要显示基本窗口
            self._init_pages()
            self._init_status()

    def _init_pages(self) -> None:
        """
        注册子页面到侧边栏导航。
        ──────────────────────────
        addSubInterface(page, icon, label, position) 是 FluentWindow 的方法：
        - page: QWidget 子类实例
        - icon: FluentIcon 枚举值，显示在侧边栏
        - label: 侧边栏显示的文字
        - position: TOP=顶部导航项, BOTTOM=底部（通常放设置类）

        新增页面只需复制任意一个 addSubInterface 块，修改参数即可。
        """
        # 聊天页面 —— 放在侧边栏顶部第一个
        self.addSubInterface(
            self.chat_page,
            FIF.CHAT,          # 聊天图标
            "Chat",
            position=NavigationItemPosition.TOP,
        )
        # Skills 管理页面
        self.addSubInterface(
            self.skills_page,
            FIF.APPLICATION,   # 应用图标
            "Skills",
            position=NavigationItemPosition.TOP,
        )
        # 模型配置页面
        self.addSubInterface(
            self.task_page,
            FIF.CODE,          # 代码图标
            "Model",
            position=NavigationItemPosition.TOP,
        )
        # 设置页面 —— 放在侧边栏底部
        self.addSubInterface(
            self.settings_page,
            FIF.SETTING,       # 齿轮图标
            "Settings",
            position=NavigationItemPosition.BOTTOM,
        )

    def _init_status(self) -> None:
        """初始化状态栏文字。"""
        self._set_status("M1 ready (LLM configurable)")

    def _set_status(self, text: str) -> None:
        """更新窗口底部状态栏文字。"""
        self.setStatusTip(text)

    def _on_config_saved(self, config: LLMConfig) -> None:
        """
        配置保存的统一处理槽函数。
        ────────────────────────────
        当 settings_page / task_page / skills_page 任一页面保存配置时，
        会 emit config_saved 信号，触发此方法：
        1. 更新 AgentChatService 的运行时配置
        2. 更新 MCPService 的连接参数
        3. 同步 ChatPage 的 IDA 开关状态
        """
        self.chat_service.update_config(config)
        self.mcp_service.update(
            host=config.mcp_host,
            port=config.mcp_port,
            timeout_seconds=config.mcp_timeout_seconds,
        )
        self.chat_page.sync_agent_switch_from_config()
        self._set_status("Config saved")
