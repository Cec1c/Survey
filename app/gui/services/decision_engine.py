from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import json
import re

from app.gui.state.chat_state import AgentPhase


class PhaseDecision(Enum):
    """决策结果枚举。

    按优先级排列：
    - CONTINUE: 继续当前阶段，执行工具调用
    - SKIP_TOOLS: 跳过工具调用，保留文本，推进阶段
    - FORCE_NEXT: 强制推进到下一阶段（收益递减/硬上限）
    - RETURN_TO_USER: 返回当前结果给用户，等待输入
    - REJECT_AND_RETRY: 打回重问（注入纠正提示）
    """
    CONTINUE = auto()
    SKIP_TOOLS = auto()
    FORCE_NEXT = auto()
    RETURN_TO_USER = auto()
    REJECT_AND_RETRY = auto()


@dataclass
class PhaseContext:
    """决策上下文：汇总当前轮次的模型输出、工具调用和资源预算。"""
    content: str = ""
    tool_calls: list = field(default_factory=list)
    phase: Any = None                     # AgentPhase
    round: int = 0
    total_tool_calls: int = 0
    fresh_evidence: int = 0
    stale_rounds: int = 0
    retry_count: int = 0
    phase_round_count: int = 0
    has_hypotheses_resolved: bool = False
    # 新颖度追踪：本轮调用的目标地址/函数名
    novel_addresses: set = field(default_factory=set)
    # 连续低新颖度轮数（由引擎维护）
    consecutive_low_novelty: int = 0


# ── 幻觉工具调用检测常量 ──────────────────────────────────────────────────

_HALLUCINATION_XML_TAGS = (
    "<invoke ",
    "<｜｜DSML｜｜tool_calls>",
    "<｜｜DSML｜｜invoke ",
    "<function_call",
)

_HALLUCINATION_JSON_FUNC = re.compile(
    r'```json\s*\n?\s*\{\s*"function"\s*:\s*"[^"]+"\s*,\s*"arguments"'
)

_HALLUCINATION_JSON_INLINE = re.compile(
    r'\{\s*"function"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{'
)

# ── 用户反问检测短语 ──────────────────────────────────────────────────────

_QUESTION_TO_USER_PHRASES = (
    # 中文反问
    "需要我继续", "要我继续", "需要继续", "请告知", "请告诉我",
    "你想让我", "你希望我", "你想分析", "你希望分析",
    "请指定", "请选择", "请说明", "请明确",
    "是否需要", "是否要", "要我帮你", "需要我帮",
    "请问", "想了解", "你想看", "你希望",
    "下一步", "应该如何", "接下来", "要分析",
    "你想深入", "你感兴趣", "重点关注", "你想知道",
    "要我详细", "要不要我", "想让我",
    # 英文
    "should i", "do you want", "would you like",
    "shall i", "which function", "what should",
    "tell me what", "let me know", "how would you like",
)

# ── 结论模式检测短语 ──────────────────────────────────────────────────────

_CONCLUSION_PATTERNS = (
    "根据以上", "综上所述", "总结", "该程序", "算法", "核心逻辑",
    "加密方式", "网络通信", "文件操作", "反调试", "入口点",
    "主要功能", "分析结论", "关键发现", "整体流程",
    "in summary", "in conclusion", "to summarize",
    "the program uses", "the algorithm is",
    "based on the analysis", "the key finding",
)

# ── 不完整结论检测短语 ────────────────────────────────────────────────────

_INCOMPLETE_PATTERNS = (
    "需要下一步操作", "需要下一步", "必须获取", "需要获取",
    "未解决的关键点", "未确认", "尚未确认", "待确认",
    "需要反编译", "需要查看", "需要读取", "需要调用",
    "needs more", "requires further", "must decompile",
)


