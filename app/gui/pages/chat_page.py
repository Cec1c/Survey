import threading
import time
from typing import Any, Dict, Optional, Tuple, Union

from PyQt5.QtCore import QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFontMetrics, QTextDocument
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    IndeterminateProgressRing,
    SmoothScrollDelegate,
    SwitchButton,
    ToolButton,
    TransparentToolButton,
)

from app.gui.services.chat_service import AgentChatService
from app.gui.state.chat_state import ChatState

# ============================================================================
# 聊天页面 (ChatPage)
# ============================================================================
# 整个文件包含 4 个自定义 Widget：
#   1. BubbleMessageWidget   — 通用对话气泡（user/assistant/tool/meta）
#   2. ThoughtBubbleWidget   — "思考过程"气泡（默认折叠）
#   3. ThinkingIndicatorWidget — "思考中..."加载动画
#   4. ChatPage              — 聊天页面主体（管理上述所有组件）
#
# 数据流：
#   用户输入 → _on_send() → 后台线程调用 AgentChatService.run_turn()
#   → 流式回调 emit stream_chunk 信号 → _on_stream_chunk() 更新气泡
#   → 完成后 emit turn_finished 信号 → _on_turn_finished() 收尾
#
# 添加新功能的常见位置：
#   - 添加工具栏按钮：在 _build_ui() 的 controls 布局中添加 ToolButton
#   - 添加新的气泡类型：参考 BubbleMessageWidget 写新 Widget，然后在 _append_xxx() 中使用
#   - 修改流式渲染逻辑：关注 _on_stream_chunk() 和 _flush_stream_content()
# ============================================================================


