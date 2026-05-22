"""Agent 工作流管理 — LLM 驱动的分析引擎

简化版架构：基于假设追踪和四阶段状态机

- _HypothesisTracker: 管理假设生命周期，从LLM输出中提取假设
- _AnalysisEngine: LLM 驱动的分析计划生成和执行引擎
- WorkflowService: 薄层 facade，保持向后兼容
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.gui.state.chat_state import (
    AgentPhase,
    Evidence,
    Hypothesis,
    HypothesisStatus,
    TurnBudget,
)


# ── 保留 WorkflowStage 以兼容旧 import ─────────────────────────────────

class WorkflowStage(Enum):
    """工作流阶段 (deprecated, 新代码使用 AgentPhase)"""
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


# ── 分析步骤数据结构 ──────────────────────────────────────────────────

@dataclass
class AnalysisStep:
    """分析计划中的单个步骤"""
    phase: str           # "plan", "execute", "verify"
    action: str
    description: str
    completed: bool = False
    result: Optional[str] = None


# ── 假设追踪器 ────────────────────────────────────────────────────────

class _HypothesisTracker:
    """管理分析假设的生命周期（简化版）

    格式约定：
    - [plan]:{计划内容}
    - [H1]:{假设内容} - ACTIVE/CONFIRMED/DENIED
    """

    # 正则模式：行首匹配 [plan]:{...} 和 [H\d+]:{...}
    _PLAN_PATTERN = re.compile(r'^\[plan\]:\{(.+?)\}\s*$', re.MULTILINE | re.DOTALL)
    _HYPOTHESIS_PATTERN = re.compile(
        r'^\[(H\d+)\]:\{(.+?)\}(?:\s*-\s*(CONFIRMED|DENIED|ACTIVE))?\s*$',
        re.MULTILINE | re.DOTALL
    )

    def __init__(self):
        self.hypotheses: Dict[str, Hypothesis] = {}
        self.plan: str = ""
        self._next_id: int = 1

    def extract_plan_and_hypotheses(self, content: str, current_turn: int = 1) -> bool:
        """从LLM响应中提取计划和假设。

        返回 True 如果成功提取到计划或假设。
        """
        # 提取计划
        plan_match = self._PLAN_PATTERN.search(content)
        if plan_match:
            self.plan = plan_match.group(1).strip()

        # 提取假设
        hypothesis_matches = self._HYPOTHESIS_PATTERN.findall(content)

        # 记录本轮提及的假设ID
        mentioned_hids = set()

        for hid, description, status_str in hypothesis_matches:
            mentioned_hids.add(hid)
            status = HypothesisStatus(status_str) if status_str else HypothesisStatus.ACTIVE

            if hid in self.hypotheses:
                # 更新已有假设
                self.hypotheses[hid].status = status
                self.hypotheses[hid].last_mentioned = current_turn
                if description.strip():
                    self.hypotheses[hid].description = description.strip()
            else:
                # 新增假设
                self.hypotheses[hid] = Hypothesis(
                    hid=hid,
                    description=description.strip(),
                    turn_number=current_turn,
                    last_mentioned=current_turn,
                    status=status,
                )
                # 更新_next_id
                try:
                    num = int(hid[1:])
                    self._next_id = max(self._next_id, num + 1)
                except ValueError:
                    pass

        # 处理未提及的假设：自动降级为STALE
        for hid, h in self.hypotheses.items():
            if hid not in mentioned_hids and h.status == HypothesisStatus.ACTIVE:
                turns_since_mention = current_turn - h.last_mentioned
                if turns_since_mention >= 2:
                    h.status = HypothesisStatus.STALE

        return bool(plan_match or hypothesis_matches)

    def get_active_hypotheses(self) -> List[Hypothesis]:
        """获取活跃假设（用于注入prompt）"""
        return [h for h in self.hypotheses.values()
                if h.status in (HypothesisStatus.ACTIVE, HypothesisStatus.CONFIRMED)]

    def get_all_hypotheses(self) -> List[Hypothesis]:
        """获取所有假设"""
        return list(self.hypotheses.values())

    def get_active_summary(self) -> str:
        """生成用于prompt的活跃假设摘要"""
        active = self.get_active_hypotheses()
        if not active:
            return ""

        parts = ["## 待验证的假设"]
        for h in active:
            parts.append(f"[{h.hid}]:{{{h.description}}} - {h.status.value}")
        return "\n".join(parts)

    def get_full_summary(self) -> str:
        """生成完整的假设摘要（包括已否决的）"""
        if not self.hypotheses:
            return ""

        parts = []

        # 活跃假设
        active = self.get_active_hypotheses()
        if active:
            parts.append("## 待验证的假设")
            for h in active:
                parts.append(f"[{h.hid}]:{{{h.description}}} - {h.status.value}")

        # 已否决的假设
        denied = [h for h in self.hypotheses.values() if h.status == HypothesisStatus.DENIED]
        if denied:
            parts.append("\n## 已否决的假设（仅供参考）")
            for h in denied:
                parts.append(f"[{h.hid}]:{{{h.description}}} - DENIED")

        # 过时的假设
        stale = [h for h in self.hypotheses.values() if h.status == HypothesisStatus.STALE]
        if stale:
            parts.append("\n## 过时的假设（未继续追踪）")
            for h in stale:
                parts.append(f"[{h.hid}]:{{{h.description}}} - STALE")

        return "\n".join(parts)

    def get_evidence_trail(self, evidence_summary: List[str], max_items: int = 10) -> str:
        """生成证据追踪摘要"""
        if not evidence_summary:
            return ""

        parts = ["## 已收集的证据"]
        for e in evidence_summary[-max_items:]:
            parts.append(f"- {e}")
        return "\n".join(parts)

    def reset(self) -> None:
        """重置追踪器"""
        self.hypotheses.clear()
        self.plan = ""
        self._next_id = 1


# ── 分析引擎 ──────────────────────────────────────────────────────────

class _AnalysisEngine:
    """LLM 驱动的分析引擎（简化版）"""

    def __init__(self):
        self.steps: List[AnalysisStep] = []
        self.current_phase: AgentPhase = AgentPhase.PLANNING
        self.plan_generated: bool = False
        self.hypothesis_tracker: _HypothesisTracker = _HypothesisTracker()
        self.evidence_summary: List[str] = []  # 统一的证据列表
        self.turn_history: List[str] = []      # 每turn的结论摘要
        self._tool_call_log: List[Dict[str, Any]] = []
        self._execution_count: int = 0
        self._verification_count: int = 0
        self._current_turn: int = 1

    # ── 各阶段 System Prompt 片段 ───────────────────────────────────

    def planning_system_prompt(self) -> str:
        parts = [
            "\n## 阶段：计划\n",
            "当前任务：制定分析计划，提出或更新假设。\n",
            "用以下格式输出（每项独占一行，行首开始）：\n",
            "[plan]:{你的分析计划}\n",
            "[H1]:{假设内容}\n",
            "[H2]:{假设内容}（可选）\n",
            "规则：\n",
            "1. 新假设不需要状态标注，默认为 ACTIVE\n",
            "2. 更新已有假设时，在末尾添加 - CONFIRMED 或 - DENIED\n",
            "3. 没有新假设或更新时，只输出 [plan]:{...}\n",
            "4. 正文中不要使用 [H1]、[plan] 等标记\n",
        ]

        # 注入之前的假设
        hs = self.hypothesis_tracker.get_active_summary()
        if hs:
            parts.append("\n" + hs + "\n")

        # 注入上一turn的结论
        if self.turn_history:
            parts.append("\n## 之前的分析结论\n")
            parts.append(self.turn_history[-1] + "\n")

        return "".join(parts)

    def executing_system_prompt(self) -> str:
        parts = [
            "\n## 阶段：执行",
            "按照分析计划收集证据。",
            "## 每次工具调用前必须思考：",
            "  1. 这个调用是必要的，还是出于好奇心？",
            "  2. 我是否已经拥有足够的信息来回答问题？",
            "  3. 这个地址/函数是否与用户的请求直接相关？",
            "## 停止条件（满足任一即停止调用工具）：",
            "  - 已反编译所有关键函数并理解了核心逻辑",
            "  - 已确认用户询问的算法/协议/结构",
            "  - 连续两次工具调用没有产生新发现",
            "  - 你发现自己只是在'确认'已知事实",
            "达到停止条件后：输出一句简短的过渡语（如'已收集足够信息，准备验证结论'），不要再调用工具。",
        ]

        # 注入假设
        hs = self.hypothesis_tracker.get_active_summary()
        if hs:
            parts.append(hs)

        # 注入证据
        et = self.hypothesis_tracker.get_evidence_trail(self.evidence_summary)
        if et:
            parts.append(et)

        # 注入计划状态
        plan_status = self._plan_status()
        if plan_status:
            parts.append(plan_status)

        return "\n".join(parts)

    def verifying_system_prompt(self) -> str:
        parts = [
            "\n## 阶段：验证",
            "快速交叉验证关键结论。",
            "对每个假设判断：",
            "  - 是否有足够证据支持或否定？",
            "  - 是否存在未解决的矛盾？",
            "最多做 1-2 次补充工具调用进行最终确认。",
            "然后输出文本验证总结，不要再调用工具。",
        ]

        # 注入假设
        hs = self.hypothesis_tracker.get_full_summary()
        if hs:
            parts.append(hs)

        # 注入证据
        et = self.hypothesis_tracker.get_evidence_trail(self.evidence_summary, max_items=20)
        if et:
            parts.append(et)

        return "\n".join(parts)

    def synthesizing_system_prompt(self) -> str:
        parts = [
            "\n## 阶段：总结",
            "综合所有证据给出最终分析结论。",
            "你不能调用任何工具，直接输出纯文本答案。",
            "",
            "## 输出结构",
            "### 概述",
            "程序类型和基本功能（1-2 句话）。",
            "",
            "### 行为分析",
            "用自然语言描述程序行为，每个行为附带证据。",
            "格式：",
            "- 行为描述（证据：函数名@地址 或 字符串 或 API 调用）",
            "",
            "### 结论",
            "直接回答用户的问题（如果有）。",
            "基于上述分析，如实总结程序的功能。",
            "",
            "## 证据要求",
            "- 每个结论必须引用具体的工具输出",
            "- 只描述工具实际返回的内容",
            "- 如果某方面没有相关信息，说'未发现相关数据'",
            "",
            "这是最终回答，不要调用任何工具。",
        ]

        # 注入假设
        hs = self.hypothesis_tracker.get_full_summary()
        if hs:
            parts.append(hs)

        return "\n".join(parts)

    # ── Plan 解析 ──────────────────────────────────────────────────

    def extract_plan_from_response(self, content: str) -> bool:
        """从模型响应中提取分析计划和假设。返回 True 如果成功提取。"""
        extracted = self.hypothesis_tracker.extract_plan_and_hypotheses(
            content, self._current_turn
        )

        if extracted:
            self.plan_generated = True
            # 将计划转换为步骤（简化版：单一步骤）
            if self.hypothesis_tracker.plan:
                self.steps.clear()
                self.steps.append(AnalysisStep(
                    phase="execute",
                    action="执行分析",
                    description=self.hypothesis_tracker.plan,
                ))

        return extracted

    # ── 证据提取 ────────────────────────────────────────────────────

    def record_tool_call(
        self, tool_name: str, args: Dict[str, Any],
        tool_result: Dict[str, Any], round_number: int,
    ) -> Optional[Evidence]:
        """记录工具调用，尝试提取关键证据"""
        self._tool_call_log.append({
            "tool_name": tool_name,
            "args": args,
            "result_summary": self._summarize_result(tool_name, tool_result),
            "round": round_number,
        })

        # 已缓存的结果不提取新证据
        if tool_result.get("from_cache"):
            return None

        fact = self._summarize_result(tool_name, tool_result)
        if not fact:
            return None

        # 添加到统一证据列表
        self.evidence_summary.append(fact)

        return Evidence(
            tool_name=tool_name,
            args=args,
            fact_summary=fact,
            round_number=round_number,
        )

    def _summarize_result(self, tool_name: str, result: Dict[str, Any]) -> str:
        """从工具结果中提取一句话关键发现。
        失败调用 / 空结果 / 缓存结果 返回空字符串，不产生证据。"""
        if not isinstance(result, dict):
            return ""

        if not result.get("ok", False):
            return ""  # 失败调用不产生证据

        if result.get("from_cache"):
            return ""  # 缓存结果不产生新证据

        payload = result.get("result")

        # 空结果检测: 无 payload 不产生证据
        if payload is None:
            return ""

        if isinstance(payload, dict):
            # 空字典不产生证据
            if not payload:
                return ""
            # 优先提取常见关键字段 (值必须非空)
            for key in ("name", "address", "pseudocode", "summary",
                         "lines", "count", "total", "size"):
                val = payload.get(key)
                if val is not None and val != "" and val != [] and val != 0:
                    s = str(val)
                    if len(s) > 150:
                        s = s[:147] + "..."
                    return f"{tool_name}: {key}={s}"
            # 回退: 跳过纯零值/空值, 提取第一个有意义的 kv
            for k, v in payload.items():
                if v is not None and v != "" and v != [] and v != {} and v != 0:
                    return f"{tool_name}: {k}={str(v)[:80]}"
            return ""  # 所有值都是空的 — 无证据

        if isinstance(payload, list):
            # 空列表不产生证据
            if len(payload) == 0:
                return ""
            return f"{tool_name}: {len(payload)} items"

        if isinstance(payload, str):
            if not payload.strip() or payload.strip() in ("", "null", "None"):
                return ""
            return f"{tool_name}: {payload[:120]}"

        # bool / int / float 零值不产生证据
        if isinstance(payload, bool):
            return f"{tool_name}: {payload}" if payload else ""
        if isinstance(payload, (int, float)):
            return f"{tool_name}: {payload}" if payload != 0 else ""

        return f"{tool_name}: {str(payload)[:120]}" if payload else ""

    # ── 辅助方法 ────────────────────────────────────────────────────

    def _plan_status(self) -> str:
        if not self.steps:
            return ""
        parts = ["## 分析计划进度"]
        for i, step in enumerate(self.steps):
            mark = "✓" if step.completed else "○"
            parts.append(f"  {mark} {i+1}. {step.action}: {step.description}")
        return "\n".join(parts)

    def mark_step_complete(self, action: str, result: str = "") -> None:
        for step in self.steps:
            if step.action == action and not step.completed:
                step.completed = True
                step.result = result
                break

    def add_turn_conclusion(self, conclusion: str) -> None:
        """添加turn结论到历史"""
        self.turn_history.append(f"Turn {self._current_turn}: {conclusion}")
        self._current_turn += 1

    def get_execution_context(self) -> str:
        """获取当前执行上下文（注入 system prompt）"""
        phase_labels = {
            AgentPhase.PLANNING: "planning",
            AgentPhase.EXECUTING: "executing",
            AgentPhase.VERIFYING: "verifying",
            AgentPhase.SYNTHESIZING: "synthesizing",
            AgentPhase.COMPLETED: "completed",
        }
        parts = [f"## 分析状态: {phase_labels.get(self.current_phase, 'unknown')}"]
        if self.steps:
            parts.append(self._plan_status())
        hs = self.hypothesis_tracker.get_active_summary()
        if hs:
            parts.append(hs)
        return "\n".join(parts)

    def get_summary(self) -> str:
        """获取最终分析总结"""
        parts = ["## 分析总结"]
        if self.steps:
            for i, step in enumerate(self.steps):
                status = "✓" if step.completed else "○"
                parts.append(f"{status} Step {i+1}: {step.action}")

        # 假设状态
        for h in self.hypothesis_tracker.hypotheses.values():
            parts.append(f"  假设 {h.hid}: {h.status.value}")

        return "\n".join(parts)

    def get_recent_tool_calls(self, limit: int = 5) -> List[Dict[str, Any]]:
        return list(reversed(self._tool_call_log[-limit:]))

    def has_tool_been_called(self, tool_name: str, args: Dict[str, Any] = None) -> bool:
        for entry in self._tool_call_log:
            if entry["tool_name"] == tool_name:
                if args is None:
                    return True
                if self._args_match(entry["args"], args):
                    return True
        return False

    @staticmethod
    def _args_match(a1: Dict, a2: Dict) -> bool:
        if set(a1.keys()) != set(a2.keys()):
            return False
        return all(a1[k] == a2[k] for k in a1)

    def reset(self) -> None:
        self.steps.clear()
        self.current_phase = AgentPhase.PLANNING
        self.plan_generated = False
        self.hypothesis_tracker.reset()
        self.evidence_summary.clear()
        self.turn_history.clear()
        self._tool_call_log.clear()
        self._execution_count = 0
        self._verification_count = 0
        self._current_turn = 1


# ── WorkflowService (facade) ───────────────────────────────────────────

class WorkflowService:
    """工作流管理服务 — 薄层 facade 委托给 _AnalysisEngine

    保持与旧代码的向后兼容，同时启用新的分析引擎。"""

    def __init__(self):
        self._engine = _AnalysisEngine()

    # 向后兼容属性
    @property
    def current_workflow(self):
        """兼容旧代码的 current_workflow 属性"""
        return self._engine.steps

    @property
    def current_stage(self):
        """兼容旧代码的 current_stage 属性"""
        phase_to_stage = {
            AgentPhase.PLANNING: WorkflowStage.PLANNING,
            AgentPhase.EXECUTING: WorkflowStage.EXECUTING,
            AgentPhase.VERIFYING: WorkflowStage.VERIFYING,
            AgentPhase.SYNTHESIZING: WorkflowStage.COMPLETED,
            AgentPhase.COMPLETED: WorkflowStage.COMPLETED,
        }
        return phase_to_stage.get(self._engine.current_phase, WorkflowStage.PLANNING)

    @property
    def execution_count(self):
        return self._engine._execution_count

    @execution_count.setter
    def execution_count(self, val):
        self._engine._execution_count = val

    @property
    def verification_count(self):
        return self._engine._verification_count

    @verification_count.setter
    def verification_count(self, val):
        self._engine._verification_count = val

    @property
    def max_execution_rounds(self):
        return 30

    @max_execution_rounds.setter
    def max_execution_rounds(self, val):
        pass  # 由 config.agent_max_execute_rounds 控制

    @property
    def max_verification_rounds(self):
        return 5

    @max_verification_rounds.setter
    def max_verification_rounds(self, val):
        pass  # 由 config.agent_max_verify_rounds 控制

    @property
    def context_memory(self):
        return self._engine._tool_call_log

    # 公开 API

    def create_workflow(self, user_request: str):
        """创建分析工作流（旧接口，现在由 PLANNING 阶段驱动）"""
        self._engine.reset()

    def should_continue_execution(self) -> bool:
        """判断是否应该继续执行"""
        return self._engine.current_phase not in (
            AgentPhase.COMPLETED, AgentPhase.SYNTHESIZING
        )

    def get_execution_context(self) -> str:
        return self._engine.get_execution_context()

    def record_tool_call(
        self, tool_name: str, args: Dict[str, Any], result: Any,
    ) -> None:
        call_key = f"{tool_name}_{hash(str(args))}"
        engine = self._engine

        # 防止完全重复记录
        for entry in engine._tool_call_log:
            if entry.get("call_key") == call_key:
                return

        entry = {
            "tool_name": tool_name,
            "args": args,
            "result": str(result)[:500],
            "timestamp": engine._execution_count,
            "call_key": call_key,
        }
        engine._tool_call_log.append(entry)

        if engine.current_phase == AgentPhase.EXECUTING:
            engine._execution_count += 1
        elif engine.current_phase == AgentPhase.VERIFYING:
            engine._verification_count += 1

    def get_recent_tool_calls(self, limit: int = 5) -> List[Dict[str, Any]]:
        return self._engine.get_recent_tool_calls(limit)

    def has_tool_been_called(self, tool_name: str, args: Dict[str, Any] = None) -> bool:
        return self._engine.has_tool_been_called(tool_name, args)

    def get_workflow_summary(self) -> str:
        return self._engine.get_summary()

    def reset_workflow(self) -> None:
        self._engine.reset()

    def get_workflow_progress(self) -> Dict[str, Any]:
        total = len(self._engine.steps)
        completed = sum(1 for s in self._engine.steps if s.completed)
        return {
            "current_stage": self._engine.current_phase.name.lower(),
            "total_steps": total,
            "completed_steps": completed,
            "progress_percent": int((completed / total) * 100) if total > 0 else 0,
            "current_step": self._engine.steps[0].action if self._engine.steps else None,
        }

    # 旧方法别名
    def complete_step(self, step_index: int, result: str, tool_calls=None) -> None:
        if 0 <= step_index < len(self._engine.steps):
            step = self._engine.steps[step_index]
            step.completed = True
            step.result = result
