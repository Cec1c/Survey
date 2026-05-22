"""Survey CLI 入口 — Click 命令组

用法:
    survey run                  # 交互式对话模式
    survey ask "分析这个函数"    # 单次提问
    survey config               # 查看配置
"""

import os
import sys
from typing import Optional

import click

# 强制 UTF-8 编码，防止 Windows GBK 下 emoji 等字符导致崩溃
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# 确保项目根目录在 sys.path 中最前面
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.gui.state.llm_config import LLMConfig
from cli.runner import CLIRunner
from cli.ui import (
    get_console,
    print_banner,
    print_banner_simple,
    print_error,
    print_info,
    print_separator,
    print_status,
    print_user_message,
    print_assistant_header,
    print_stream_chunk,
)


@click.group(invoke_without_command=True)
@click.version_option(version="0.2.0", prog_name="Survey CLI")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Survey CLI — 命令行逆向工程助手。

    基于现有 Agent 接口构建的 CLI 工具，支持与 IDA Pro
    MCP Bridge 交互、Skills 注入和流式对话。

    默认启动交互模式。
    """
    if ctx.invoked_subcommand is None:
        # 没有子命令，默认启动交互模式
        ctx.invoke(run)


@cli.command("run")
@click.option(
    "--config", "-c",
    default="app/config/llm_config.json",
    show_default=True,
    help="LLM 配置文件路径（相对于项目根目录或绝对路径）",
)
def run(config: str) -> None:
    """启动交互式对话模式。

    双线输入框，流式输出，彩色工具调用面板。
    输入 /help 查看内置命令列表。
    """
    runner = CLIRunner(config_path=config)
    runner.run_interactive()


@cli.command("ask")
@click.option(
    "--config", "-c",
    default="app/config/llm_config.json",
    show_default=True,
    help="LLM 配置文件路径",
)
@click.argument("question")
def ask(config: str, question: str) -> None:
    """单次提问模式。

    提交一个问题，获取回答后退出。
    适合脚本调用和快速查询。
    """
    runner = CLIRunner(config_path=config)
    runner.ask_once(question)


@cli.command("config")
@click.option(
    "--config", "-c",
    default="app/config/llm_config.json",
    show_default=True,
    help="LLM 配置文件路径",
)
def show_config(config: str) -> None:
    """查看当前配置。

    显示模型、API 地址、MCP 连接状态、Skills 信息等。
    """
    runner = CLIRunner(config_path=config)
    c = get_console()

    print_banner_simple()
    c.print("[bold #C4B5FD]当前配置[/bold #C4B5FD]")
    c.print()
    c.print(f"  [#94A3B8]配置文件:[/#94A3B8] {runner.config_path}")
    c.print(f"  [#94A3B8]API 地址:[/#94A3B8] {runner.config.base_url}")
    c.print(f"  [#94A3B8]模型:[/#94A3B8]     {runner.config.model}")
    c.print(f"  [#94A3B8]温度:[/#94A3B8]     {runner.config.temperature}")

    api_key = runner.config.api_key
    if api_key:
        masked = api_key[:6] + "****" + api_key[-4:] if len(api_key) > 10 else "****"
        c.print(f"  [#94A3B8]API Key:[/#94A3B8]  {masked}")
    else:
        c.print(f"  [#94A3B8]API Key:[/#94A3B8]  [#F87171]未设置[/#F87171]")

    c.print()
    # 读取原始配置文件中的值（而非运行时可能被降级的值）
    raw_config = LLMConfig.load(runner._resolved_config_path or os.path.join(_PROJECT_ROOT, config))
    ida_configured = raw_config.use_ida_tools
    ida_effective = runner._ida_available

    c.print(f"  [#94A3B8]IDA 工具 (配置):[/#94A3B8] {'[#4ADE80]启用[/#4ADE80]' if ida_configured else '[#64748B]禁用[/#64748B]'}")
    if ida_configured and not ida_effective:
        c.print(f"  [#94A3B8]IDA 工具 (实际):[/#94A3B8] [#FBBF24]已降级为关闭（Bridge 不可用）[/#FBBF24]")
    c.print(f"  [#94A3B8]MCP 地址:[/#94A3B8] {runner.config.mcp_host}:{runner.config.mcp_port}")
    c.print(f"  [#94A3B8]最大轮次:[/#94A3B8] {runner.config.agent_max_tool_rounds}")

    c.print()
    c.print(f"  [#94A3B8]Skills:[/#94A3B8]   {'[#4ADE80]启用[/#4ADE80]' if runner.config.skills_enabled else '[#64748B]禁用[/#64748B]'}")
    c.print(f"  [#94A3B8]Skills 目录:[/#94A3B8] {runner.config.skills_directory or '(未设置)'}")

    # 检查连接状态
    if ida_configured:
        c.print()
        if ida_effective:
            c.print("  [#4ADE80]● IDA Bridge 已连接[/#4ADE80]")
        else:
            c.print("  [#FBBF24]○ IDA Bridge 未连接[/#FBBF24]")

    c.print()
    print_separator()


def main():
    """CLI 入口点。"""
    cli()


if __name__ == "__main__":
    main()