class BubbleMessageWidget(QWidget):
    """
    通用对话气泡组件。
    ──────────────────
    支持 4 种角色（role），每种有不同的样式：
      - "user"      用户消息，紫色背景，右对齐
      - "assistant"  AI 回复，深色背景，左对齐
      - "tool"      工具调用结果，更深色背景，左对齐，可折叠
      - "meta"      元信息（如工具调用摘要），同 tool 样式

    结构：
      QHBoxLayout(外层) → QFrame#bubbleFrame → QVBoxLayout
        ├── QWidget#bubbleHeader
        │     ├── TransparentToolButton#bubbleToggle (折叠箭头)
        │     └── QLabel#bubbleTitle (标题)
        └── QLabel#bubbleContent (正文)

    信号：
      toggled() — 折叠/展开时 emit
    """

    toggled = pyqtSignal()

    def __init__(
        self,
        role: str,          # 角色：user/assistant/tool/meta
        text: str,          # 正文内容
        title: str = "",    # 标题（工具气泡显示"工具调用: xxx"）
        collapsible: bool = False,      # 是否可折叠
        collapsed: bool = False,        # 初始是否折叠
        collapse_hides_body: bool = False,  # 折叠时是否完全隐藏正文
        parent=None,
    ):
        super().__init__(parent)
        self.role = role
        self.text = text
        self.title = title
        self.collapsible = collapsible
        self.collapsed = collapsed
        self.collapse_hides_body = collapse_hides_body
        self.max_text_width = 520  # 气泡最大文本宽度（像素）
        self._elastic_content_inner: Optional[int] = None  # 弹性宽度缓存

        # 禁用默认背景绘制，由 QFrame#bubbleFrame 的 QSS 控制
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(4, 4, 4, 4)
        self._outer.setSpacing(0)
        self._outer.setAlignment(Qt.AlignTop)

        self._frame = QFrame(self)
        self._frame.setObjectName("bubbleFrame")
        self._frame_layout = QVBoxLayout(self._frame)
        self._frame_layout.setContentsMargins(12, 10, 12, 10)
        self._frame_layout.setSpacing(6)

        self._header = QWidget(self._frame)
        self._header.setObjectName("bubbleHeader")
        self._header.setAttribute(Qt.WA_StyledBackground, True)
        self._header.setAutoFillBackground(False)
        self._header_layout = QHBoxLayout(self._header)
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        self._header_layout.setSpacing(2)

        self._toggle_btn = TransparentToolButton(self._header)
        self._toggle_btn.setObjectName("bubbleToggle")
        self._toggle_btn.setFixedSize(16, 16)
        self._toggle_btn.setIconSize(QSize(12, 12))
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.setAutoFillBackground(False)
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)

        self._title_lbl = QLabel(self._header)
        self._title_lbl.setObjectName("bubbleTitle")
        self._title_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._header_layout.addWidget(self._toggle_btn, 0, Qt.AlignVCenter)
        self._header_layout.addWidget(self._title_lbl, 1, Qt.AlignVCenter)

        self._content_lbl = QLabel(self._frame)
        self._content_lbl.setObjectName("bubbleContent")
        self._content_lbl.setWordWrap(True)
        self._content_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._content_lbl.setAutoFillBackground(False)
        self._content_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._content_lbl.setFocusPolicy(Qt.ClickFocus)
        self._content_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._frame_layout.addWidget(self._header)
        self._frame_layout.addWidget(self._content_lbl)

        if self.role == "user":
            self._outer.addStretch(1)
            self._outer.addWidget(self._frame, 0, Qt.AlignTop)
        else:
            self._outer.addWidget(self._frame, 0, Qt.AlignLeft | Qt.AlignTop)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._apply_frame_style()
        self._apply_header_visibility()
        self._apply_collapse_state()

    def _wrapped_plain_height(self, text: str, width: int) -> int:
        w = max(1, width)
        doc = QTextDocument()
        doc.setDefaultFont(self._content_lbl.font())
        doc.setPlainText(text or "")
        doc.setTextWidth(float(w))
        return max(1, int(doc.size().height()))

    def _intrinsic_meta_tool_size(self) -> QSize:
        """meta/工具气泡：QListWidget 依赖 sizeHint；QLabel 换行高度需手动与 ThoughtBubble 对齐。"""
        om = self._outer.contentsMargins()
        fl = self._frame_layout
        fmarg = fl.contentsMargins()
        sp = fl.spacing()
        bubble_w = max(1, self._frame.maximumWidth())
        total_w = bubble_w + om.left() + om.right()

        show_header = bool(self.collapsible or (self.title or "").strip())
        header_h = 0
        if show_header:
            header_h = max(
                self._toggle_btn.sizeHint().height(),
                self._title_lbl.sizeHint().height(),
                1,
            )

        hide_body = self.collapsible and self.collapsed and self.collapse_hides_body
        if hide_body or not self._content_lbl.isVisible():
            body_h = 0
        else:
            cw = max(1, self._content_lbl.width()) if self._content_lbl.width() > 0 else max(
                1, self._content_lbl.maximumWidth()
            )
            body_h = self._wrapped_plain_height(self._visible_body_text(), cw)

        if show_header and not hide_body:
            inner_h = fmarg.top() + header_h + sp + body_h + fmarg.bottom()
        elif show_header:
            inner_h = fmarg.top() + header_h + fmarg.bottom()
        else:
            inner_h = fmarg.top() + body_h + fmarg.bottom()
        total_h = inner_h + om.top() + om.bottom()
        return QSize(total_w, total_h)

    def sizeHint(self) -> QSize:
        if self.role in ("meta", "tool"):
            return self._intrinsic_meta_tool_size()
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        if self.role in ("meta", "tool"):
            return self._intrinsic_meta_tool_size()
        return super().minimumSizeHint()

    def set_content(self, text: str, max_text_width: int, title: Optional[str] = None) -> None:
        self.text = text
        if title is not None:
            self.title = title
        self.max_text_width = max_text_width
        self._elastic_content_inner = None

        bubble_w = max_text_width + 48
        self._frame.setMaximumWidth(bubble_w)
        self._content_lbl.setMaximumWidth(self._content_wrap_width())
        self._title_lbl.setMaximumWidth(max(80, max_text_width - 22))

        self._apply_header_visibility()
        self._apply_collapse_state()

        # 用户与助手使用弹性宽度；meta/tool 固定换行宽度以便正确测量高度。
        if self.role in ("user", "assistant"):
            self._apply_elastic_frame_width()
        else:
            self._frame.setMinimumWidth(0)
            self._frame.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            inner = max(1, self._content_wrap_width())
            self._content_lbl.setFixedWidth(inner)
            om = self._outer.contentsMargins()
            self.setMaximumWidth(bubble_w + om.left() + om.right())

        self.updateGeometry()

    def _content_wrap_width(self) -> int:
        m = self._frame_layout.contentsMargins()
        full_inner = max(1, self.max_text_width + 48 - m.left() - m.right())
        if self.role in ("user", "assistant") and self._elastic_content_inner is not None:
            return self._elastic_content_inner
        return full_inner

    def _apply_elastic_frame_width(self) -> None:
        """用户 / 助手气泡：短句随内容收窄，长文顶到列宽上限；避免 QFrame 过窄裁字。"""
        m = self._frame_layout.contentsMargins()
        # 测量时始终用满列内宽，勿用 _content_wrap_width()（已含弹性内宽，会算错）
        inner_cap = max(1, self.max_text_width + 48 - m.left() - m.right())
        cap = self.max_text_width + 48
        text = self._visible_body_text()
        fm = QFontMetrics(self._content_lbl.font())
        if not text.strip():
            w = min(cap, 88)
        else:
            rect = fm.boundingRect(QRect(0, 0, inner_cap, 100000), Qt.TextWordWrap, text)
            inner_used = max(1, min(inner_cap, rect.width()))
            w = min(cap, max(52, inner_used + m.left() + m.right() + 8))
        self._frame.setMinimumWidth(w)
        self._frame.setMaximumWidth(w)
        self._frame.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._elastic_content_inner = max(1, w - m.left() - m.right())
        self._content_lbl.setMaximumWidth(self._elastic_content_inner)

    def _visible_body_text(self) -> str:
        raw = self.text or ""
        if not self.collapsible:
            return raw if raw.strip() else "..."
        if self.collapsed and self.collapse_hides_body:
            return ""
        if self.collapsed:
            one = raw.replace("\n", " ").strip()
            if len(one) > 200:
                one = one[:200] + "..."
            return one if one else "..."
        return raw if raw.strip() else "..."

    def _apply_frame_style(self) -> None:
        # Match previous painter bubbles: user fill #5e35b1 / stroke #8e63ea; assistant #1f1a2a; meta/tool #171521.
        if self.role == "user":
            sheet = """
                QFrame#bubbleFrame {
                    background-color: #5e35b1;
                    border: 1px solid #8e63ea;
                    border-radius: 12px;
                }
                QWidget#bubbleHeader {
                    background-color: transparent;
                }
                QToolButton#bubbleToggle {
                    background-color: transparent;
                    border: none;
                    padding: 0px;
                }
                QToolButton#bubbleToggle:hover {
                    background-color: rgba(255, 255, 255, 0.08);
                }
                QLabel#bubbleContent {
                    color: #f3edff;
                    font-size: 13px;
                    background-color: transparent;
                    border: none;
                    selection-background-color: rgba(255, 255, 255, 0.25);
                    selection-color: #ffffff;
                }
                QLabel#bubbleTitle {
                    color: #e8ddff;
                    font-size: 12px;
                    background-color: transparent;
                    border: none;
                }
            """
        elif self.role in ("tool", "meta"):
            sheet = """
                QFrame#bubbleFrame {
                    background-color: #171521;
                    border: 1px solid #3a3150;
                    border-radius: 12px;
                }
                QWidget#bubbleHeader {
                    background-color: transparent;
                }
                QToolButton#bubbleToggle {
                    background-color: transparent;
                    border: none;
                    padding: 0px;
                }
                QToolButton#bubbleToggle:hover {
                    background-color: rgba(255, 255, 255, 0.06);
                }
                QLabel#bubbleContent {
                    color: #cfc5e8;
                    font-size: 12px;
                    background-color: transparent;
                    border: none;
                    selection-background-color: rgba(90, 75, 130, 0.55);
                    selection-color: #f5f0ff;
                }
                QLabel#bubbleTitle {
                    color: #d8cdf4;
                    font-size: 12px;
                    background-color: transparent;
                    border: none;
                }
            """
        else:
            sheet = """
                QFrame#bubbleFrame {
                    background-color: #1f1a2a;
                    border: 1px solid #3a3150;
                    border-radius: 12px;
                }
                QWidget#bubbleHeader {
                    background-color: transparent;
                }
                QToolButton#bubbleToggle {
                    background-color: transparent;
                    border: none;
                    padding: 0px;
                }
                QToolButton#bubbleToggle:hover {
                    background-color: rgba(255, 255, 255, 0.06);
                }
                QLabel#bubbleContent {
                    color: #efe9ff;
                    font-size: 13px;
                    background-color: transparent;
                    border: none;
                    selection-background-color: rgba(90, 75, 130, 0.55);
                    selection-color: #ffffff;
                }
                QLabel#bubbleTitle {
                    color: #d8cdf4;
                    font-size: 12px;
                    background-color: transparent;
                    border: none;
                }
            """
        self._frame.setStyleSheet(sheet)

    def _apply_header_visibility(self) -> None:
        show_header = bool(self.collapsible or (self.title or "").strip())
        self._header.setVisible(show_header)
        if not show_header:
            return
        self._toggle_btn.setVisible(bool(self.collapsible))
        self._title_lbl.setText(self.title or "")
        self._title_lbl.setVisible(bool((self.title or "").strip()))
        # Thinking bar: one flat row, title runs horizontally with the chevron.
        if self.collapse_hides_body:
            self._title_lbl.setWordWrap(False)
        else:
            self._title_lbl.setWordWrap(True)

    def _apply_collapse_state(self) -> None:
        if self.collapsible:
            self._toggle_btn.setIcon(FIF.CHEVRON_RIGHT.icon() if self.collapsed else FIF.ARROW_DOWN.icon())
            self._toggle_btn.setToolTip("展开" if self.collapsed else "收起")
        hide_body = self.collapsible and self.collapsed and self.collapse_hides_body
        self._content_lbl.setVisible(not hide_body)
        self._content_lbl.setText(self._visible_body_text())
        self._frame_layout.setContentsMargins(12, 10, 12, 10)
        self._frame_layout.setSpacing(6)
        self._outer.setContentsMargins(4, 4, 4, 4)

    def _on_toggle_clicked(self) -> None:
        """普通气泡的折叠/展开：仅在可折叠时切换 collapsed 标志并刷新内容。"""
        if not self.collapsible:
            return
        self.collapsed = not self.collapsed
        self._apply_collapse_state()
        self.updateGeometry()
        self.toggled.emit()


