"""Survey CLI Runner — 桥接 AgentChatService 与 CLI UI"""

import json
import os
import sys
from typing import Any, Dict, Optional

# 强制 UTF-8 编码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rich.text import Text as RichText

from app.gui.services.chat_service import AgentChatService
from app.gui.services.mcp_service import MCPService
from app.gui.services.skills_service import SkillsService
from app.gui.state.llm_config import LLMConfig
from app.gui.state.chat_state import ChatState, ChatMessage
from cli.ui import (
    get_console,
    print_banner,
    print_banner_simple,
    print_user_message,
    print_assistant_header,
    print_stream_chunk,
    print_tool_panel,
    print_error,
    print_info,
    print_status,
    print_success,
    print_warn,
    print_round_separator,
    print_reasoning_toggle,
    print_reasoning_indicator,
    store_reasoning,
    clear_reasoning,
    reset_response_content,
    create_input_session,
    prompt_user,
)


class CLIRunner:
    """CLI 运行器：配置加载、服务初始化、对话循环。"""

    def __init__(self, config_path: str = "app/config/llm_config.json"):
        self.config_path = config_path
        self.config: LLMConfig = LLMConfig()
        self.mcp_service: Optional[MCPService] = None
        self.chat_service: Optional[AgentChatService] = None
        self.state: ChatState = ChatState()
        self._round_num: int = 0
        self._tool_count: int = 0
        self._reasoning_buf: list = []
        self._resolved_config_path: str = ""
        self._ida_available: bool = False
        self._using_tools: bool = False

        self._load_config()
        self._init_services()
        self._detect_ida()

    # ── 配置与初始化 ────────────────────────────────────────────────────

    def _load_config(self) -> None:
        full_path = os.path.join(_PROJECT_ROOT, self.config_path)
        if not os.path.exists(full_path):
            if os.path.exists(self.config_path):
                full_path = self.config_path
            else:
                print_error(f"配置文件不存在: {full_path}")
                print_info("将使用默认配置。")
        self.config = LLMConfig.load(full_path)
        self._resolved_config_path = full_path
        NO_EMOJI = " Do not use emoji in your responses. 请勿在回复中使用任何 emoji 表情符号。"
        if NO_EMOJI not in self.config.system_prompt:
            self.config.system_prompt += NO_EMOJI

    def _init_services(self) -> None:
        self.mcp_service = MCPService(
            host=self.config.mcp_host,
            port=self.config.mcp_port,
            timeout_seconds=self.config.mcp_timeout_seconds,
        )
        self.chat_service = AgentChatService(
            config=self.config,
            mcp_service=self.mcp_service,
        )

    def _detect_ida(self) -> None:
        """检测 IDA Bridge 是否可用，并自动启用/降级工具模式。"""
        # 始终尝试连接（即使配置中 use_ida_tools=false），
        # 以便用户在 IDA 插件就绪后无需手动改配置即可使用工具模式。
        if self.check_connection():
            self._ida_available = True
            self._using_tools = True
            if not self.config.use_ida_tools:
                self.config.use_ida_tools = True
                if self.chat_service:
                    self.chat_service.update_config(self.config)
        elif self.config.use_ida_tools:
            # 配置期望工具模式但连接不上 → 自动降级
            self._ida_available = False
            self._using_tools = False
            self.config.use_ida_tools = False
            if self.chat_service:
                self.chat_service.update_config(self.config)
        else:
            self._ida_available = False
            self._using_tools = False

    def reload_config(self) -> None:
        self._load_config()
        self._init_services()
        self._detect_ida()
        print_info("配置已重载。")

    # ── 连接检查 ────────────────────────────────────────────────────────

    def check_connection(self) -> bool:
        try:
            result = self.mcp_service.call_tool("check_connection", {})
            return bool(result.get("ok") and result.get("result"))
        except Exception:
            return False

    def _get_mode_label(self) -> str:
        """获取当前模式标签。"""
        if self._ida_available:
            return "[#4ADE80]● IDA 工具模式[/#4ADE80]"
        else:
            return "[#94A3B8]○ 纯聊天模式[/#94A3B8]"

    def print_status_bar(self) -> None:
        """打印状态栏 — 一行概览关键信息。"""
        c = get_console()
        # 模型名截断
        model = self.config.model
        if len(model) > 30:
            model = model[:27] + "..."

        items = [
            f"[bold #A78BFA]模型[/bold #A78BFA] {model}",
            self._get_mode_label(),
        ]
        if self.config.skills_enabled:
            skills_count = len(self.chat_service.skills_service.get_all_skills())
            items.append(f"[bold #A78BFA]Skills[/bold #A78BFA] {skills_count}")

        line = "  " + "  │  ".join(items)
        c.print(line)
        c.print()

    def terminal_width(self) -> int:
        import shutil
        return shutil.get_terminal_size().columns

    # ── 流式回调 ────────────────────────────────────────────────────────

    def _on_stream_chunk(self, chunk_type: str, text: str) -> None:
        if chunk_type == "reasoning":
            self._reasoning_buf.append(text)
        elif chunk_type == "content":
            if self._reasoning_buf and not getattr(self, "_indicator_shown", False):
                self._indicator_shown = True
                store_reasoning("".join(self._reasoning_buf))
                print_reasoning_indicator()
            print_stream_chunk(text, "content")

    def _on_round_start(self) -> None:
        self._round_num += 1
        if self._round_num > 1:
            print_round_separator(self._round_num)
        self._indicator_shown = False
        self._reasoning_buf.clear()
        reset_response_content()

    def _on_tool_trace(self, tool_name: str, args: dict, result: dict) -> None:
        self._tool_count += 1
        cached = result.get("cached", False) if isinstance(result, dict) else False
        print_tool_panel(tool_name, args, result, cached=cached)

    # ── 内置命令 ────────────────────────────────────────────────────────

    def _handle_internal_command(self, text: str) -> Optional[str]:
        text = text.strip()

        if text in ("/exit", "/quit", "/q"):
            print_info("再见！")
            return "__EXIT__"

        if text == "/clear":
            self.state.clear()
            self.chat_service._persistent_tool_cache.clear()
            self.chat_service.workflow_service.reset_workflow()
            print_info("对话历史已清空。")
            return ""

        if text == "/status":
            self.print_status_bar()
            return ""

        if text == "/reload":
            self.reload_config()
            self.state.clear()
            return ""

        if text in ("/help", "/?"):
            self._print_help()
            return ""

        if text.startswith("/skills"):
            self._list_skills()
            return ""

        if text == "/reset":
            self.chat_service.workflow_service.reset_workflow()
            self.chat_service._persistent_tool_cache.clear()
            print_info("工作流已重置。")
            return ""

        if text in ("/model", "/current-model"):
            self._show_current_model()
            return ""

        if text == "/models":
            self._list_models()
            return ""

        if text.startswith("select "):
            self._select_model(text)
            return ""

        return None

    def _print_help(self) -> None:
        c = get_console()
        from rich.panel import Panel
        from rich import box

        help_md = """\
[bold #C4B5FD]内置命令[/bold #C4B5FD]
  [bold #A78BFA]/exit, /quit, /q[/bold #A78BFA]    退出程序
  [bold #A78BFA]/clear[/bold #A78BFA]               清空对话历史
  [bold #A78BFA]/status[/bold #A78BFA]              显示当前状态
  [bold #A78BFA]/model[/bold #A78BFA]               显示当前模型和提供商
  [bold #A78BFA]/models[/bold #A78BFA]              获取可用模型列表
  [bold #A78BFA]/reload[/bold #A78BFA]              重载配置文件
  [bold #A78BFA]/reset[/bold #A78BFA]               重置工作流
  [bold #A78BFA]/skills[/bold #A78BFA]              列出可用 Skills
  [bold #A78BFA]/help, /?[/bold #A78BFA]            显示此帮助

[bold #C4B5FD]快捷键[/bold #C4B5FD]
  [bold #A78BFA]Enter[/bold #A78BFA]                发送消息
  [bold #A78BFA]Alt+Enter[/bold #A78BFA]            插入换行
  [bold #A78BFA]Ctrl+C[/bold #A78BFA]               取消当前输入
  [bold #A78BFA]Ctrl+O[/bold #A78BFA]               展开/折叠思考内容"""

        panel = Panel(
            help_md,
            title="[bold #C4B5FD]帮助[/bold #C4B5FD]",
            title_align="left",
            border_style="#5B5198",
            box=box.ROUNDED,
        )
        c.print(panel)

    def _list_skills(self) -> None:
        all_skills = self.chat_service.skills_service.get_all_skills()
        if not all_skills:
            print_info("没有找到任何 Skills。")
            return

        c = get_console()
        c.print()
        c.print("[bold #C4B5FD]可用 Skills[/bold #C4B5FD]")
        for skill_id, skill in sorted(all_skills.items()):
            status = "[#4ADE80]✓[/#4ADE80]" if skill.enabled else "[#64748B]○[/#64748B]"
            c.print(f"  {status} [bold]{skill.name}[/bold] [#64748B]({skill.category})[/#64748B]")
            c.print(f"    [#94A3B8]{skill.description[:80]}[/#94A3B8]")
        c.print()

    # ── 模型管理 ────────────────────────────────────────────────────────

    def _show_current_model(self) -> None:
        c = get_console()
        base = self.config.base_url.rstrip("/")
        provider = "自定义"
        if "deepseek" in base:
            provider = "DeepSeek"
        elif "openai" in base:
            provider = "OpenAI"
        elif "anthropic" in base:
            provider = "Anthropic"
        elif "localhost" in base or "127.0.0.1" in base:
            provider = "本地"

        c.print()
        c.print(f"  [bold #A78BFA]提供商[/bold #A78BFA]   {provider}")
        c.print(f"  [bold #A78BFA]API 地址[/bold #A78BFA]  {base}")
        c.print(f"  [bold #A78BFA]当前模型[/bold #A78BFA]  [bold #4ADE80]{self.config.model}[/bold #4ADE80]")
        c.print(f"  [bold #A78BFA]运行模式[/bold #A78BFA]  {self._get_mode_label()}")
        c.print()
        c.print(RichText("  /models 获取可用模型列表", style="#64748B"))
        c.print()

    def _fetch_models(self) -> list:
        import urllib.request
        import urllib.error

        base = self.config.base_url.rstrip("/")
        if base.endswith("/v1"):
            models_url = base + "/models"
        elif base.endswith("/v1/chat/completions"):
            models_url = base.rsplit("/", 2)[0] + "/models"
        else:
            models_url = base + "/v1/models"

        try:
            req = urllib.request.Request(
                models_url,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode("utf-8"))
            models = data.get("data", data.get("models", []))
            result = []
            for m in models:
                mid = m.get("id", m.get("name", str(m)))
                result.append(mid)
            return sorted(result)
        except Exception as exc:
            print_info(f"无法获取模型列表: {exc}")
            return []

    def _list_models(self) -> None:
        c = get_console()
        c.print()
        c.print("[bold #A78BFA]正在获取模型列表...[/bold #A78BFA]")

        models = self._fetch_models()
        if not models:
            c.print("  (无法获取模型列表，请检查网络和 API Key)", style="#64748B")
            c.print()
            return

        self._cached_models = models

        c.print()
        c.print("[bold #C4B5FD]可用模型[/bold #C4B5FD]")
        for i, m in enumerate(models):
            if m == self.config.model:
                c.print(f"  [#4ADE80][{i}] ▶ {m} (当前)[/#4ADE80]")
            else:
                c.print(f"  [bold #A78BFA][{i}][/bold #A78BFA] [#E2E8F0]{m}[/#E2E8F0]")
        c.print()
        c.print("  输入 [bold #A78BFA]select <数字>[/bold #A78BFA] 切换模型", style="#64748B")
        c.print()

    def _select_model(self, text: str) -> None:
        c = get_console()
        parts = text.strip().split()
        if len(parts) != 2:
            c.print("  用法: select <数字>", style="#F87171")
            return

        try:
            idx = int(parts[1])
        except ValueError:
            c.print(f"  无效的数字: {parts[1]}", style="#F87171")
            return

        if not hasattr(self, "_cached_models") or not self._cached_models:
            c.print("  请先运行 /models 获取模型列表", style="#F87171")
            return

        if idx < 0 or idx >= len(self._cached_models):
            c.print(f"  序号超出范围 (0-{len(self._cached_models)-1})", style="#F87171")
            return

        new_model = self._cached_models[idx]
        old_model = self.config.model
        self.config.model = new_model
        self.chat_service.update_config(self.config)
        try:
            save_path = self._resolved_config_path or os.path.join(_PROJECT_ROOT, self.config_path)
            self.config.save(save_path)
        except Exception:
            pass

        c.print(f"  [#4ADE80]已切换:[/#4ADE80] {old_model} → [bold #4ADE80]{new_model}[/bold #4ADE80]")

    # ── 主流程 ───────────────────────────────────────────────────────────

    def ask_once(self, question: str) -> None:
        """单次提问模式。"""
        print_banner_simple()
        print_user_message(question)

        if not self.config.api_key.strip():
            print_error("API Key 未配置。请在配置文件中设置 api_key。")
            return

        print_assistant_header()
        self._round_num = 0
        self._tool_count = 0
        self._reasoning_buf.clear()
        self._indicator_shown = False

        result = self.chat_service.run_turn(
            user_text=question,
            state=self.state,
            on_stream_chunk=self._on_stream_chunk,
            on_stream_round_start=self._on_round_start,
            on_tool_trace=self._on_tool_trace,
        )

        c = get_console()
        c.print()
        c.print()

        if self._reasoning_buf and not self._indicator_shown:
            reasoning_text = "".join(self._reasoning_buf)
            store_reasoning(reasoning_text)
            print_reasoning_indicator()

        if not result.get("ok"):
            print_error(result.get("final", "未知错误"))
        else:
            final_text = result.get("final", "")
            if final_text:
                self.state.append("assistant", final_text)

    def run_interactive(self) -> None:
        """交互式 REPL 循环。"""
        print_banner()
        self.print_status_bar()

        if not self.config.api_key.strip():
            print_error("API Key 未配置。请在配置文件中设置 api_key。")
            print_info(f"配置文件位置: {self.config_path}")
            return

        # IDA 状态提示
        if self.config.use_ida_tools and self._ida_available:
            print_success("IDA Bridge 已连接，工具调用模式就绪。")
        elif not self._ida_available:
            print_info("纯聊天模式（IDA 未连接，工具调用已自动禁用）。")

        print_info("输入 /help 获取帮助，/exit 退出。")

        session = create_input_session(on_ctrl_o=print_reasoning_toggle)

        while True:
            user_text = prompt_user(session)

            if not user_text:
                continue

            # 内置命令
            cmd_result = self._handle_internal_command(user_text)
            if cmd_result == "__EXIT__":
                break
            if cmd_result is not None:
                continue

            # 发送给 LLM
            print_user_message(user_text)
            self.state.append("user", user_text)
            self._round_num = 0
            self._tool_count = 0
            self._reasoning_buf.clear()
            clear_reasoning()
            self._indicator_shown = False

            print_assistant_header()

            try:
                result = self.chat_service.run_turn(
                    user_text=user_text,
                    state=self.state,
                    on_stream_chunk=self._on_stream_chunk,
                    on_stream_round_start=self._on_round_start,
                    on_tool_trace=self._on_tool_trace,
                )

                c = get_console()
                c.print()
                c.print()

                if self._reasoning_buf and not self._indicator_shown:
                    reasoning_text = "".join(self._reasoning_buf)
                    store_reasoning(reasoning_text)
                    print_reasoning_indicator()

                if not result.get("ok"):
                    print_error(result.get("final", "未知错误"))
                elif result.get("needs_user_input"):
                    print_status("工作流已暂停 — 等待用户输入。")
                elif result.get("workflow_completed"):
                    print_success("工作流已完成。")

                final_text = result.get("final", "")
                if final_text and not final_text.startswith("[Error]"):
                    msg = self.state.append("assistant", final_text)
                    reasoning = "".join(self._reasoning_buf) if self._reasoning_buf else ""
                    if reasoning:
                        msg.reasoning_content = reasoning

            except KeyboardInterrupt:
                get_console().print()
                print_info("已中断。")
            except Exception as exc:
                print_error(f"运行时异常: {exc}")


def main():
    """CLI 入口（供 setup.py / pyproject.toml 使用）。"""
    from cli.main import cli
    cli()
