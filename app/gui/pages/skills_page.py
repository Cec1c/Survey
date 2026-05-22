import os
import json
import re
from typing import List, Dict
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QWidget, QFrame,
                            QScrollArea, QSizePolicy)
from qfluentwidgets import (
    CardWidget, StrongBodyLabel, SubtitleLabel, PrimaryPushButton,
    PushButton, CheckBox, LineEdit, InfoBar, InfoBarPosition,
    ComboBox, BodyLabel, SwitchButton, ToolTipFilter,
    ToolTipPosition, FluentIcon as FIF
)
from PyQt5.QtGui import QFont

from app.gui.state.llm_config import LLMConfig

# 预定义的逆向分析相关Skills（精简版）
BUILTIN_SKILLS = []

_SKILLS_PAGE_QSS = """
QWidget {
    color: #efe9ff;
    background-color: transparent;
}
QLabel {
    color: #efe9ff;
}
QLineEdit, QComboBox {
    color: #efe9ff;
    background-color: rgba(94, 53, 177, 0.2);
    border: 1px solid rgba(126, 63, 242, 0.3);
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #5e35b1;
}
QComboBox:hover, QLineEdit:hover {
    border: 1px solid rgba(126, 63, 242, 0.6);
}
QComboBox::drop-down {
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    color: #efe9ff;
}
CardWidget {
    background-color: rgba(30, 30, 40, 0.8);
    border: 1px solid rgba(126, 63, 242, 0.3);
    border-radius: 8px;
}
CardWidget:hover {
    border: 1px solid rgba(126, 63, 242, 0.5);
}
SkillCard {
    background-color: rgba(40, 40, 50, 0.6);
    border: 1px solid rgba(126, 63, 242, 0.3);
    border-radius: 6px;
}
SkillCard:hover {
    background-color: rgba(50, 50, 65, 0.7);
    border: 1px solid rgba(126, 63, 242, 0.5);
}
QScrollArea {
    border: none;
    background-color: transparent;
}
/* 现代化滚动条样式 */
QScrollBar:vertical {
    background: rgba(60, 60, 80, 0.5);
    width: 8px;
    border-radius: 4px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: rgba(126, 63, 242, 0.6);
    min-height: 20px;
    border-radius: 4px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(126, 63, 242, 0.8);
}
QScrollBar::handle:vertical:pressed {
    background: rgba(126, 63, 242, 1.0);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    background: none;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
QScrollBar:horizontal {
    background: rgba(60, 60, 80, 0.5);
    height: 8px;
    border-radius: 4px;
    margin: 0px;
}
QScrollBar::handle:horizontal {
    background: rgba(126, 63, 242, 0.6);
    min-width: 20px;
    border-radius: 4px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background: rgba(126, 63, 242, 0.8);
}
QScrollBar::handle:horizontal:pressed {
    background: rgba(126, 63, 242, 1.0);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
    background: none;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}
"""


class SkillCard(CardWidget):
    """
    单个 Skill 的卡片组件。
    ────────────────────────
    继承自 qfluentwidgets.CardWidget（圆角卡片容器）。
    每个卡片显示：标题 + 开关、描述、分类标签。
    objectName 设为 "SkillCard" 用于 QSS 样式定位。

    信号：
      toggled(str, bool) — 开关变化时 emit (skill_id, enabled)
    """
    toggled = pyqtSignal(str, bool)

    def __init__(self, skill_data: dict, parent=None):
        super().__init__(parent)
        self.skill_data = skill_data  # 包含 id, name, description, category, enabled 等字段
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # ── 顶部行：标题 + 开关 ──
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        # 标题（StrongBodyLabel = 加粗标签）
        title = StrongBodyLabel(self.skill_data["name"])
        title.setFont(QFont("Segoe UI", 10, QFont.Bold))
        top_row.addWidget(title)

        top_row.addStretch()  # 弹性空间，把开关推到右边

        # 开关按钮（SwitchButton = 滑动开关）
        self.switch = SwitchButton(self)
        self.switch.setChecked(self.skill_data.get("enabled", False))
        self.switch.checkedChanged.connect(self._on_toggled)
        top_row.addWidget(self.switch)

        layout.addLayout(top_row)

        # ── 描述文字 ──
        desc = BodyLabel(self.skill_data["description"])
        desc.setWordWrap(True)  # 自动换行
        desc.setStyleSheet("color: rgba(239, 233, 255, 0.7); font-size: 9pt;")
        layout.addWidget(desc)

        # ── 分类标签 ──
        category = BodyLabel(f"分类: {self.skill_data.get('category', 'General')}")
        category.setStyleSheet("color: rgba(239, 233, 255, 0.5); font-size: 8pt;")
        layout.addWidget(category)

        # objectName 用于 QSS 样式选择器（见 _SKILLS_PAGE_QSS 中的 SkillCard 规则）
        self.setObjectName("SkillCard")

    def _on_toggled(self, checked: bool):
        """开关变化时更新数据并 emit 信号通知 SkillsPage。"""
        self.skill_data["enabled"] = checked
        self.toggled.emit(self.skill_data["id"], checked)


