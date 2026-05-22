import json
import os
from dataclasses import asdict, dataclass
from typing import List


@dataclass
class SkillConfig:
    """单个技能配置"""
    name: str
    description: str
    enabled: bool = True
    category: str = "general"
    config: dict = None

    def __post_init__(self):
        if self.config is None:
            self.config = {}


@dataclass
class LLMConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    system_prompt: str = (
        "你是一名专业的逆向工程助手，可以使用 IDA Pro 工具。"
        "你的目标是高效分析二进制文件，产出清晰、基于事实的报告。\n\n"
        "## 工具使用规则\n"
        "- 使用工具收集事实：反编译、反汇编、读取内存、查看交叉引用。\n"
        "- 不要编造地址、函数名或分析结果。\n"
        "- 地址必须是十六进制数（如 0x401000），不能是变量名（如 dword_431754）。\n"
        "- 工具返回错误时，不要用相同参数重试，根据错误调整。\n"
        "- 避免用相同参数重复调用同一工具。\n\n"
        "## 壳检测（分析新二进制时首先执行）\n"
        "深入分析前，先检查是否加壳：\n"
        "1. list_strings_filter(\"UPX\") → UPX 段名（UPX0、UPX1、UPX2）表示 UPX 加壳\n"
        "2. get_entry_points() → 非常小的入口点（stub）提示可能是壳\n"
        "3. list_imports(0, 20) → 极少的导入函数（仅 LoadLibrary/GetProcAddress）是强信号\n"
        "4. list_functions(0, 10) → 极少的函数数量提示可能是壳\n"
        "如果确认是 UPX 壳，调用 upx_unpack 脱壳，然后告知用户用 IDA 重新打开。\n\n"
        "## 停止调用工具的条件\n"
        "- 已反编译关键函数并追踪主要逻辑。\n"
        "- 已识别出用户询问的算法、协议或数据结构。\n"
        "- 已有足够事实来完成回答。\n"
        "- 不要为了确认已知事实而继续调用工具。\n"
        "- 不要探索无关的函数。\n"
        "- 准备好回答时，输出纯文本，不再调用工具。\n\n"
        "## 证据规则\n"
        "- 每个结论必须引用具体的工具输出（地址、函数名、字符串等）。\n"
        "- 只描述工具实际返回的内容，如果未找到相关数据，如实说明。\n"
        "- 不确定时，说明原因（如'现有数据无法确定'）。\n\n"
        "## 描述规则\n"
        "- 基于证据如实描述程序行为。\n"
        "- 陈述程序做了什么，不要推测意图。\n"
        "- 用自然语言解释逻辑，不要输出大段伪代码。\n\n"
        "## 回答格式\n"
        "- 使用用户提问的语言回答。\n"
        "- 结构：概述 → 行为分析 → 结论。\n"
        "- 使用要点列表，每个发现 1-2 句话并附带证据。\n"
        "- 如果用户问了具体问题，先直接回答。\n"
        "- 不要在回复中使用 emoji 表情符号。"
    )
    plain_system_prompt: str = (
        "你是一个通用编程与逆向学习助手。当前未启用 IDA/MCP 工具，"
        "不能访问二进制、地址、反编译结果或任何外部工具数据。"
        "请明确说明这一限制，并在无需工具的范围内提供可执行帮助："
        "解释思路、给出排查步骤、提供命令/脚本模板、回答概念问题。"
        "禁止伪造工具调用、地址、函数名或分析结论。"
    )
    temperature: float = 0.2
    # 默认关闭：仅普通对话；开启后才会向 API 注册 MCP tools 并可能调用 IDA。
    use_ida_tools: bool = False
    # Agent 每轮 LLM 请求（含工具调用）的最大次数，避免复杂任务过早结束。
    agent_max_tool_rounds: int = 48
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 31337
    mcp_timeout_seconds: float = 20.0

    # Skills配置
    skills_enabled: bool = True
    skills_directory: str = ""
    available_skills: List[dict] = None
    active_skills: List[str] = None

    # LLM4Decompile 配置 (可选模块，需 GPU + vLLM 部署，默认关闭)
    llm4decompile_enabled: bool = False
    llm4decompile_base_url: str = "http://localhost:8080/v1"
    llm4decompile_model: str = "llm4decompile-9b-v2"
    llm4decompile_timeout: float = 60.0

    # ── Agent 工作流控制 ──
    # 启用 LLM 驱动的分析计划生成 (Phase 1: PLANNING)
    agent_enable_planning: bool = True
    # 启用假设-证据追踪 (Phase 3: VERIFYING)
    agent_enable_hypothesis_tracking: bool = True
    # 各阶段最大轮数
    agent_max_plan_rounds: int = 3
    agent_max_execute_rounds: int = 30
    agent_max_verify_rounds: int = 5
    # 上下文压缩阈值 (字符数，超过后触发智能压缩)
    agent_compaction_threshold_chars: int = 25000
    # 硬性上下文上限
    agent_max_context_chars: int = 80000
    # 连续无新证据轮数上限 (超过后强制推进阶段)
    agent_stale_round_limit: int = 3

    # ── 工具执行器配置 ──
    # ThreadPool 最大并行工作线程
    tool_executor_max_workers: int = 8
    # 并发反编译上限 (避免 IDA 过载)
    tool_executor_decompile_limit: int = 2

    # ── 工具结果管理 ──
    # 每轮所有工具结果的全局软限制 (字符数)
    tool_result_global_budget_chars: int = 120_000

    # ── 错误恢复 ──
    # 每个工具调用的最大重试次数
    error_recovery_max_retries: int = 2

    # ── 延迟工具加载 ──
    # 是否在后期轮次中延迟加载非核心工具 schema
    tool_deferred_loading: bool = True
    # 第几轮后开始延迟加载
    tool_deferred_after_rounds: int = 3

    def __post_init__(self):
        if self.available_skills is None:
            self.available_skills = []
        if self.active_skills is None:
            self.active_skills = []
        if not self.skills_directory:
            self.skills_directory = ""

    @classmethod
    def load(cls, path: str) -> "LLMConfig":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(
                base_url=str(data.get("base_url", cls.base_url)),
                api_key=str(data.get("api_key", "")),
                model=str(data.get("model", cls.model)),
                system_prompt=str(data.get("system_prompt", cls.system_prompt)),
                plain_system_prompt=str(data.get("plain_system_prompt", cls.plain_system_prompt)),
                temperature=float(data.get("temperature", cls.temperature)),
                use_ida_tools=bool(data.get("use_ida_tools", False)),
                agent_max_tool_rounds=int(data.get("agent_max_tool_rounds", cls.agent_max_tool_rounds)),
                mcp_host=str(data.get("mcp_host", cls.mcp_host)),
                mcp_port=int(data.get("mcp_port", cls.mcp_port)),
                mcp_timeout_seconds=float(data.get("mcp_timeout_seconds", cls.mcp_timeout_seconds)),
                skills_enabled=bool(data.get("skills_enabled", True)),
                skills_directory=str(data.get("skills_directory", "")),
                available_skills=list(data.get("available_skills", [])),
                active_skills=list(data.get("active_skills", [])),
                llm4decompile_enabled=bool(data.get("llm4decompile_enabled", False)),
                llm4decompile_base_url=str(data.get("llm4decompile_base_url", "http://localhost:8080/v1")),
                llm4decompile_model=str(data.get("llm4decompile_model", "llm4decompile-9b-v2")),
                llm4decompile_timeout=float(data.get("llm4decompile_timeout", 60.0)),
                agent_enable_planning=bool(data.get("agent_enable_planning", True)),
                agent_enable_hypothesis_tracking=bool(data.get("agent_enable_hypothesis_tracking", True)),
                agent_max_plan_rounds=int(data.get("agent_max_plan_rounds", 3)),
                agent_max_execute_rounds=int(data.get("agent_max_execute_rounds", 30)),
                agent_max_verify_rounds=int(data.get("agent_max_verify_rounds", 5)),
                agent_compaction_threshold_chars=int(data.get("agent_compaction_threshold_chars", 25000)),
                agent_max_context_chars=int(data.get("agent_max_context_chars", 80000)),
                agent_stale_round_limit=int(data.get("agent_stale_round_limit", 3)),
                tool_executor_max_workers=int(data.get("tool_executor_max_workers", 8)),
                tool_executor_decompile_limit=int(data.get("tool_executor_decompile_limit", 2)),
                tool_result_global_budget_chars=int(data.get("tool_result_global_budget_chars", 120_000)),
                error_recovery_max_retries=int(data.get("error_recovery_max_retries", 2)),
                tool_deferred_loading=bool(data.get("tool_deferred_loading", True)),
                tool_deferred_after_rounds=int(data.get("tool_deferred_after_rounds", 3)),
            )
        except Exception:
            return cls()

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
