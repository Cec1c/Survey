"""Survey CLI 终端 UI — 美化版双线输入框 + Rich 彩色输出"""

import re
import shutil
import sys
from typing import Optional, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.formatted_text import FormattedText
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich import box


# ═══════════════════════════════════════════════════════════════════════════
# 调色板 — Survey 紫色主题 (Knock Principle)
# ═══════════════════════════════════════════════════════════════════════════
# 亮紫：标题文字  #9d7cd8   超亮紫：顶部边框 #d75fff
# 暗紫：字符画    #875fff   深紫：版本号   #5f00ff
C_ACCENT  = "#9D7CD8"
C_ACCENT2 = "#B794F4"
C_SEP     = "#5B3A8C"
C_BORDER  = "#7C5CBF"
C_PROMPT  = "#B794F4"
C_INPUT   = "#E8E4F0"
C_USER    = "#C4B5FD"
C_ASST    = "#67E8F9"
C_TEXT    = "#E2E8F0"
C_REASON  = "#94A3B8"
C_INFO    = "#94A3B8"
C_STATUS  = "#B794F4"
C_ERROR   = "#F87171"
C_SUCCESS = "#4ADE80"
C_WARN    = "#FBBF24"
C_TOOL    = "#2DD4BF"
C_TOOL_OK = "#86EFAC"
C_DIM     = "#64748B"
C_HIGHLIGHT = "#FDE68A"

# ═══════════════════════════════════════════════════════════════════════════
# prompt_toolkit 输入框样式
# ═══════════════════════════════════════════════════════════════════════════
PROMPT_STYLE = PTStyle.from_dict({
    "separator": C_SEP,
    "prompt": f"{C_ACCENT2} bold",
    "input": C_INPUT,
    "placeholder": C_DIM,
    "thinking-title": "#94A3B8 bold",
    "thinking-line": "#7A8A9A",
    "thinking-indicator": C_DIM,
    "thinking-prompt": "#67E8F9 bold",
    "thinking-content": C_TEXT,
})

# ═══════════════════════════════════════════════════════════════════════════
# Rich Console（单例）
# ═══════════════════════════════════════════════════════════════════════════
_console: Optional[Console] = None


def get_console() -> Console:
    global _console
    if _console is None:
        _console = Console(
            highlight=True,
            markup=True,
            force_terminal=True,
            legacy_windows=False,
        )
    return _console


# ═══════════════════════════════════════════════════════════════════════════
# 终端尺寸
# ═══════════════════════════════════════════════════════════════════════════
def terminal_width() -> int:
    return shutil.get_terminal_size().columns


def terminal_height() -> int:
    return shutil.get_terminal_size().lines


# ═══════════════════════════════════════════════════════════════════════════
# 分隔线
# ═══════════════════════════════════════════════════════════════════════════
SEP_CHAR = "─"


def print_separator(style: str = C_SEP) -> None:
    c = get_console()
    c.print(SEP_CHAR * terminal_width(), style=style)


def make_separator_text(width: int) -> str:
    return SEP_CHAR * width