class ThoughtBubbleWidget(QWidget):
    """专门用于“思考”内容的气泡，行高完全交给 Qt，避免折叠/展开时裁剪。"""

    toggled = pyqtSignal()

    def __init__(self, text: str, title: str = "", collapsed: bool = True, parent=None):
        super().__init__(parent)
        self.text = text
        self.title = title
        self.collapsed = collapsed
        self.max_text_width = 520

        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(0)
        outer.setAlignment(Qt.AlignTop)

        frame = QFrame(self)
        frame.setObjectName("thoughtFrame")
        self._frame = frame
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        self._layout = layout

        header = QWidget(frame)
        header.setObjectName("thoughtHeader")
        header.setAttribute(Qt.WA_StyledBackground, False)
        header.setAutoFillBackground(False)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(2)
        self._toggle_btn = TransparentToolButton(header)
        self._toggle_btn.setObjectName("thoughtToggle")
        self._toggle_btn.setFixedSize(16, 16)
        self._toggle_btn.setIconSize(QSize(12, 12))
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._on_thought_toggle_clicked)
        self._title_lbl = QLabel(header)
        self._title_lbl.setObjectName("thoughtTitle")
        self._title_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._title_lbl.setWordWrap(False)
        header_layout.addWidget(self._toggle_btn, 0, Qt.AlignVCenter)
        header_layout.addWidget(self._title_lbl, 1, Qt.AlignVCenter)

        self._content_lbl = QLabel(frame)
        self._content_lbl.setObjectName("thoughtContent")
        self._content_lbl.setWordWrap(True)
        self._content_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._content_lbl.setAutoFillBackground(False)
        self._content_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._content_lbl.setFocusPolicy(Qt.ClickFocus)

        layout.addWidget(header)
        layout.addWidget(self._content_lbl)

        outer.addWidget(frame, 0, Qt.AlignLeft | Qt.AlignTop)

        self._apply_style()
        self.set_content(text, self.max_text_width, title)

    def _wrapped_label_height(self, text: str, width: int) -> int:
        """与换行 QLabel 一致的高度；QWidget.sizeHint 对 QLabel 常低估行数。"""
        w = max(1, width)
        doc = QTextDocument()
        doc.setDefaultFont(self._content_lbl.font())
        doc.setPlainText(text or "")
        doc.setTextWidth(float(w))
        return max(1, int(doc.size().height()))

    def _thought_intrinsic_size(self) -> QSize:
        outer = self.layout()
        om = outer.contentsMargins()
        fl = self._frame.layout()
        fmarg = fl.contentsMargins()
        sp = fl.spacing()
        frame_max = max(1, self._frame.maximumWidth())
        total_w = frame_max + om.left() + om.right()

        header_h = max(
            self._toggle_btn.sizeHint().height(),
            self._title_lbl.sizeHint().height(),
            1,
        )
        if self.collapsed:
            inner_h = fmarg.top() + header_h + fmarg.bottom()
        else:
            cw = max(1, self._content_lbl.maximumWidth())
            body_h = self._wrapped_label_height(self.text or "", cw)
            inner_h = fmarg.top() + header_h + sp + body_h + fmarg.bottom()

        total_h = inner_h + om.top() + om.bottom()
        return QSize(total_w, total_h)

    def sizeHint(self) -> QSize:
        return self._thought_intrinsic_size()

    def minimumSizeHint(self) -> QSize:
        return self._thought_intrinsic_size()

    def _apply_style(self) -> None:
        sheet = """
            QFrame#thoughtFrame {
                background-color: #171521;
                border: 1px solid #3a3150;
                border-radius: 12px;
            }
            QWidget#thoughtHeader {
                background-color: transparent;
            }
            QToolButton#thoughtToggle {
                background-color: transparent;
                border: none;
                padding: 0px;
            }
            QToolButton#thoughtToggle:hover {
                background-color: rgba(255, 255, 255, 0.06);
            }
            QLabel#thoughtContent {
                color: #cfc5e8;
                font-size: 12px;
                background-color: transparent;
                border: none;
                selection-background-color: rgba(90, 75, 130, 0.55);
                selection-color: #f5f0ff;
            }
            QLabel#thoughtTitle {
                color: #d8cdf4;
                font-size: 12px;
                background-color: transparent;
                border: none;
            }
        """
        self._frame.setStyleSheet(sheet)

    def set_content(self, text: str, max_text_width: int, title: Optional[str] = None) -> None:
        self.text = text
        if title is not None:
            self.title = title
        self.max_text_width = max_text_width
        bubble_w = max_text_width + 48
        self._frame.setMaximumWidth(bubble_w)
        self._content_lbl.setMaximumWidth(max_text_width)
        # 固定换行宽度，避免 QListWidget 先给过大宽度再缩小时 heightForWidth 与绘制不一致。
        self._content_lbl.setFixedWidth(max_text_width)
        om = self.layout().contentsMargins()
        self.setMaximumWidth(bubble_w + om.left() + om.right())
        self._title_lbl.setText(self.title or "")
        self._toggle_btn.setIcon(FIF.CHEVRON_RIGHT.icon() if self.collapsed else FIF.ARROW_DOWN.icon())
        self._toggle_btn.setToolTip("展开" if self.collapsed else "收起")
        # 先设全文再显隐，避免 QTextDocument 空文档时选区游标报错。
        self._content_lbl.setText(self.text or "...")
        self._content_lbl.setVisible(not self.collapsed)
        self.updateGeometry()

    def _on_thought_toggle_clicked(self) -> None:
        self.collapsed = not self.collapsed
        self.set_content(self.text, self.max_text_width, self.title)
        self.toggled.emit()


