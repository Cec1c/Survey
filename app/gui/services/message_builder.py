"""Message construction for LLM API calls.

Extracted from AgentChatService for testability and separation of concerns.

Responsibilities:
- Build layered system prompts (core + phase-specific + skills + tool guidance)
- Construct message lists for API calls (plain mode and agent phase mode)
- Compact context when token budgets are exceeded
"""

import json
from typing import Any, Dict, List, Optional

from app.gui.state.chat_state import AgentPhase, ChatState


class MessageBuilder:
    """Builds message lists for LLM API calls."""

    def __init__(self, skills_service, llm4decompile_service):
        self.skills_service = skills_service
        self.llm4decompile_service = llm4decompile_service

    def build_phase_messages(
        self,
        state: ChatState,
        current_messages: List[Dict],
        engine,
        phase: AgentPhase,
        user_text: str,
        system_prompt: str,
        skills_enabled: bool,
        tool_registry=None,
    ) -> List[Dict[str, Any]]:
        """Build layered system prompt + history + current tool messages.

        Layers:
        1. Core system prompt (passed as parameter)
        2. Phase-specific prompt (from engine)
        3. Withheld error context (SYNTHESIZING only)
        4. Skills content (if enabled)
        5. Tool use guidance
        6. LLM4Decompile availability hint
        7. Conversation history (last 8 messages)
        8. Current round tool-call messages
        """
        system_parts = [system_prompt]

        # Phase-specific prompt
        if phase == AgentPhase.PLANNING:
            system_parts.append(engine.planning_system_prompt())
        elif phase == AgentPhase.EXECUTING:
            system_parts.append(engine.executing_system_prompt())
        elif phase == AgentPhase.VERIFYING:
            system_parts.append(engine.verifying_system_prompt())
        elif phase == AgentPhase.SYNTHESIZING:
            system_parts.append(engine.synthesizing_system_prompt())

        # Skills content
        if skills_enabled:
            skills_content = self.skills_service.get_relevant_skills(user_text)
            if skills_content:
                system_parts.append(skills_content)

        # Tool use guidance
        system_parts.extend([
            "\n## 工具调用指导",
            "- 避免重复调用相同工具和参数",
            "- 按照当前阶段的指示执行",
            "- 每步完成后总结关键发现",
        ])

        # LLM4Decompile availability hint
        if self.llm4decompile_service.enabled and self.llm4decompile_service.available:
            system_parts.append(
                "\n## LLM4Decompile\n"
                "当 IDA 伪代码难以理解时，使用 llm4decompile_refine 获取更清晰的 C 代码。"
            )

        system_text = "\n".join(system_parts)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_text}]

        # Recent conversation history (last 8 messages)
        recent = state.messages[-8:]
        for msg in recent:
            if msg.role in ("user", "assistant"):
                entry: Dict[str, Any] = {"role": msg.role, "content": msg.content}
                if msg.role == "assistant" and msg.reasoning_content:
                    entry["reasoning_content"] = msg.reasoning_content
                messages.append(entry)

        # Current round tool-call messages (skip stale system prompt)
        for m in current_messages:
            if m.get("role") == "system" and m is current_messages[0]:
                continue
            messages.append(m)

        # Ensure user message is last when no tool messages exist
        if not current_messages:
            messages.append({"role": "user", "content": user_text})

        return messages

    def build_plain_messages(
        self,
        state: ChatState,
        user_text: str,
        system_prompt: str,
    ) -> List[Dict[str, Any]]:
        """Build messages for plain mode (no tools).

        Uses a flat system prompt without phase-specific layers, skills,
        or tool definitions.
        """
        recent = state.messages[-12:]

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for msg in recent:
            if msg.role in ("user", "assistant"):
                entry: Dict[str, Any] = {"role": msg.role, "content": msg.content}
                if msg.role == "assistant" and msg.reasoning_content:
                    entry["reasoning_content"] = msg.reasoning_content
                messages.append(entry)

        # Append user text if it differs from the last message
        if not recent or recent[-1].role != "user" or recent[-1].content != user_text:
            messages.append({"role": "user", "content": user_text})
        return messages

    @staticmethod
    def compact_context(
        messages: List[Dict],
        engine,
        max_chars: int,
    ) -> List[Dict]:
        """Smart context compaction: keep last 4 rounds, compress old ones.

        When the total serialised size exceeds ``max_chars`` and there are
        more than 4 tool-call rounds, earlier rounds are replaced by a
        summary containing hypothesis state and an evidence trail.
        """
        round_indices = MessageBuilder.find_tool_rounds(messages)
        total_chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)

        # No compaction needed
        if total_chars <= max_chars or len(round_indices) <= 4:
            return messages

        # Keep the last 4 rounds (8 messages: 4 assistant + 4 tool)
        keep_count = min(4, len(round_indices))
        cutoff_idx = round_indices[-keep_count] if keep_count > 0 else len(messages)

        kept_msgs = messages[cutoff_idx:]

        # Build summary from the analysis engine
        evidence_trail = engine.hypothesis_tracker.get_evidence_trail(
            engine.evidence_summary, max_items=15
        )
        hypothesis_summary = engine.hypothesis_tracker.get_active_summary()

        summary_parts = ["[历史分析轮次已压缩以节省上下文空间]"]
        if hypothesis_summary:
            summary_parts.append(hypothesis_summary)
        if evidence_trail:
            summary_parts.append(evidence_trail)
        compacted_count = len(round_indices) - keep_count
        if compacted_count > 0:
            summary_parts.append(f"[{compacted_count} 轮工具调用已压缩]")

        result: List[Dict] = []
        # Preserve original system message if present
        if kept_msgs and kept_msgs[0].get("role") == "system":
            result.append(kept_msgs.pop(0))
        result.append({"role": "system", "content": "\n\n".join(summary_parts)})
        result.extend(kept_msgs)
        return result

    @staticmethod
    def make_assistant_msg(msg: Dict) -> Dict:
        """Create an assistant message dict from an API response dict.

        Preserves ``content``, ``tool_calls``, and ``reasoning_content``.
        """
        entry: Dict[str, Any] = {
            "role": "assistant",
            "content": msg.get("content") or "",
        }
        if msg.get("tool_calls"):
            entry["tool_calls"] = msg["tool_calls"]
        rc = msg.get("reasoning_content")
        if rc:
            entry["reasoning_content"] = rc
        return entry

    @staticmethod
    def find_tool_rounds(messages: List[Dict]) -> List[int]:
        """Return indices of assistant messages that contain tool_calls."""
        return [i for i, m in enumerate(messages)
                if m.get("role") == "assistant" and m.get("tool_calls")]