class PhaseDecisionEngine:
    """统一决策引擎。

    将散布在 chat_service.py 中的 10+ 个分散检测条件收敛到一处，
    按固定优先级评估所有停止条件，返回统一的 PhaseDecision。
    """

    def __init__(
        self,
        stale_round_limit: int = 3,
        hard_round_limit: int = 15,          # 从 10 提升 — 仅作安全网
        hard_tool_limit: int = 50,           # 从 20 提升 — 仅作安全网
        novelty_threshold: float = 0.3,      # 新颖度低于此值视为绕圈
        novelty_consecutive_limit: int = 2,  # 连续低新颖度轮数触发干预
    ):
        self.stale_round_limit = stale_round_limit
        self.hard_round_limit = hard_round_limit
        self.hard_tool_limit = hard_tool_limit
        self.novelty_threshold = novelty_threshold
        self.novelty_consecutive_limit = novelty_consecutive_limit
        # 新颖度追踪: 记录所有已出现过的目标 (地址/函数名) → 最后出现轮次
        self._seen_targets: Dict[str, int] = {}
        # 连续低新颖度计数器
        self._consecutive_low_novelty: int = 0

    def reset_novelty(self) -> None:
        """新一轮 turn 开始时重置新颖度追踪。"""
        self._seen_targets.clear()
        self._consecutive_low_novelty = 0

    def record_targets(self, targets: set, round_num: int) -> None:
        """记录本轮探索的目标，更新已见列表。"""
        for t in targets:
            self._seen_targets[t] = round_num

    def score_novelty(self, targets: set, current_round: int) -> float:
        """计算本轮的新颖度得分。

        每个目标:
          从未出现过或本轮刚发现 → 1.0 (新发现)
          在前 2 轮内出现过 → 0.1 (刚看过 — 绕圈信号)
          2 轮前出现过 → 0.3 (可能值得重新审视)

        返回: 平均新颖度 (0.0–1.0), 无目标时返回 0.0
        """
        if not targets:
            return 1.0  # 没有工具调用本身不说明绕圈, 交给其他检测处理
        scores = []
        for t in targets:
            last_seen = self._seen_targets.get(t)
            if last_seen is None or last_seen == current_round:
                scores.append(1.0)    # 全新目标 / 本轮发现
            elif current_round - last_seen <= 1:
                scores.append(0.1)    # 上一轮刚看过 — 绕圈信号
            else:
                scores.append(0.4)    # 2+ 轮前看过 — 可能值得重新审视
        return sum(scores) / len(scores)

    # ── 主决策入口 ─────────────────────────────────────────────────────

    def decide(self, ctx: PhaseContext) -> PhaseDecision:
        """按优先级依次评估所有停止条件。

        检查顺序（命中即返回）：
          1. 反问用户 → RETURN_TO_USER
          2. 幻觉工具调用 → REJECT_AND_RETRY
          3. 不完整结论 → REJECT_AND_RETRY
          4. 结论已就绪 → SKIP_TOOLS
          5. 新颖度耗竭 → FORCE_NEXT (替代轮次提示)
          6. 硬性上限 → FORCE_NEXT (安全网, 15轮/50调用)
          7. 收益递减 → FORCE_NEXT
          8. 无工具调用 → FORCE_NEXT
          9. 默认 → CONTINUE
        """
        # 1. 反问用户
        if self._is_user_question(ctx.content):
            return PhaseDecision.RETURN_TO_USER

        # 2. 幻觉工具调用
        hallucinated, _ = self._has_hallucinated_tool_calls(ctx.content)
        if hallucinated:
            return PhaseDecision.REJECT_AND_RETRY

        # 3. 不完整结论
        if self._is_incomplete_conclusion(ctx.content):
            return PhaseDecision.REJECT_AND_RETRY

        # 4. 结论已就绪
        if ctx.content and self._looks_like_conclusion(ctx.content):
            return PhaseDecision.SKIP_TOOLS

        # 5. 新颖度耗竭: 连续 N 轮分析边界未扩张 → 推进
        novelty = self.score_novelty(ctx.novel_addresses, ctx.phase_round_count)
        if novelty < self.novelty_threshold:
            self._consecutive_low_novelty += 1
            ctx.consecutive_low_novelty = self._consecutive_low_novelty
            if self._consecutive_low_novelty >= self.novelty_consecutive_limit:
                return PhaseDecision.FORCE_NEXT
        else:
            self._consecutive_low_novelty = 0
            ctx.consecutive_low_novelty = 0

        # 6. 硬性上限 (安全网 — 正常分析不应触发)
        if (
            ctx.total_tool_calls >= self.hard_tool_limit
            or ctx.phase_round_count >= self.hard_round_limit
        ):
            return PhaseDecision.FORCE_NEXT

        # 7. 收益递减
        if self._is_diminishing(ctx):
            return PhaseDecision.FORCE_NEXT

        # 8. 无工具调用 → 模型主动停下
        if not ctx.tool_calls:
            return PhaseDecision.FORCE_NEXT

        # 9. 默认：继续
        return PhaseDecision.CONTINUE

    # ── 检测方法（从 chat_service.py 提取） ────────────────────────────

    @staticmethod
    def _is_user_question(text: str) -> bool:
        """检测模型是否在反问用户，需要等待用户输入。"""
        t = (text or "").strip()
        if not t:
            return False

        ends_with_qmark = t.endswith("?") or t.endswith("？")
        has_phrase = any(p in t.lower() for p in _QUESTION_TO_USER_PHRASES)

        if ends_with_qmark and has_phrase:
            return True
        if ends_with_qmark and len(t) < 300:
            return True
        if has_phrase and len(t) < 500:
            return True
        return False

    @staticmethod
    def _has_hallucinated_tool_calls(text: str) -> Tuple[bool, str]:
        """检测并剥离 model 在纯文本中幻觉输出的工具调用格式。

        DeepSeek 等模型在 with_tools=False 时可能在 content 中输出：
        - XML: <invoke name="xxx"> / <｜｜DSML｜｜tool_calls>
        - JSON: ```json {"function":"decompile","arguments":{...}} ```

        Returns:
            (detected, cleaned_text)  —— detected=True 表示发现并剥离了幻觉调用。
        """
        if not text:
            return False, text

        # XML 格式
        for tag in _HALLUCINATION_XML_TAGS:
            idx = text.find(tag)
            if idx != -1:
                trimmed = text[:idx].strip()
                cleaned = (trimmed + "\n\n[分析已结束]") if trimmed else "[分析已结束]"
                return True, cleaned

        # JSON 代码块格式
        m = _HALLUCINATION_JSON_FUNC.search(text)
        if m:
            trimmed = text[: m.start()].strip()
            cleaned = (trimmed + "\n\n[分析已结束]") if trimmed else "[分析已结束]"
            return True, cleaned

        # 行内 JSON
        m = _HALLUCINATION_JSON_INLINE.search(text)
        if m:
            before = text[: m.start()]
            # 确保不在 Markdown 代码块引用中
            if "```" not in before[before.rfind("\n"):] if "\n" in before else True:
                trimmed = before.strip()
                cleaned = (trimmed + "\n\n[分析已结束]") if trimmed else "[分析已结束]"
                return True, cleaned

        return False, text

    @staticmethod
    def _is_incomplete_conclusion(text: str) -> bool:
        """检测结论是否不完整——模型在结论中请求更多工具调用。"""
        t = (text or "").lower()
        if any(p in t for p in _INCOMPLETE_PATTERNS):
            return True
        # 检测幻觉 JSON 工具调用格式
        if '"function"' in t and '"arguments"' in t and "{" in t:
            return True
        return False

    @staticmethod
    def _looks_like_conclusion(content: str) -> bool:
        """检测模型是否在文本中给出了结论，却同时调用了工具。"""
        text = (content or "").strip()
        has_cjk = any("\u4e00" <= c <= "\u9fff" for c in text)
        min_len = 60 if has_cjk else 200
        if len(text) < min_len:
            return False
        return any(p in text.lower() for p in _CONCLUSION_PATTERNS)

    @staticmethod
    def _is_address_guessing(tool_calls: list) -> bool:
        """检测模型是否在对连续地址进行猜测性读取。

        条件：本轮 >= 2 个 read_* 调用，且地址范围在 0x1000 内。
        """
        reads: List[int] = []
        for tc in tool_calls:
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            if not (name.startswith("read_") or name.startswith("data_read_")):
                continue
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                continue
            for key in ("address", "memory_address"):
                val = args.get(key, "")
                if isinstance(val, str):
                    try:
                        reads.append(int(val.strip(), 0))
                    except ValueError:
                        pass
        if len(reads) < 2:
            return False
        addr_range = max(reads) - min(reads)
        return addr_range <= 0x1000

    def _is_diminishing(self, ctx: PhaseContext) -> bool:
        """收益递减检测：连续空轮次 / 所有假设已解决。

        注意：轮次相关的"无新证据"检测已由新颖度追踪 (step 5) 接管。
        此处的检测仅作为补充安全网。
        """
        if ctx.stale_rounds >= self.stale_round_limit:
            return True
        if ctx.has_hypotheses_resolved and ctx.phase_round_count >= 2:
            return True
        return False

    @staticmethod
    def _is_valid_hex(val: str) -> bool:
        """检查字符串是否看起来像有效的十六进制地址（而非变量名）。"""
        if not val or not val.strip():
            return False
        stripped = val.strip()
        try:
            int(stripped, 0)
        except ValueError:
            return False
        if "_" in stripped:
            return False
        return True

    # ── 辅助方法 ───────────────────────────────────────────────────────

    @staticmethod
    def is_last_message_user_question(messages: list) -> bool:
        """检查最后一条 assistant 消息是否为反问用户。"""
        for m in reversed(messages):
            role = m.get("role", "")
            if role == "assistant":
                content = m.get("content", "") or ""
                return PhaseDecisionEngine._is_user_question(content)
            if role in ("user", "system"):
                break
        return False

    @staticmethod
    def strip_hallucinated_tool_calls(text: str) -> str:
        """纯文本剥离：不返回检测标志，只返回清理后的文本。"""
        _, cleaned = PhaseDecisionEngine._has_hallucinated_tool_calls(text)
        return cleaned

    # ── 纠正 / 收尾提示构建 ───────────────────────────────────────────

    @staticmethod
    def build_rejection_hint(ctx: PhaseContext) -> str:
        """构建一个 system 消息，告诉模型停止幻觉/不完整行为并使用已有数据。"""
        parts: List[str] = []

        # 检测到幻觉工具调用
        hallucinated, cleaned = PhaseDecisionEngine._has_hallucinated_tool_calls(ctx.content)
        if hallucinated:
            parts.append(
                "[System] 检测到你输出了模拟/幻觉的工具调用格式 "
                "(XML <invoke> 或 JSON {\"function\":...})。"
                "当前模式不允许工具调用。请基于已有数据直接输出分析结论，"
                "不要虚构工具调用。"
            )

        # 不完整结论
        if PhaseDecisionEngine._is_incomplete_conclusion(ctx.content):
            parts.append(
                "[System] 你的回答请求了更多工具调用或标记了'未解决'。"
                "工具调用阶段已经结束。你必须基于目前已有的数据直接给出分析结论。"
                "不要提'需要下一步'、'必须获取'等，只总结你已确认的事实。"
            )

        if not parts:
            parts.append(
                "[System] 工具调用阶段已结束。请基于已有数据直接输出最终分析结论，"
                "不要调用任何工具。"
            )

        return "\n\n".join(parts)

    @staticmethod
    @staticmethod
    def build_novelty_hint(consecutive_low: int, threshold: float = 0.3) -> Optional[str]:
        """当分析新颖度持续走低时，给出针对性的引导而非盲目催促。

        仅在连续低新颖度达到阈值-1 时（即将触发干预前）发送一次提示。
        """
        if consecutive_low != 1:
            return None
        return (
            "[System] 最近几轮分析似乎集中在已探索过的区域。"
            "如果还有关键的调用链或数据结构未追踪，请聚焦到那些新目标上；"
            "否则可以直接基于已有证据给出分析结论。"
        )