class SkillsPage(QWidget):
    """
    Skills 管理页面。
    ──────────────────
    功能：
      - 扫描 Skills 目录，解析 .md 文件的 YAML frontmatter
      - 以卡片形式展示所有可用 Skills
      - 支持按分类过滤、单个启用/禁用
      - 保存 Skills 配置到 LLMConfig

    信号：
      config_saved(object) — 保存后通知主窗口同步

    布局结构：
      ┌─ 标题 "Skills 管理" ──────────────────────┐
      │  配置卡片（目录路径 + 浏览按钮 + 全局开关） │
      │  "可用 Skills" + 分类过滤下拉框            │
      │  ┌─ QScrollArea ───────────────────────┐   │
      │  │  SkillCard 1                       │   │
      │  │  SkillCard 2                       │   │
      │  │  ...                               │   │
      │  └────────────────────────────────────┘   │
      │  [刷新列表]                [保存配置]      │
      └───────────────────────────────────────────┘
    """

    config_saved = pyqtSignal(object)

    def __init__(self, config: LLMConfig, config_path: str, parent=None):
        super().__init__(parent)
        self._config = config
        self._config_path = config_path
        self._skill_cards: List[SkillCard] = []  # 当前显示的所有卡片引用
        self._loading_skills = False  # 防止重复加载的标志

        try:
            self._setup_ui()
            # 延迟 100ms 加载 skills，避免初始化时布局尚未完成导致的问题
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(100, self._load_skills)
        except Exception as e:
            print(f"SkillsPage初始化时出错: {e}")
            import traceback
            traceback.print_exc()

    def _setup_ui(self):
        """构建页面 UI 布局。"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # ── 页面标题 ──
        title = SubtitleLabel("Skills 管理")
        main_layout.addWidget(title)
        main_layout.addSpacing(8)

        # ── 配置卡片（目录路径 + 全局开关）──
        config_card = self._create_config_card()
        main_layout.addWidget(config_card)

        # ── Skills 列表标题 + 分类过滤器 ──
        skills_header = QHBoxLayout()

        skills_title = StrongBodyLabel("可用 Skills")
        skills_title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        skills_header.addWidget(skills_title)

        skills_header.addStretch()  # 弹性空间，过滤器推到右边

        # 分类下拉框 —— 选择"全部"显示所有，选择具体分类只显示该类
        self.category_filter = ComboBox(self)
        self.category_filter.addItem("全部")
        self.category_filter.addItems(sorted(set(skill["category"] for skill in BUILTIN_SKILLS)))
        self.category_filter.currentTextChanged.connect(self._filter_skills)
        skills_header.addWidget(self.category_filter)

        main_layout.addLayout(skills_header)

        # ── Skills 卡片滚动区域 ──
        # QScrollArea 包裹一个容器 QWidget，动态添加 SkillCard 到容器的布局中
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)      # 容器随滚动区域自动调整大小
        scroll.setFrameShape(QFrame.NoFrame)  # 无边框

        self.skills_container = QWidget()
        self.skills_layout = QVBoxLayout(self.skills_container)
        self.skills_layout.setSpacing(12)
        self.skills_layout.setContentsMargins(0, 0, 0, 0)

        scroll.setWidget(self.skills_container)
        main_layout.addWidget(scroll, stretch=1)  # stretch=1 让滚动区域占满剩余空间

        # ── 底部按钮行 ──
        button_row = QHBoxLayout()
        self.refresh_btn = PushButton("刷新列表", self)
        self.refresh_btn.clicked.connect(self._load_skills)

        self.save_btn = PrimaryPushButton("保存配置", self)
        self.save_btn.clicked.connect(self._on_save)

        button_row.addWidget(self.refresh_btn)
        button_row.addStretch()  # 弹性空间，保存按钮推到右边
        button_row.addWidget(self.save_btn)
        main_layout.addLayout(button_row)

        # 应用页面级 QSS 样式（定义在文件顶部的 _SKILLS_PAGE_QSS）
        self.setStyleSheet(_SKILLS_PAGE_QSS)

    def _create_config_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = StrongBodyLabel("Skills 配置")
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        layout.addWidget(title)

        form_layout = QVBoxLayout()
        form_layout.setSpacing(8)

        # Skills目录
        dir_row = QHBoxLayout()
        dir_label = BodyLabel("Skills 目录:")
        dir_row.addWidget(dir_label)

        self.skills_dir_input = LineEdit(self)
        self.skills_dir_input.setPlaceholderText("输入Skills目录路径")
        self.skills_dir_input.setText(self._config.skills_directory)
        dir_row.addWidget(self.skills_dir_input)

        self.browse_btn = PushButton("浏览...", self)
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.clicked.connect(self._browse_directory)
        dir_row.addWidget(self.browse_btn)

        form_layout.addLayout(dir_row)

        # 全局开关
        switch_row = QHBoxLayout()
        switch_label = BodyLabel("启用 Skills:")
        switch_row.addWidget(switch_label)

        switch_row.addStretch()

        self.skills_switch = SwitchButton(self)
        self.skills_switch.setChecked(self._config.skills_enabled)
        switch_row.addWidget(self.skills_switch)

        form_layout.addLayout(switch_row)

        layout.addLayout(form_layout)
        return card

    def _browse_directory(self):
        from PyQt5.QtWidgets import QFileDialog
        directory = QFileDialog.getExistingDirectory(self, "选择Skills目录")
        if directory:
            self.skills_dir_input.setText(directory)

    def _load_skills(self):
        """
        加载并显示 Skills。
        ────────────────────
        流程：
          1. 清除现有卡片
          2. 扫描 Skills 目录获取文件级 Skills
          3. 合并内置 Skills + 目录扫描结果 + 配置中的自定义 Skills
          4. 根据 active_skills 配置更新启用状态
          5. 应用分类过滤器
          6. 为每个 Skill 创建 SkillCard 并加入滚动布局
        """
        # 防止重复加载（比如快速点击刷新按钮）
        if self._loading_skills:
            return
        self._loading_skills = True

        try:
            # 清除现有卡片
            for card in self._skill_cards:
                card.deleteLater()
            self._skill_cards.clear()

            # 清除布局中的所有项目（包括stretch）
            while self.skills_layout.count():
                item = self.skills_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            # 首先扫描skills目录获取新的skills
            directory_skills = self._scan_skills_directory()

            # 合并内置Skills和扫描到的Skills
            all_skills = BUILTIN_SKILLS.copy()

            # 添加扫描到的目录skills
            for skill in directory_skills:
                if not any(s["id"] == skill["id"] for s in all_skills):
                    all_skills.append(skill)

            # 添加配置中的其他自定义Skills
            if self._config.available_skills:
                for skill in self._config.available_skills:
                    if not any(s["id"] == skill["id"] for s in all_skills):
                        all_skills.append(skill)

            # 根据当前配置更新启用状态
            active_skill_ids = set(self._config.active_skills)
            for skill in all_skills:
                if skill["id"] in active_skill_ids:
                    skill["enabled"] = True

            # 更新分类过滤器
            self._update_category_filter(all_skills)

            # 应用过滤器
            filter_text = self.category_filter.currentText()
            filtered_skills = all_skills
            if filter_text != "全部":
                filtered_skills = [s for s in all_skills if s.get("category") == filter_text]

            # 创建卡片
            for skill in filtered_skills:
                card = SkillCard(skill, self)
                card.toggled.connect(self._on_skill_toggled)
                self.skills_layout.addWidget(card)
                self._skill_cards.append(card)

            # 添加弹性空间
            self.skills_layout.addStretch()

        except Exception as e:
            print(f"加载Skills时出错: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._loading_skills = False

    def _scan_skills_directory(self) -> List[dict]:
        """扫描skills目录，查找新的skill文件和目录"""
        skills = []
        skills_dir = self.skills_dir_input.text().strip()

        if not skills_dir or not os.path.exists(skills_dir):
            return skills

        try:
            # 限制扫描的文件数量，防止过多文件导致问题
            max_files = 50
            file_count = 0

            for item in os.listdir(skills_dir):
                if file_count >= max_files:
                    print(f"已达到最大文件扫描限制: {max_files}")
                    break

                item_path = os.path.join(skills_dir, item)

                try:
                    # 检查是否是目录形式的skill（如idapython）
                    if os.path.isdir(item_path):
                        skill_file = os.path.join(item_path, "SKILL.md")
                        if os.path.exists(skill_file):
                            skill_data = self._parse_skill_file(skill_file, item)
                            if skill_data:
                                skills.append(skill_data)
                                file_count += 1

                    # 检查是否是.md文件
                    elif item.endswith('.md') and item != 'README.md':
                        skill_data = self._parse_skill_file(item_path, item.replace('.md', ''))
                        if skill_data:
                            skills.append(skill_data)
                            file_count += 1

                except Exception as e:
                    print(f"处理文件 {item} 时出错: {e}")
                    continue

        except Exception as e:
            print(f"扫描skills目录时出错: {e}")
            import traceback
            traceback.print_exc()

        return skills

    def _parse_skill_file(self, file_path: str, skill_id: str) -> dict:
        """解析skill文件，提取frontmatter信息"""
        try:
            # 检查文件大小，避免处理过大的文件
            file_size = os.path.getsize(file_path)
            if file_size > 1024 * 1024:  # 限制为1MB
                print(f"跳过过大的skill文件: {file_path} ({file_size} bytes)")
                return None

            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # 限制内容长度，避免内存问题
            if len(content) > 500 * 1024:  # 限制为500KB
                content = content[:500 * 1024]

            # 解析frontmatter
            frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
            if frontmatter_match:
                frontmatter_text = frontmatter_match.group(1)

                # 限制frontmatter长度
                if len(frontmatter_text) > 10 * 1024:  # 限制为10KB
                    frontmatter_text = frontmatter_text[:10 * 1024]

                frontmatter_data = self._parse_yaml_like(frontmatter_text)

                name = frontmatter_data.get('name', skill_id)
                description = frontmatter_data.get('description', '无描述')
                category = frontmatter_data.get('category', 'Reverse Engineering')

                # 限制描述长度
                if len(description) > 500:
                    description = description[:500] + "..."

                return {
                    "id": skill_id,
                    "name": name,
                    "description": description,
                    "category": category,
                    "enabled": False,
                    "file_path": file_path,
                    "content": content[:1000] if len(content) > 1000 else content  # 只保存前1000字符
                }
            else:
                # 没有frontmatter，使用默认值
                return {
                    "id": skill_id,
                    "name": skill_id.replace('_', ' ').title(),
                    "description": "自定义Skill",
                    "category": "Custom",
                    "enabled": False,
                    "file_path": file_path,
                    "content": content[:1000] if len(content) > 1000 else content
                }
        except Exception as e:
            print(f"解析skill文件 {file_path} 时出错: {e}")
            return None

    def _parse_yaml_like(self, text: str) -> dict:
        """简单的YAML解析器，用于解析frontmatter"""
        result = {}
        for line in text.split('\n'):
            line = line.strip()
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip().strip('"\'')
                result[key] = value
        return result

    def _update_category_filter(self, all_skills: List[dict]):
        """更新分类过滤器"""
        current_text = self.category_filter.currentText()

        # 获取所有分类
        categories = set()
        for skill in all_skills:
            if skill.get("category"):
                categories.add(skill["category"])

        # 临时断开信号连接，防止递归
        self.category_filter.currentTextChanged.disconnect(self._filter_skills)

        try:
            # 重新构建过滤器列表
            self.category_filter.clear()
            self.category_filter.addItem("全部")
            for category in sorted(categories):
                self.category_filter.addItem(category)

            # 尝试恢复之前的选择
            index = self.category_filter.findText(current_text)
            if index >= 0:
                self.category_filter.setCurrentIndex(index)
            elif self.category_filter.count() > 0:
                self.category_filter.setCurrentIndex(0)
        finally:
            # 重新连接信号
            self.category_filter.currentTextChanged.connect(self._filter_skills)

    def _filter_skills(self, category: str):
        """根据分类过滤Skills"""
        self._load_skills()

    def _on_skill_toggled(self, skill_id: str, enabled: bool):
        """Skill开关变化"""
        if enabled:
            if skill_id not in self._config.active_skills:
                self._config.active_skills.append(skill_id)
        else:
            if skill_id in self._config.active_skills:
                self._config.active_skills.remove(skill_id)

    def _on_save(self):
        """
        保存按钮槽函数。
        ──────────────────
        将 UI 状态写回配置：
          - Skills 目录路径
          - 全局启用开关
          - 每个 Skill 的启用状态（从 SkillCard 读取）
        然后持久化到 JSON 文件并通知主窗口。
        """
        self._config.skills_directory = self.skills_dir_input.text().strip()
        self._config.skills_enabled = self.skills_switch.isChecked()

        # 从每个 SkillCard 的 skill_data 中读取当前状态
        self._config.available_skills = []
        for card in self._skill_cards:
            skill_data = card.skill_data.copy()
            self._config.available_skills.append(skill_data)

        self._config.save(self._config_path)
        self.config_saved.emit(self._config)

        InfoBar.success(
            title="保存成功",
            content="Skills配置已更新",
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self
        )