class ThinkingIndicatorWidget(QWidget):
    """
    "思考中..."加载指示器。
    ───────────────────────
    显示在列表底部，LLM 正在生成回复时出现。
    包含一个 IndeterminateProgressRing（旋转环）+ 文字标签。
    回复开始流式输出后自动移除。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(34, 0, 0, 0)  # 左侧缩进，与气泡对齐
        layout.setSpacing(8)
        # IndeterminateProgressRing 是 qfluentwidgets 的旋转加载环
        self.ring = IndeterminateProgressRing(self)
        self.ring.setFixedSize(14, 14)
        self.ring.setStrokeWidth(2)
        self.label = QLabel("思考中...", self)
        self.label.setStyleSheet("color:#9f94be; font-size:12px;")
        layout.addWidget(self.ring)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def sizeHint(self) -> QSize:
        return QSize(10, 22)


class ChatPage(QWidget):
    """
    聊天页面主体。
    ──────────────
    这是整个 GUI 最核心的页面，负责：
      1. 用户输入 → 发送给 AgentChatService
      2. 流式接收 LLM 回复并实时显示气泡
      3. 工具调用结果的实时展示
      4. 思考过程的折叠展示
      5. 停止/清空对话

    信号（全部通过 Qt.QueuedConnection 连接，确保线程安全）：
      status_changed(str)           — 状态栏文字变更
      turn_finished(object)         — 一轮对话完成
      stream_chunk(str, str)        — 流式文本片段 (kind, text)
      stream_reset()                — 新一轮 LLM 调用开始
      tool_trace_item(str, obj, obj)— 工具调用完成 (name, args, result)
    """

    status_changed = pyqtSignal(str)
    turn_finished = pyqtSignal(object)
    stream_chunk = pyqtSignal(str, str)        # kind: "reasoning" | "content"
    stream_reset = pyqtSignal()
    tool_trace_item = pyqtSignal(str, object, object)

    def __init__(self, service: AgentChatService, config_path: str, parent=None):
        super().__init__(parent)
        self.state = ChatState()          # 聊天状态（消息列表）
        self.service = service            # AgentChatService 实例
        self._config_path = config_path
        self._is_sending = False          # 是否正在等待 LLM 回复

        # ── 流式渲染的 pending 状态 ──
        # LLM 回复是逐 chunk 到达的，这些变量追踪"当前正在构建的气泡"
        self._pending_assistant_item: Optional[QListWidgetItem] = None   # 回复气泡的列表项
        self._pending_assistant_widget: Optional[BubbleMessageWidget] = None  # 回复气泡组件
        self._pending_assistant_text = ""  # 累积的回复文本
        self._pending_reasoning_item: Optional[QListWidgetItem] = None  # 思考气泡的列表项
        self._pending_reasoning_widget: Optional[ThoughtBubbleWidget] = None  # 思考气泡组件
        self._pending_reasoning_text = ""  # 累积的思考文本
        self._pending_indicator_item: Optional[QListWidgetItem] = None  # "思考中..."指示器
        self._pending_indicator_widget: Optional[ThinkingIndicatorWidget] = None
        self._turn_start_ts = 0.0         # 本轮开始时间戳（用于显示耗时）
        self._turn_tool_count = 0         # 本轮工具调用次数
        self._stop_event: Optional[threading.Event] = None  # 停止信号

        # ── 流式渲染节流定时器 ──
        # 最多每 80ms 刷新一次 UI，避免每个 SSE chunk 都触发 QTextDocument 重计算
        from PyQt5.QtCore import QTimer
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)  # 单次触发，需要手动重启
        self._render_timer.setInterval(80)       # 80ms 间隔
        self._render_timer.timeout.connect(self._flush_stream_content)
        self._dirty_content = False    # 回复文本是否有新内容待刷新
        self._dirty_reasoning = False  # 思考文本是否有新内容待刷新
        self._meta_bubble_count = 0   # 工具调用气泡计数（用于清理旧气泡）

        # ── 信号连接（QueuedConnection 确保跨线程安全）──
        self.turn_finished.connect(self._on_turn_finished, Qt.QueuedConnection)
        self.stream_chunk.connect(self._on_stream_chunk, Qt.QueuedConnection)
        self.stream_reset.connect(self._on_stream_reset, Qt.QueuedConnection)
        self.tool_trace_item.connect(self._on_tool_trace_item, Qt.QueuedConnection)
        self._build_ui()

    def _flush_stream_content(self) -> None:
        """将累积的流式文本刷新到 UI (由节流定时器触发)。"""
        if self._dirty_content and self._pending_assistant_widget:
            self._pending_assistant_widget.set_content(
                self._pending_assistant_text, self._bubble_text_width()
            )
            self._pending_assistant_item.setSizeHint(
                self._pending_assistant_widget.sizeHint()
            )
            self._dirty_content = False
        if self._dirty_reasoning and self._pending_reasoning_widget:
            self._pending_reasoning_widget.set_content(
                self._pending_reasoning_widget.text, self._bubble_text_width(),
                title=self._build_thought_title()
            )
            self._pending_reasoning_item.setSizeHint(
                self._pending_reasoning_widget.sizeHint()
            )
            self._dirty_reasoning = False

    def _build_ui(self) -> None:
        """
        构建聊天页面的 UI 布局。
        ────────────────────────
        布局结构（从上到下）：
          ┌─ QListWidget（消息列表，占满剩余空间）─────────┐
          │  BubbleMessageWidget (user)                    │
          │  ThoughtBubbleWidget (思考过程)                 │
          │  BubbleMessageWidget (assistant)               │
          │  BubbleMessageWidget (meta, 工具调用)           │
          │  ThinkingIndicatorWidget (加载中)               │
          └────────────────────────────────────────────────┘
          ┌─ 模式栏 ──────────────────────────────────────┐
          │  [IDA 分析 (MCP)] 开关  提示文字               │
          └────────────────────────────────────────────────┘
          ┌─ 输入面板 (圆角) ─────────────────────────────┐
          │  [  输入消息...                           ]    │
          │  [删除]                      [发送] [停止]     │
          │  ────────────────────────────────────────────  │
          └────────────────────────────────────────────────┘
        """
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: #0e0c14;")  # 页面背景色
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── 消息列表（QListWidget + 自定义 Widget）──
        self.list_widget = QListWidget(self)
        # Do not use stylesheet padding for inset — it fights the rounded rect and looks patchy.
        # Horizontal-only tightening: viewport margins (content vs. chrome), vertical kept comfortable.
        self.list_widget.setStyleSheet(
            """
            QListWidget {
                background-color: #121018;
                border: 1px solid #2a2436;
                border-radius: 10px;
                outline: none;
                color: #e8e0ff;
                padding: 0px;
            }
            QListWidget::item {
                border: none;
                padding: 0px;
                margin: 0px;
            }
            """
        )
        self.list_widget.setFrameShape(self.list_widget.NoFrame)
        self.list_widget.setVerticalScrollMode(self.list_widget.ScrollPerPixel)
        if hasattr(self.list_widget, "setSpacing"):
            self.list_widget.setSpacing(0)
        hm, vm = 4, 8
        self.list_widget.setViewportMargins(hm, vm, hm, vm)
        self.scroll_delegate = SmoothScrollDelegate(self.list_widget)
        root.addWidget(self.list_widget, 1)

        mode_bar = QWidget(self)
        mode_bar.setStyleSheet("QWidget { color: #cfc5e8; } QLabel { color: #cfc5e8; }")
        mode_row = QHBoxLayout(mode_bar)
        mode_row.setContentsMargins(0, 0, 0, 6)
        mode_row.setSpacing(10)
        self.agent_switch = SwitchButton("IDA 分析 (MCP)", self)
        self.agent_switch.setChecked(self.service.config.use_ida_tools)
        self.agent_switch.checkedChanged.connect(self._on_agent_switch)
        hint = BodyLabel("关闭时仅普通对话；开启后模型可调用 IDA 工具（较慢）", self)
        mode_row.addWidget(self.agent_switch)
        mode_row.addWidget(hint, 1)
        root.addWidget(mode_bar)

        input_panel = QWidget(self)
        input_panel.setObjectName("inputPanel")
        input_panel.setStyleSheet(
            """
            QWidget#inputPanel {
                background-color: #16131f;
                border: 1px solid #2f2740;
                border-radius: 16px;
            }
            """
        )
        panel_layout = QVBoxLayout(input_panel)
        panel_layout.setContentsMargins(10, 8, 10, 8)
        panel_layout.setSpacing(4)

        self.input_box = QTextEdit(input_panel)
        self.input_box.setPlaceholderText("在这里输入消息...")
        self.input_box.setFixedHeight(44)
        self.input_box.setStyleSheet(
            """
            QTextEdit {
                background: transparent;
                border: none;
                color: #efe9ff;
            }
            """
        )
        panel_layout.addWidget(self.input_box)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self.clear_btn = ToolButton(FIF.DELETE, input_panel)
        self.clear_btn.setToolTip("清理对话")
        self.send_btn = ToolButton(FIF.UP, input_panel)
        self.send_btn.setToolTip("发送")
        self.stop_btn = ToolButton(FIF.CLOSE, input_panel)
        self.stop_btn.setToolTip("停止")
        self.clear_btn.setFixedSize(32, 32)
        self.send_btn.setFixedSize(32, 32)
        self.stop_btn.setFixedSize(32, 32)
        self.stop_btn.hide()
        self.send_btn.clicked.connect(self._on_send)
        self.stop_btn.clicked.connect(self._on_stop)
        self.clear_btn.clicked.connect(self._on_clear)
        controls.addWidget(self.clear_btn)
        controls.addStretch(1)
        controls.addWidget(self.send_btn)
        controls.addWidget(self.stop_btn)
        panel_layout.addLayout(controls)

        divider = QWidget(input_panel)
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: #3a3150; border: none;")
        panel_layout.addWidget(divider)
        root.addWidget(input_panel, 0)

    def _bubble_text_width(self) -> int:
        vw = max(1, self.list_widget.viewport().width())
        # Use most of the viewport so the row is not a narrow strip + huge empty band.
        return max(220, int(vw * 0.90))

    def _sync_list_bubble_items(self) -> None:
        """首屏/首条消息时 viewport 尺寸可能尚未提交，与 resize 时一样重算 item 尺寸。"""
        w = self._bubble_text_width()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            wg = self.list_widget.itemWidget(item)
            if isinstance(wg, ThoughtBubbleWidget):
                wg.set_content(wg.text, w, title=wg.title)
                item.setSizeHint(wg.sizeHint())
            elif isinstance(wg, BubbleMessageWidget):
                wg.set_content(wg.text, w, title=wg.title)
                item.setSizeHint(wg.sizeHint())

    def _sync_one_bubble_item(self, item: Optional[QListWidgetItem]) -> None:
        """仅刷新单个更新中的 item，避免流式阶段全量重排导致卡顿。"""
        if item is None:
            return
        wg = self.list_widget.itemWidget(item)
        if wg is None:
            return
        w = self._bubble_text_width()
        if isinstance(wg, ThoughtBubbleWidget):
            wg.set_content(wg.text, w, title=wg.title)
            item.setSizeHint(wg.sizeHint())
        elif isinstance(wg, BubbleMessageWidget):
            wg.set_content(wg.text, w, title=wg.title)
            item.setSizeHint(wg.sizeHint())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_list_bubble_items()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_list_bubble_items()

    def _append_bubble(
        self,
        role: str,
        text: str,
        title: str = "",
        collapsible: bool = False,
        collapsed: bool = False,
        collapse_hides_body: bool = False,
    ) -> Tuple[QListWidgetItem, BubbleMessageWidget]:
        item = QListWidgetItem(self.list_widget)
        widget = BubbleMessageWidget(
            role,
            text,
            title=title,
            collapsible=collapsible,
            collapsed=collapsed,
            collapse_hides_body=collapse_hides_body,
            parent=self.list_widget,
        )
        widget.set_content(text, self._bubble_text_width())
        widget.toggled.connect(lambda: self._on_bubble_toggled(item, widget))
        item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)
        self.list_widget.scrollToBottom()
        QTimer.singleShot(0, self._sync_list_bubble_items)
        return item, widget

    def _append_thought_bubble(
        self,
        text: str,
        title: str = "",
        collapsed: bool = True,
    ) -> Tuple[QListWidgetItem, ThoughtBubbleWidget]:
        item = QListWidgetItem(self.list_widget)
        widget = ThoughtBubbleWidget(text, title=title, collapsed=collapsed, parent=self.list_widget)
        widget.set_content(text, self._bubble_text_width(), title=title)
        widget.toggled.connect(lambda: self._on_bubble_toggled(item, widget))  # type: ignore[arg-type]
        item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)
        self.list_widget.scrollToBottom()
        QTimer.singleShot(0, self._sync_list_bubble_items)
        return item, widget

    def _append_indicator(self) -> Tuple[QListWidgetItem, ThinkingIndicatorWidget]:
        item = QListWidgetItem(self.list_widget)
        widget = ThinkingIndicatorWidget(self.list_widget)
        item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)
        self.list_widget.scrollToBottom()
        QTimer.singleShot(0, self._sync_list_bubble_items)
        return item, widget

    def _on_bubble_toggled(
        self, item: QListWidgetItem, widget: Union[BubbleMessageWidget, ThoughtBubbleWidget]
    ) -> None:
        bar = self.list_widget.verticalScrollBar()
        old_value = bar.value()
        item.setSizeHint(widget.sizeHint())
        bar.setValue(old_value)

        def _defer_resize() -> None:
            widget.updateGeometry()
            item.setSizeHint(widget.sizeHint())
            self.list_widget.verticalScrollBar().setValue(old_value)

        QTimer.singleShot(0, _defer_resize)

    def _update_pending_bubble(
        self,
        item: Optional[QListWidgetItem],
        widget: Optional[Union[BubbleMessageWidget, ThoughtBubbleWidget]],
        text: str,
        auto_scroll: bool = True,
    ) -> None:
        if not item or not widget:
            return
        widget.set_content(text, self._bubble_text_width())
        item.setSizeHint(widget.sizeHint())
        if auto_scroll:
            self.list_widget.scrollToBottom()
        # 流式首段常在这一帧 layout 完成前就算好 sizeHint；仅同步当前 item，避免全量遍历。
        QTimer.singleShot(0, lambda: self._sync_one_bubble_item(item))

    def _remove_indicator(self) -> None:
        if self._pending_indicator_item is not None:
            row = self.list_widget.row(self._pending_indicator_item)
            if row >= 0:
                self.list_widget.takeItem(row)
        self._pending_indicator_item = None
        self._pending_indicator_widget = None

    def _trim_meta_bubbles(self, keep: int = 15) -> None:
        """移除旧的工具调用气泡, 保持列表在可控大小。"""
        removed = 0
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item is None:
                continue
            w = self.list_widget.itemWidget(item)
            if w is not None and getattr(w, "role", "") == "meta":
                if self._meta_bubble_count - removed > keep:
                    self.list_widget.takeItem(i)
                    removed += 1
        self._meta_bubble_count -= removed

    def sync_agent_switch_from_config(self) -> None:
        self.agent_switch.blockSignals(True)
        self.agent_switch.setChecked(self.service.config.use_ida_tools)
        self.agent_switch.blockSignals(False)

    def _on_agent_switch(self, on: bool) -> None:
        self.service.config.use_ida_tools = on
        self.service.config.save(self._config_path)

    def _on_stream_reset(self) -> None:
        # A new LLM round begins: close prior placeholders and append new bubbles.
        self._finalize_round_placeholders()
        self._pending_assistant_text = ""
        self._pending_reasoning_text = ""
        self._pending_reasoning_item, self._pending_reasoning_widget = self._append_thought_bubble(
            "...",
            title=self._build_thought_title(collapsed=True),
            collapsed=True,
        )
        self._pending_assistant_item = None
        self._pending_assistant_widget = None
        self._pending_indicator_item, self._pending_indicator_widget = self._append_indicator()

    def _finalize_round_placeholders(self) -> None:
        """Remove empty placeholders when switching agent rounds."""
        self._remove_indicator()
        if self._pending_reasoning_item and self._pending_reasoning_widget:
            if not self._pending_reasoning_text.strip():
                row = self.list_widget.row(self._pending_reasoning_item)
                if row >= 0:
                    self.list_widget.takeItem(row)
                self._pending_reasoning_item = None
                self._pending_reasoning_widget = None
        if self._pending_assistant_item and self._pending_assistant_widget:
            if not self._pending_assistant_text.strip():
                row = self.list_widget.row(self._pending_assistant_item)
                if row >= 0:
                    self.list_widget.takeItem(row)
                self._pending_assistant_item = None
                self._pending_assistant_widget = None

    def _build_thought_title(self, collapsed: Optional[bool] = None) -> str:
        elapsed = max(0.0, time.time() - self._turn_start_ts) if self._turn_start_ts else 0.0
        chars = len(self._pending_reasoning_text)
        tools = self._turn_tool_count
        _ = collapsed
        return f"思考  ·  {elapsed:.1f}s  ·  {chars}字  ·  {tools}工具"

    def _on_stream_chunk(self, kind: str, text: str) -> None:
        """
        流式文本片段到达的槽函数（主线程，由 stream_chunk 信号触发）。
        ─────────────────────────────────────────────────────────────
        参数：
          kind: "reasoning" = 思考过程, "content" = 正式回复
          text: 本次到达的文本片段

        流式渲染策略：
          - 首次收到某类型文本时，创建对应的气泡 Widget
          - 后续 chunk 只标记 _dirty_xxx = True，由节流定时器统一刷新
          - 80ms 节流避免每个 chunk 都触发 QTextDocument 重排版
        """
        if kind == "reasoning":
            # ── 思考过程文本 ──
            self._pending_reasoning_text += text
            if self._pending_reasoning_item is None:
                # 首次收到思考文本：创建思考气泡（默认折叠）
                self._pending_reasoning_item, self._pending_reasoning_widget = self._append_thought_bubble(
                    self._pending_reasoning_text or "...",
                    title=self._build_thought_title(collapsed=True),
                    collapsed=True,
                )
            else:
                # 后续 chunk：标记脏数据，由定时器统一刷新
                self._dirty_reasoning = True
                self._pending_reasoning_widget.text = self._pending_reasoning_text
                if not self._render_timer.isActive():
                    self._render_timer.start()
            return

        # ── 正式回复文本 ──
        # 收到第一个 content chunk 时，移除"思考中..."指示器
        if self._pending_indicator_item is not None:
            self._remove_indicator()
        if self._pending_assistant_item is None:
            # 首次收到回复文本：创建助手气泡
            self._pending_assistant_item, self._pending_assistant_widget = self._append_bubble("assistant", "")
        self._pending_assistant_text += text
        self._dirty_content = True
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _on_tool_trace_item(self, tool_name: str, args: object, res: object) -> None:
        self._turn_tool_count += 1
        self._meta_bubble_count += 1
        if self._pending_reasoning_item and self._pending_reasoning_widget:
            self._pending_reasoning_widget.set_content(
                self._pending_reasoning_widget.text, self._bubble_text_width(), title=self._build_thought_title()
            )
            self._pending_reasoning_item.setSizeHint(self._pending_reasoning_widget.sizeHint())
        payload = f"args={args}\nresult={res}"
        self._append_bubble(
            "meta",
            payload,
            title=f"工具调用: {tool_name}",
            collapsible=True,
            collapsed=True,
            collapse_hides_body=True,
        )


    def _on_send(self) -> None:
        """
        发送按钮的槽函数 —— 整个聊天流程的起点。
        ────────────────────────────────────────────
        流程：
          1. 读取输入框文本，追加到状态和 UI
          2. 重置 pending 状态（上一轮的气泡引用）
          3. 切换按钮（隐藏发送，显示停止）
          4. 启动后台线程调用 AgentChatService.run_turn()
          5. 后台线程通过 emit 信号将流式数据传回主线程
        """
        if self._is_sending:
            return  # 防止重复发送
        user_text = self.input_box.toPlainText().strip()
        if not user_text:
            return

        # 追加用户消息到状态和 UI
        self.state.append("user", user_text)
        self._append_bubble("user", user_text)
        self.input_box.clear()

        # 重置 pending 状态（新一轮对话）
        self._pending_assistant_item = None
        self._pending_assistant_widget = None
        self._pending_assistant_text = ""
        self._pending_reasoning_item = None
        self._pending_reasoning_widget = None
        self._pending_reasoning_text = ""
        self._turn_start_ts = time.time()  # 记录开始时间（用于显示耗时）
        self._turn_tool_count = 0

        # 切换 UI 状态
        self._is_sending = True
        self._stop_event = threading.Event()  # 停止信号，点击停止按钮时 set()
        self.send_btn.hide()
        self.stop_btn.show()
        self.stop_btn.setEnabled(True)
        self.clear_btn.setEnabled(False)
        self.status_changed.emit("IDA 分析中…" if self.service.config.use_ida_tools else "Thinking…")

        stop_evt = self._stop_event

        def _worker() -> None:
            def round_start() -> None:
                self.stream_reset.emit()

            def chunk(kind: str, text: str) -> None:
                self.stream_chunk.emit(kind, text)

            def tool_tr(name: str, args: Dict[str, Any], res: Any) -> None:
                self.tool_trace_item.emit(name, args, res)

            try:
                result: Dict[str, Any] = self.service.run_turn(
                    user_text,
                    self.state,
                    on_stream_chunk=chunk,
                    on_stream_round_start=round_start,
                    on_tool_trace=tool_tr if self.service.config.use_ida_tools else None,
                    stop_event=stop_evt,
                )
            except Exception as exc:
                result = {"ok": False, "final": f"[Error] Agent internal error: {exc}", "trace": []}
            self.turn_finished.emit(result)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_stop(self) -> None:
        if not self._is_sending:
            return
        self.stop_btn.setEnabled(False)
        if self._stop_event:
            self._stop_event.set()
        # 立即中断 SSE 流
        self.service._api.force_stop()

    def _on_turn_finished(self, result: object) -> None:
        """
        一轮对话完成的槽函数（主线程，由 turn_finished 信号触发）。
        ──────────────────────────────────────────────────────────
        流程：
          1. 恢复 UI 状态（按钮、标志位）
          2. 刷新残留的流式缓冲区
          3. 处理工具调用气泡（如果没实时渲染则补渲染）
          4. 更新最终回复文本
          5. 清理 pending 状态
          6. 更新状态栏
        """
        self._is_sending = False
        self._stop_event = None
        self.stop_btn.hide()
        self.send_btn.show()
        self.send_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)

        # 停止节流定时器，刷新残留内容
        self._render_timer.stop()
        self._flush_stream_content()

        data = result if isinstance(result, dict) else {"ok": False, "final": "[Error] Agent result invalid", "trace": []}

        # 清理旧工具调用气泡 (保留最近 15 个, 避免列表膨胀)
        if self._meta_bubble_count > 15:
            self._trim_meta_bubbles(keep=15)

        if not bool(data.get("trace_rendered_live")):
            for step in data.get("trace", []):
                tool = str(step.get("tool", "unknown_tool"))
                args = step.get("arguments", {})
                res = step.get("result", {})
                self._append_bubble(
                    "meta",
                    f"args={args}\nresult={res}",
                    title=f"工具调用: {tool}",
                    collapsible=True,
                    collapsed=True,
                    collapse_hides_body=True,
                )

        answer_text = str(data.get("final", "")) or "[Error] 模型未返回内容。"
        # 停止时优先使用已流式输出的部分内容（UI 显示和 state 保持一致）
        if data.get("stopped") and self._pending_assistant_text.strip():
            answer_text = self._pending_assistant_text
        self._remove_indicator()
        self.state.append("assistant", answer_text)
        if self._pending_reasoning_item and self._pending_reasoning_widget:
            if self._pending_reasoning_text.strip():
                self._pending_reasoning_widget.set_content(
                    self._pending_reasoning_text, self._bubble_text_width(), title=self._build_thought_title()
                )
                self._pending_reasoning_item.setSizeHint(self._pending_reasoning_widget.sizeHint())
            else:
                row = self.list_widget.row(self._pending_reasoning_item)
                self.list_widget.takeItem(row)
                self._pending_reasoning_item = None
                self._pending_reasoning_widget = None
        if self._pending_assistant_item and self._pending_assistant_widget:
            self._update_pending_bubble(
                self._pending_assistant_item, self._pending_assistant_widget, answer_text
            )
        else:
            self._append_bubble("assistant", answer_text)

        self._pending_assistant_item = None
        self._pending_assistant_widget = None
        self._pending_assistant_text = ""
        self._pending_reasoning_item = None
        self._pending_reasoning_widget = None
        self._pending_reasoning_text = ""
        self._pending_indicator_item = None
        self._pending_indicator_widget = None
        self._turn_start_ts = 0.0
        self._turn_tool_count = 0
        if answer_text.startswith("[Error]"):
            self.status_changed.emit("Request failed")
        elif answer_text.startswith("[Stopped]") or data.get("stopped"):
            self.status_changed.emit("Stopped")
        else:
            self.status_changed.emit("Replied")

    def _on_clear(self) -> None:
        self.state.clear()
        self.list_widget.clear()
        self._pending_assistant_item = None
        self._pending_assistant_widget = None
        self._pending_assistant_text = ""
        self._pending_reasoning_item = None
        self._pending_reasoning_widget = None
        self._pending_reasoning_text = ""
        self._remove_indicator()
        self._turn_start_ts = 0.0
        self._turn_tool_count = 0
        self.status_changed.emit("Session cleared")