# ═══════════════════════════════════════════════════════════════════════════
# 横幅
# ═══════════════════════════════════════════════════════════════════════════
def print_banner() -> None:
    """Survey 启动横幅 — Keyhole + SURVEY 立体字，紫色 ANSI 主题 + 边框"""

    # ANSI 色码
    R = "\033[0m"
    C_BORDER = "\033[1;38;5;171m"  # 超亮紫 — 边框
    C_KEY = "\033[38;5;99m"        # 暗紫 — Keyhole
    C_NAME = "\033[1;38;5;141m"    # 亮紫 — SURVEY
    C_TAG = "\033[38;5;141m"       # 亮紫 — 描述
    C_VER = "\033[38;5;57m"        # 深紫 — 版本

    _keyhole = [
        "   .+++++++++.             .++++++++.   ",
        "   .++++++.       .....       .+++++.   ",
        "   .++++.    .+++++++++++++.    .+++.   ",
        "   .+++    .+++++++++++++++++.    ++.   ",
        "   .++   .++++++...   ..+++++++.   +.   ",
        "   .+.   .+++.             .++++   ..   ",
        "   .+   .++.       ...       .++.   .   ",
        "   .+.  ..   ..   +   ..  ..   ..   .   ",
        "   .+.  ..    .   .   ..  ..    .   .   ",
        "   .+.  .++.       ...       .++.   .   ",
        "   .+.   .++++.           ..+++.   ..   ",
        "   .++.   .+++++++++++++++++++.   .+.   ",
        "   .+++.    .+++++++++++++++.    .++.   ",
        "   .            .+++++++..    .+++++.   ",
        "          ++.              .++.+++++.   ",
        "         .++++++.... ...++++++++++++.   ",
    ]

    _survey_text = [
        " ███████╗██╗   ██╗██████╗ ██╗   ██╗███████╗██╗   ██╗",
        " ██╔════╝██║   ██║██╔══██╗██║   ██║██╔════╝╚██╗ ██╔╝",
        " ███████╗██║   ██║██████╔╝██║   ██║█████╗   ╚████╔╝",
        " ╚════██║██║   ██║██╔══██╗╚██╗ ██╔╝██╔══╝    ╚██╔╝",
        " ███████║╚██████╔╝██║  ██║ ╚████╔╝ ███████╗   ██║",
        " ╚══════╝ ╚═════╝ ╚═╝  ╚═╝  ╚═══╝  ╚══════╝   ╚═╝",
    ]
    _tag = "LLM-Powered Reverse Engineering Assistant"
    _ver = "v0.2.0 · CLI Edition"

    left_w = max(len(l) for l in _keyhole)
    right_w = max(len(l) for l in _survey_text)
    # tag/ver 以 SURVEY 列宽居中
    _survey_text.append("")
    _survey_text.append(_tag.center(right_w))
    _survey_text.append(_ver.center(right_w))
    right_w = max(right_w, len(_tag), len(_ver))
    gap = 6
    total_w = left_w + gap + right_w

    n_left = len(_keyhole)
    n_right = len(_survey_text)
    right_offset = (n_left - n_right) // 2

    width = terminal_width()
    inner = width - 2
    left_pad = max(0, (inner - total_w) // 2)

    border_top = f"{C_BORDER}╭{'─' * inner}╮{R}"
    border_bot = f"{C_BORDER}╰{'─' * inner}╯{R}"
    BL = f"{C_BORDER}│{R}"
    BR = f"{C_BORDER}│{R}"

    print()
    print(border_top)

    # 主体：Keyhole + SURVEY
    for i in range(n_left):
        left = _keyhole[i].ljust(left_w)
        ri = i - right_offset
        right = _survey_text[ri] if 0 <= ri < n_right else ""
        if ri == n_right - 2:
            c_right = C_TAG
        elif ri == n_right - 1:
            c_right = C_VER
        else:
            c_right = C_NAME
        content_w = left_w + gap + (len(right) if right else 0)
        rpad = inner - left_pad - content_w
        print(f"{BL}{' ' * left_pad}{C_KEY}{left}{R}{' ' * gap}{c_right}{right}{R}{' ' * rpad}{BR}")

    print(border_bot)
    print()


def print_banner_simple() -> None:
    c = get_console()
    width = terminal_width()
    c.print()
    panel = Panel(
        "[bold #9D7CD8]Survey CLI[/bold #9D7CD8] [dim #94A3B8]· LLM逆向工程助手 · v0.2.0[/dim #94A3B8]",
        box=box.HEAVY,
        border_style=C_ACCENT,
        width=min(width, 80),
    )
    c.print(panel)
    c.print()


# ═══════════════════════════════════════════════════════════════════════════
# 消息打印（Rich 输出）
# ═══════════════════════════════════════════════════════════════════════════
def print_user_message(text: str) -> None:
    c = get_console()
    width = min(terminal_width() - 4, 100)
    c.print()
    user_panel = Panel(
        Text(text, style=C_TEXT),
        title="[bold #C4B5FD]你[/bold #C4B5FD]",
        title_align="left",
        border_style=C_ACCENT,
        box=box.ROUNDED,
        width=width,
    )
    c.print(user_panel)


def print_assistant_header() -> None:
    c = get_console()
    c.print(Text("▸ 助手", style=f"bold {C_ASST}"))


def print_stream_chunk(text: str, chunk_type: str = "content") -> None:
    """流式打印内容块 — 跳过不可编码字符（emoji 等）。"""
    global _response_content
    c = get_console()
    safe = []
    for ch in text:
        cp = ord(ch)
        if cp > 0xFFFF or (0xD800 <= cp <= 0xDFFF):
            continue
        safe.append(ch)
    safe_text = "".join(safe)
    if chunk_type == "content":
        _response_content += safe_text
        c.print(safe_text, end="", style=C_TEXT)
    sys.stdout.flush()


def print_error(text: str) -> None:
    c = get_console()
    c.print()
    panel = Panel(
        Text(text, style="bold #FECACA"),
        border_style=C_ERROR,
        box=box.HEAVY,
        title="[bold #F87171]✗ 错误[/bold #F87171]",
        title_align="left",
    )
    c.print(panel)


def print_info(text: str) -> None:
    c = get_console()
    c.print(Text(f"  {text}", style=C_INFO))


def print_status(status_text: str) -> None:
    c = get_console()
    c.print(Text(f"  ◉ {status_text}", style=C_STATUS))


def print_success(text: str) -> None:
    c = get_console()
    c.print(Text(f"  ✓ {text}", style=C_SUCCESS))


def print_warn(text: str) -> None:
    c = get_console()
    c.print(Text(f"  ! {text}", style=C_WARN))


def print_round_separator(round_num: int) -> None:
    c = get_console()
    c.print()
    c.print(Text(f"  · · · 第 {round_num} 轮工具调用 · · ·", style=C_SEP))


# ═══════════════════════════════════════════════════════════════════════════
# 工具调用面板（Rich 输出）
# ═══════════════════════════════════════════════════════════════════════════
def print_tool_panel(
    tool_name: str,
    args: dict,
    result: dict | None = None,
    cached: bool = False,
) -> None:
    c = get_console()
    import json

    # 紧凑一行: 图标 + 工具名 + 关键参数摘要
    icon = "↻" if cached else "⚙"
    color = C_WARN if cached else C_TOOL
    cache_tag = " (缓存)" if cached else ""

    # 提取参数摘要
    arg_summary = ""
    if args:
        parts = []
        for k, v in list(args.items())[:3]:
            sv = str(v)
            if len(sv) > 30:
                sv = sv[:27] + "..."
            parts.append(f"{k}={sv}")
        arg_summary = "  " + ", ".join(parts)

    # 结果状态
    status = ""
    if isinstance(result, dict):
        if not result.get("ok"):
            err = str(result.get("error", ""))[:60]
            status = f"  [{C_ERROR}]失败: {err}[/{C_ERROR}]"
        elif result.get("from_cache"):
            status = ""
        else:
            payload = result.get("result")
            if isinstance(payload, dict):
                n = payload.get("total", "")
                if n or n == 0:
                    status = f"  [{C_DIM}]{n} 项[/{C_DIM}]"
            elif isinstance(payload, list):
                status = f"  [{C_DIM}]{len(payload)} 项[/{C_DIM}]"

    c.print(f"  [{color}]{icon} {tool_name}[/{color}]{arg_summary}{cache_tag}{status}")


# ═══════════════════════════════════════════════════════════════════════════
# 思考（reasoning）管理 — 由 prompt_toolkit 渲染（可动态切换）
# ═══════════════════════════════════════════════════════════════════════════
_response_reasoning: str = ""
_response_content: str = ""
_response_expanded: bool = False
_reasoning_ready: bool = False


def store_reasoning(text: str) -> None:
    global _response_reasoning, _reasoning_ready
    _response_reasoning = text
    _reasoning_ready = True


def reset_response_content() -> None:
    global _response_content
    _response_content = ""


def clear_reasoning() -> None:
    global _response_reasoning, _response_content, _response_expanded, _reasoning_ready
    _response_reasoning = ""
    _response_content = ""
    _response_expanded = False
    _reasoning_ready = False


def print_reasoning_indicator() -> None:
    """通过 Rich 打印折叠指示器（用于 ask 等非交互模式）。
    交互模式下由 prompt_toolkit 负责渲染交互式折叠面板。"""
    if _reasoning_ready and _response_reasoning:
        c = get_console()
        c.print(Text(
            f"▸ 思考 ({len(_response_reasoning)} chars)",
            style=C_DIM,
        ))


def print_reasoning_toggle(event=None) -> None:
    """Ctrl+O：切换思考面板折叠/展开。修改 prompt tokens 后触发重绘。"""
    global _response_expanded
    if not _reasoning_ready or not _response_reasoning:
        return
    _response_expanded = not _response_expanded
    # 先更新 prompt tokens（invalidate 前）
    refresh_prompt_tokens()
    if event is not None:
        event.app.invalidate()


# ═══════════════════════════════════════════════════════════════════════════
# prompt_toolkit 输入框
# ═══════════════════════════════════════════════════════════════════════════

def _make_prompt_message():
    """构建 prompt message 的可变列表。

    prompt_toolkit 内部 FormattedTextControl 存储的是列表引用，
    原地修改该列表后调用 invalidate() 即可触发重绘。
    返回: [list] — 可变的 FormattedText 列表
    """
    width = terminal_width()
    sep = make_separator_text(width)
    return [
        ("class:separator", sep),
        ("", "\n"),
        ("class:prompt", "❯ "),
    ]


def _apply_thinking_tokens(tokens: list) -> None:
    """根据当前思考状态，原地更新 prompt message tokens 列表。

    在分隔线和 ❯ 提示符之间插入：
      - 折叠状态：一行指示器
      - 展开状态：思考面板 + 回复内容重放
    """
    # 移除旧的思考相关 tokens（保留分隔线和 ❯）
    while len(tokens) > 2:
        tokens.pop()
    # 恢复 ❯
    tokens.pop()  # 移除旧 ❯

    if _reasoning_ready and _response_reasoning:
        tokens.append(("", "\n"))  # 在分隔线后加空行
        if _response_expanded:
            # 展开：显示完整思考面板
            tokens.append(("class:thinking-title", "═══ 思考过程 ═══\n"))
            reasoning_lines = _response_reasoning.split("\n")
            max_lines = 80
            for line in reasoning_lines[:max_lines]:
                tokens.append(("class:thinking-line", line + "\n"))
            if len(reasoning_lines) > max_lines:
                tokens.append(("class:thinking-indicator",
                               f"    ... (截断，共 {len(reasoning_lines)} 行)\n"))
            tokens.append(("class:thinking-title", "═══ 思考结束 ═══\n\n"))
            # 重放助手回复
            if _response_content:
                tokens.append(("class:thinking-prompt", "▸ 回复\n"))
                tokens.append(("class:thinking-content", _response_content + "\n\n"))
        else:
            # 折叠：显示指示器
            tokens.append(("class:thinking-indicator",
                           f"▸ 思考 ({len(_response_reasoning)} chars) — Ctrl+O 展开/收起\n\n"))

    tokens.append(("class:prompt", "❯ "))


def create_input_session(
    history_file: str = "~/.survey_cli_history",
    on_ctrl_o=None,
) -> PromptSession:
    """创建带历史和键绑定的 PromptSession。"""
    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    @bindings.add("c-c")
    def _(event):
        event.current_buffer.reset()

    if on_ctrl_o:
        @bindings.add("c-o")
        def _(event):
            on_ctrl_o(event)

    try:
        history = FileHistory(
            history_file.replace("~", __import__("os").path.expanduser("~"))
        )
    except Exception:
        history = None

    return PromptSession(
        history=history,
        style=PROMPT_STYLE,
        key_bindings=bindings,
        multiline=False,
    )


def prompt_user(session: PromptSession) -> str:
    """显示输入框。

    使用可变 list 作为 message——prompt_toolkit 的 FormattedTextControl
    存储的是列表引用，因此 Ctrl+O 切换思考面板时修改列表并 invalidate
    即可实现动态重绘。
    """
    tokens = _make_prompt_message()
    _apply_thinking_tokens(tokens)

    # 存储 tokens 引用供 Ctrl+O handler 修改
    global _active_prompt_tokens
    _active_prompt_tokens = tokens

    try:
        text = session.prompt(
            message=tokens,
            placeholder="输入问题 (Alt+Enter 换行, Enter 发送, /help 帮助)...",
        )
        return text.strip()
    except (EOFError, KeyboardInterrupt):
        return ""


# 当前活跃的 prompt tokens（供 toggle 修改）
_active_prompt_tokens: Optional[list] = None


def refresh_prompt_tokens() -> None:
    """刷新当前活跃的 prompt tokens — 由 print_reasoning_toggle 调用。"""
    if _active_prompt_tokens is not None:
        _apply_thinking_tokens(_active_prompt_tokens)
