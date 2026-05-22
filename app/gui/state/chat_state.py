from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import List, Dict, Optional


# ── Agent 工作流状态类型 ──────────────────────────────────────────────

class HypothesisStatus(Enum):
    """假设验证状态（简化版）"""
    ACTIVE = "ACTIVE"        # 正在调查
    CONFIRMED = "CONFIRMED"  # 已确认
    DENIED = "DENIED"        # 已否决
    STALE = "STALE"          # 过时（多turn未提及）


@dataclass
class Evidence:
    """单条分析证据（简化版）"""
    tool_name: str
    args: Dict[str, object]   # 工具参数 (JSON-serializable)
    fact_summary: str          # 关键发现 (1-2行)
    round_number: int


@dataclass
class Hypothesis:
    """分析假设（简化版）"""
    hid: str                   # "H1", "H2", ...
    description: str
    turn_number: int           # 提出的turn编号
    last_mentioned: int        # 最后提及的turn编号
    status: HypothesisStatus = HypothesisStatus.ACTIVE

    def to_context_string(self) -> str:
        """用于 system prompt 注入的单行摘要"""
        return f"[{self.hid}]:{{{self.description}}} - {self.status.value}"


class AgentPhase(Enum):
    """Agent 工作阶段"""
    PLANNING = auto()       # 制定分析计划
    EXECUTING = auto()      # 执行分析
    VERIFYING = auto()      # 验证假设
    SYNTHESIZING = auto()   # 综合总结
    COMPLETED = auto()      # 完成


@dataclass
class TurnBudget:
    """单轮 agent 调用中的资源追踪"""
    total_tool_calls: int = 0
    fresh_evidence_count: int = 0
    consecutive_stale_rounds: int = 0
    phase_round_count: int = 0


# ── 对话状态 (原有类型，保持不变) ──────────────────────────────────────

@dataclass
class ChatMessage:
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))
    reasoning_content: str = ""


@dataclass
class ChatState:
    session_id: str = "default"
    messages: List[ChatMessage] = field(default_factory=list)

    def append(self, role: str, content: str) -> ChatMessage:
        message = ChatMessage(role=role, content=content)
        self.messages.append(message)
        return message

    def clear(self) -> None:
        self.messages.clear()
