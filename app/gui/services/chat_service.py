"""Agent chat service — coordinator layer.

Delegates to specialized modules:
  - ApiClient: LLM API communication and SSE streaming
  - MessageBuilder: Layered message construction
  - ContextManager: Tool result filtering and size management
  - PhaseDecisionEngine: Agent-phase stop-condition decisions
  - ToolPipeline: Hooked tool execution with caching and error recovery
  - errors: Typed error hierarchy and classification

Backward-compatibility wrappers are provided for methods that tests
still reference directly.
"""

import json
import threading
from typing import Any, Callable, Dict, List, Optional

from app.gui.services.mcp_service import MCPService
from app.gui.services.skills_service import SkillsService
from app.gui.services.workflow_service import WorkflowService
from app.gui.services.llm4decompile_service import LLM4DecompileService
from app.gui.services.tool_manifest import ToolManifest
from m2.tools._decorators import ToolMeta
from app.gui.services.tool_hooks import ToolHookChain, RenameConflictChecker, AddressValidator
from app.gui.state.chat_state import AgentPhase, ChatState, HypothesisStatus, TurnBudget
from app.gui.state.llm_config import LLMConfig

# Extracted modules
from app.gui.services.errors import classify_error
from app.gui.services.context_manager import ContextManager
from app.gui.services.decision_engine import PhaseDecisionEngine, PhaseDecision, PhaseContext
from app.gui.services.api_client import ApiClient
from app.gui.services.message_builder import MessageBuilder
from app.gui.services.tool_pipeline import ToolPipeline

# ── Callback type aliases ─────────────────────────────────────────────────

StreamChunkCallback = Optional[Callable[[str, str], None]]
RoundStartCallback = Optional[Callable[[], None]]
ToolTraceCallback = Optional[Callable[[str, Dict[str, Any]], Any]]


class AgentChatService:
    """OpenAI-compatible chat service with MCP tool-calling loop.

    Acts as a coordinator that delegates to specialised services:

    * **ApiClient** -- HTTP + SSE streaming to the LLM API provider.
    * **MessageBuilder** -- Layered system-prompt construction.
    * **ContextManager** -- Tool-result filtering and truncation.
    * **PhaseDecisionEngine** -- Unified stop-condition decisions per round.
    * **ToolPipeline** -- Hooked tool execution (hook -> cache -> MCP -> post-hook).
    """

    def __init__(self, config: LLMConfig, mcp_service: MCPService):
        self.config = config
        self.mcp_service = mcp_service

        # Skills service
        self.skills_service = SkillsService(skills_directory=config.skills_directory or "skills")
        self.skills_service.load_skills()

        # Workflow service (holds the analysis engine)
        self.workflow_service = WorkflowService()

        # LLM4Decompile service
        self.llm4decompile_service = LLM4DecompileService(
            base_url=config.llm4decompile_base_url or "http://localhost:8080/v1",
            model=config.llm4decompile_model or "llm4decompile-9b-v2",
            timeout=config.llm4decompile_timeout,
            enabled=config.llm4decompile_enabled,
        )

        # Tool manifest (reads from @ida_tool registry)
        self._tool_manifest = ToolManifest()
        self._refresh_conditional_tools()

        # Hook chain (PreToolUse / PostToolUse)
        self._hook_chain = ToolHookChain()
        self._hook_chain.add_pre(RenameConflictChecker())
        self._hook_chain.add_pre(AddressValidator())

        # Cross-turn tool-result cache
        self._persistent_tool_cache: Dict[str, Any] = {}

        # ── Extracted components ──────────────────────────────────────
        self._context = ContextManager()
        self._decider = PhaseDecisionEngine(
            stale_round_limit=config.agent_stale_round_limit,
            hard_round_limit=20,
            hard_tool_limit=50,
        )
        self._api = ApiClient(config)
        self._msgs = MessageBuilder(
            self.skills_service,
            self.llm4decompile_service,
        )
        self._pipeline = ToolPipeline(
            manifest=self._tool_manifest,
            mcp_service=self.mcp_service,
            hook_chain=self._hook_chain,
            persistent_cache=self._persistent_tool_cache,
            llm4decompile_service=self.llm4decompile_service,
            max_workers=config.tool_executor_max_workers,
            decompile_limit=config.tool_executor_decompile_limit,
            max_retries=config.error_recovery_max_retries,
        )

        # Phase-level flags exposed for tests and cross-phase checks
        self._conclusion_reached: bool = False
        self._synth_retried: bool = False

    # ── Configuration ─────────────────────────────────────────────────

    def update_config(self, config: LLMConfig) -> None:
        self.config = config
        if config.skills_directory != self.skills_service.skills_directory:
            self.skills_service = SkillsService(skills_directory=config.skills_directory or "skills")
            self.skills_service.load_skills()
        self.llm4decompile_service = LLM4DecompileService(
            base_url=config.llm4decompile_base_url or "http://localhost:8080/v1",
            model=config.llm4decompile_model or "llm4decompile-9b-v2",
            timeout=config.llm4decompile_timeout,
            enabled=config.llm4decompile_enabled,
        )
        self._refresh_conditional_tools()

    def _refresh_conditional_tools(self) -> None:
        if self.config.llm4decompile_enabled:
            if "llm4decompile_refine" not in self._tool_manifest.get_names():
                self._tool_manifest.register_conditional(ToolMeta(
                    name="llm4decompile_refine",
                    fn=lambda **kw: None,
                    description="Use LLM4Decompile model to refine IDA Hex-Rays pseudocode into cleaner, more readable C code.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "address": {"type": "string", "description": "Function address to decompile and refine"},
                            "mode": {"type": "string", "description": "Refinement mode: 'refine' (default) or 'two_phase'"},
                        },
                        "required": ["address"],
                    },
                    category="analysis",
                ))
        else:
            self._tool_manifest.unregister_conditional("llm4decompile_refine")

        import os as _os
        # 从skill配置读取upx路径 (相对于项目根目录)
        _project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        _skill_dir = _os.path.join(_project_root, self.config.skills_directory or "skills", "upx_unpack")
        _path_file = _os.path.join(_skill_dir, "upx_path.txt")
        if _os.path.exists(_path_file):
            with open(_path_file, 'r', encoding='utf-8') as _f:
                _upx = _f.read().strip()
            # 如果是相对路径，转换为绝对路径
            if not _os.path.isabs(_upx):
                _upx = _os.path.join(_project_root, _upx)
        else:
            _upx = _os.path.join(_skill_dir, "upx-5.0.2-win64", "upx.exe")
        if _os.path.exists(_upx):
            if "upx_unpack" not in self._tool_manifest.get_names():
                self._tool_manifest.register_conditional(ToolMeta(
                    name="upx_unpack",
                    fn=lambda **kw: None,
                    description="Unpack a UPX-compressed executable.",
                    parameters={
                        "type": "object",
                        "properties": {"file_path": {"type": "string", "description": "Path to the packed executable."}},
                        "required": [],
                    },
                    category="default",
                    concurrency_safe=False,
                ))
            if hasattr(self, '_pipeline') and self._pipeline is not None:
                self._pipeline.set_upx_path(_upx)
        else:
            self._tool_manifest.unregister_conditional("upx_unpack")

    # ── Public entry-point ────────────────────────────────────────────

    def run_turn(
        self,
        user_text: str,
        state: ChatState,
        on_stream_chunk: StreamChunkCallback = None,
        on_stream_round_start: RoundStartCallback = None,
        on_tool_trace: ToolTraceCallback = None,
        stop_event: threading.Event = None,
    ) -> Dict[str, Any]:
        if not self.config.api_key.strip():
            return {"ok": False, "final": "[Error] API Key 未配置，请在 Settings 中填写。", "trace": []}
        if not self.config.base_url.strip() or not self.config.model.strip():
            return {"ok": False, "final": "[Error] Base URL 或 Model 未配置。", "trace": []}
        # 将 stop_event 传递给 ApiClient 以便中断 SSE 流
        self._api.stop_event = stop_event
        if not self.config.use_ida_tools:
            return self._run_plain_turn(user_text, state, on_stream_chunk, on_stream_round_start, stop_event)
        return self._run_agent_turn(user_text, state, on_stream_chunk, on_stream_round_start, on_tool_trace, stop_event)

    def _run_plain_turn(
        self,
        user_text: str,
        state: ChatState,
        on_stream_chunk: StreamChunkCallback,
        on_stream_round_start: RoundStartCallback,
        stop_event: threading.Event = None,
    ) -> Dict[str, Any]:
        system_prompt = (self.config.plain_system_prompt or "").strip() or (
            "你是一个通用助手。当前未启用工具调用能力。"
            "不要尝试调用 MCP/函数工具，也不要虚构工具结果。"
            "请直接在纯文本能力范围内回答并说明限制。"
        )
        messages = self._msgs.build_plain_messages(state, user_text, system_prompt=system_prompt)
        if on_stream_round_start:
            on_stream_round_start()
        msg, err = self._api.stream_completion(messages, with_tools=False, on_stream_chunk=on_stream_chunk, stop_event=stop_event)
        if err:
            if stop_event and stop_event.is_set():
                return {"ok": False, "final": "[Stopped] 用户请求停止", "trace": [], "stopped": True}
            if ApiClient._should_retry_without_tools(err):
                msg, err = self._api.stream_completion(
                    messages, with_tools=False, on_stream_chunk=on_stream_chunk, stop_event=stop_event,
                )
            if err:
                return {"ok": False, "final": err, "trace": []}
        text = self._api.extract_assistant_text(msg).strip() or "[Error] 模型未返回内容。"
        return {"ok": not text.startswith("[Error]"), "final": text, "trace": [], "streamed": True}

    # ── Four-phase agent loop ─────────────────────────────────────────

    def _run_agent_turn(
        self,
        user_text: str,
        state: ChatState,
        on_stream_chunk: StreamChunkCallback,
        on_stream_round_start: RoundStartCallback,
        on_tool_trace: ToolTraceCallback,
        stop_event: threading.Event = None,
    ) -> Dict[str, Any]:
        """PLANNING -> EXECUTING -> VERIFYING -> SYNTHESIZING"""
        engine = self.workflow_service._engine
        engine.reset()

        budget = TurnBudget()
        trace: List[Dict[str, Any]] = []
        messages: List[Dict[str, Any]] = []
        self._conclusion_reached = False
        self._synth_retried = False
        self._decider.reset_novelty()

        # Phase 1: PLANNING
        if self.config.agent_enable_planning:
            self._planning_phase(
                messages, state, engine, budget, user_text,
                on_stream_chunk, on_stream_round_start,
                on_tool_trace, trace, stop_event,
            )
        else:
            engine.current_phase = AgentPhase.EXECUTING

        if stop_event and stop_event.is_set():
            return self._build_stopped_result(messages, trace, on_tool_trace)

        # Phase 2: EXECUTING
        self._executing_phase(
            messages, state, engine, budget, user_text,
            on_stream_chunk, on_stream_round_start, on_tool_trace, trace, stop_event,
        )
        if stop_event and stop_event.is_set():
            return self._build_stopped_result(messages, trace, on_tool_trace)
        if self._decider.is_last_message_user_question(messages):
            return self._return_question_to_user(messages, trace, on_tool_trace)

        # Phase 3: VERIFYING
        if self.config.agent_enable_hypothesis_tracking and not self._conclusion_reached:
            self._verifying_phase(
                messages, state, engine, budget, user_text,
                on_stream_chunk, on_stream_round_start, on_tool_trace, trace, stop_event,
            )
            if stop_event and stop_event.is_set():
                return self._build_stopped_result(messages, trace, on_tool_trace)
            if self._decider.is_last_message_user_question(messages):
                return self._return_question_to_user(messages, trace, on_tool_trace)

        # Skip SYNTHESIZING when a usable conclusion already exists
        if self._conclusion_reached:
            final_text = self._extract_last_conclusion(messages)
            if final_text:
                if self._decider._is_user_question(final_text):
                    return self._return_question_to_user(messages, trace, on_tool_trace)
                if not self._decider._is_incomplete_conclusion(final_text):
                    engine.current_phase = AgentPhase.COMPLETED
                    return {
                        "ok": True,
                        "final": final_text,
                        "trace": trace,
                        "streamed": True,
                        "trace_rendered_live": bool(on_tool_trace),
                        "workflow_completed": True,
                    }

        # Phase 4: SYNTHESIZING
        engine.current_phase = AgentPhase.SYNTHESIZING
        phase_messages = self._msgs.build_phase_messages(
            state, messages, engine, AgentPhase.SYNTHESIZING, user_text,
            system_prompt=self.config.system_prompt,
            skills_enabled=self.config.skills_enabled,
        )
        if on_stream_round_start:
            on_stream_round_start()
        msg, err = self._api.stream_completion(
            phase_messages, with_tools=False, on_stream_chunk=on_stream_chunk, stop_event=stop_event,
        )
        if err:
            if stop_event and stop_event.is_set():
                return self._build_stopped_result(messages, trace, on_tool_trace)
            return {"ok": False, "final": err, "trace": trace, "trace_rendered_live": bool(on_tool_trace)}

        engine.current_phase = AgentPhase.COMPLETED
        final_text = self._api.extract_assistant_text(msg).strip() or "[Error] 模型未返回内容。"
        final_text = self._decider.strip_hallucinated_tool_calls(final_text)

        # Retry once if the synthesis output is incomplete
        if self._decider._is_incomplete_conclusion(final_text) and not self._synth_retried:
            self._synth_retried = True
            retry_msg = (
                "[System] 你的上一次回答请求了更多工具调用或标记了'未解决'。"
                "工具调用阶段已经结束。你必须基于目前已有的数据直接给出分析结论。"
                "不要提'需要下一步'、'必须获取'等，只总结你已确认的事实。"
            )
            messages.append({"role": "system", "content": retry_msg})
            engine.current_phase = AgentPhase.SYNTHESIZING
            phase_messages = self._msgs.build_phase_messages(
                state, messages, engine, AgentPhase.SYNTHESIZING, user_text,
                system_prompt=self.config.system_prompt,
                skills_enabled=self.config.skills_enabled,
            )
            msg, err = self._api.stream_completion(
                phase_messages, with_tools=False, on_stream_chunk=on_stream_chunk, stop_event=stop_event,
            )
            if not err:
                final_text = self._api.extract_assistant_text(msg).strip() or ""
                final_text = self._decider.strip_hallucinated_tool_calls(final_text)

        if self._decider._is_user_question(final_text):
            return self._return_question_to_user(messages, trace, on_tool_trace)

        return {
            "ok": not final_text.startswith("[Error]"),
            "final": final_text,
            "trace": trace,
            "streamed": True,
            "trace_rendered_live": bool(on_tool_trace),
            "workflow_completed": True,
        }

    def _build_stopped_result(
        self,
        messages: List[Dict[str, Any]],
        trace: List[Dict[str, Any]],
        on_tool_trace: ToolTraceCallback,
    ) -> Dict[str, Any]:
        """构建停止后的返回结果，保留已完成轮次的上下文。"""
        # 从 messages 中提取最后一条 assistant 消息作为最终文本
        final_text = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
                final_text = msg["content"]
                break
        return {
            "ok": True,
            "final": final_text or "[Stopped] 用户请求停止",
            "trace": trace,
            "streamed": True,
            "trace_rendered_live": bool(on_tool_trace),
            "stopped": True,
        }

    # ── Phase implementations ─────────────────────────────────────────

    def _planning_phase(
        self,
        messages: List[Dict], state: ChatState, engine, budget: TurnBudget,
        user_text: str, on_stream_chunk: StreamChunkCallback,
        on_stream_round_start: RoundStartCallback,
        on_tool_trace: ToolTraceCallback = None,
        trace: List[Dict] = None,
        stop_event: threading.Event = None,
    ) -> None:
        """Phase 1: LLM-driven plan generation with optional tool calls."""
        engine.current_phase = AgentPhase.PLANNING
        max_rounds = max(1, int(self.config.agent_max_plan_rounds))
        tool_schemas = self._tool_schemas()

        for _ in range(max_rounds):
            if stop_event and stop_event.is_set():
                return
            budget.phase_round_count += 1
            phase_messages = self._msgs.build_phase_messages(
                state, messages, engine, AgentPhase.PLANNING, user_text,
                system_prompt=self.config.system_prompt,
                skills_enabled=self.config.skills_enabled,
            )
            if on_stream_round_start:
                on_stream_round_start()
            msg, err = self._api.stream_completion(
                phase_messages, with_tools=True, tool_schemas=tool_schemas,
                on_stream_chunk=on_stream_chunk, stop_event=stop_event,
            )
            if err:
                if stop_event and stop_event.is_set():
                    return
                if ApiClient._should_retry_without_tools(err):
                    msg, err = self._api.stream_completion(
                        phase_messages, with_tools=False, tool_schemas=tool_schemas,
                        on_stream_chunk=on_stream_chunk, stop_event=stop_event,
                    )
                if err:
                    break

            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""

            if tool_calls:
                assistant_msg = self._msgs.make_assistant_msg(msg)
                messages.append(assistant_msg)
                _, must_stop = self._pipeline.execute_batch(
                    tool_calls, messages, engine, budget,
                    recorder=None,
                    on_tool_trace=on_tool_trace,
                    trace=trace,
                )
                if must_stop:
                    self._conclusion_reached = True
                    return
                continue

            if engine.extract_plan_from_response(content):
                messages.append({"role": "assistant", "content": content})
                return

            messages.append({"role": "assistant", "content": content})

        engine.current_phase = AgentPhase.EXECUTING

    def _executing_phase(
        self,
        messages: List[Dict], state: ChatState, engine, budget: TurnBudget,
        user_text: str, on_stream_chunk: StreamChunkCallback,
        on_stream_round_start: RoundStartCallback,
        on_tool_trace: ToolTraceCallback, trace: List[Dict],
        stop_event: threading.Event = None,
    ) -> None:
        """Phase 2: Execute analysis plan with tool calls and evidence tracking."""
        engine.current_phase = AgentPhase.EXECUTING
        max_rounds = max(1, int(self.config.agent_max_execute_rounds))
        budget.fresh_evidence_count = 0
        budget.consecutive_stale_rounds = 0
        tool_schemas = self._tool_schemas()
        _retried = False

        for _ in range(max_rounds):
            if stop_event and stop_event.is_set():
                self._pipeline.cancel()
                return
            budget.phase_round_count += 1
            # Context compaction
            messages = self._msgs.compact_context(
                messages, engine,
                int(self.config.agent_compaction_threshold_chars),
            )

            phase_messages = self._msgs.build_phase_messages(
                state, messages, engine, AgentPhase.EXECUTING, user_text,
                system_prompt=self.config.system_prompt,
                skills_enabled=self.config.skills_enabled,
            )

            use_deferred = (
                self.config.tool_deferred_loading
                and budget.phase_round_count > self.config.tool_deferred_after_rounds
            )

            if on_stream_round_start:
                on_stream_round_start()

            # ── Incremental tool execution (interleaved with streaming) ──
            # result budget resets per-batch now
            self._pipeline.reset_incremental()

            def _on_tool(tc):
                self._pipeline.add_tool(tc)

            msg, err = self._api.stream_completion(
                phase_messages, with_tools=True, tool_schemas=tool_schemas,
                on_stream_chunk=on_stream_chunk, deferred_ok=use_deferred,
                on_tool_call=_on_tool, stop_event=stop_event,
            )
            if err:
                if stop_event and stop_event.is_set():
                    self._pipeline.cancel()
                    return
                if ApiClient._should_retry_without_tools(err):
                    msg, err = self._api.stream_completion(
                        phase_messages, with_tools=False, tool_schemas=tool_schemas,
                        on_stream_chunk=on_stream_chunk, deferred_ok=use_deferred,
                        on_tool_call=_on_tool, stop_event=stop_event,
                    )
                if err:
                    break

            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""

            # ── Decision-engine dispatch ──
            all_hypotheses = engine.hypothesis_tracker.hypotheses
            all_resolved = (
                all(
                    h.status in (HypothesisStatus.CONFIRMED, HypothesisStatus.DENIED)
                    for h in all_hypotheses.values()
                )
                if all_hypotheses
                else False
            )

            ctx = PhaseContext(
                content=content,
                tool_calls=tool_calls,
                phase=AgentPhase.EXECUTING,
                round=budget.phase_round_count,
                total_tool_calls=budget.total_tool_calls,
                fresh_evidence=budget.fresh_evidence_count,
                stale_rounds=budget.consecutive_stale_rounds,
                phase_round_count=budget.phase_round_count,
                has_hypotheses_resolved=all_resolved,
                novel_addresses=self._extract_targets(tool_calls),
            )
            decision = self._decider.decide(ctx)

            # --- non-CONTINUE: flush incremental tools before breaking ---
            if decision != PhaseDecision.CONTINUE:
                if decision == PhaseDecision.REJECT_AND_RETRY:
                    if _retried:
                        break
                    self._pipeline.cancel()
                    hint = self._decider.build_rejection_hint(ctx)
                    messages.append({"role": "system", "content": hint})
                    _retried = True
                    continue

                # Flush already-running incremental tools so GUI sees them
                if tool_calls:
                    results = self._pipeline.wait_all()
                    _, must_stop = self._pipeline.process_incremental_results(
                        results, tool_calls, messages, engine, budget,
                        recorder=None,
                        on_tool_trace=on_tool_trace,
                        trace=trace,
                    )
                    if must_stop:
                        self._conclusion_reached = True
                        break

                if decision == PhaseDecision.RETURN_TO_USER:
                    if content.strip():
                        messages.append({"role": "assistant", "content": content})
                        self._conclusion_reached = True
                elif decision == PhaseDecision.SKIP_TOOLS:
                    messages.append({"role": "assistant", "content": content})
                    self._conclusion_reached = True
                elif decision == PhaseDecision.FORCE_NEXT:
                    if content.strip():
                        messages.append({"role": "assistant", "content": content})
                break

            # --- CONTINUE (execute tools normally) ---
            # Address-guessing detection
            if self._decider._is_address_guessing(tool_calls):
                hint = (
                    "[System] 你似乎在对连续的地址进行猜测性读取。"
                    "请停止逐一尝试地址，改用 decompile_function 分析调用方函数"
                    "或 list_functions 搜索相关函数。"
                    "或者，如果你已经收集了足够信息，请直接给出分析结论。"
                )
                messages.append({"role": "system", "content": hint})
                budget.consecutive_stale_rounds += 2

            assistant_msg = self._msgs.make_assistant_msg(msg)
            messages.append(assistant_msg)

            # Incremental: tools are already executing/done by this point
            if tool_calls:
                results = self._pipeline.wait_all()
                fresh_count, must_stop = self._pipeline.process_incremental_results(
                    results, tool_calls, messages, engine, budget,
                    recorder=None,
                    on_tool_trace=on_tool_trace,
                    trace=trace,
                )
                if must_stop:
                    self._conclusion_reached = True
                    break
            else:
                fresh_count = 0

            if fresh_count > 0:
                budget.fresh_evidence_count += fresh_count
                budget.consecutive_stale_rounds = 0
            else:
                budget.consecutive_stale_rounds += 1

            # 新颖度驱动的提示（替代轮次计数）
            # 记录本轮探索目标, 用于下一轮的 novelty 决策
            novelty_targets = self._extract_targets(tool_calls)
            self._decider.record_targets(novelty_targets, budget.phase_round_count)

            hint = self._decider.build_novelty_hint(
                self._decider._consecutive_low_novelty
            )
            if hint:
                messages.append({"role": "system", "content": hint})
    def _verifying_phase(
        self,
        messages: List[Dict], state: ChatState, engine, budget: TurnBudget,
        user_text: str, on_stream_chunk: StreamChunkCallback,
        on_stream_round_start: RoundStartCallback,
        on_tool_trace: ToolTraceCallback, trace: List[Dict],
        stop_event: threading.Event = None,
    ) -> None:
        """Phase 3: Verify hypotheses with optional supplementary tool calls."""
        engine.current_phase = AgentPhase.VERIFYING
        max_rounds = max(1, int(self.config.agent_max_verify_rounds))
        budget.consecutive_stale_rounds = 0
        tool_schemas = self._tool_schemas()

        for _ in range(max_rounds):
            if stop_event and stop_event.is_set():
                self._pipeline.cancel()
                return
            budget.phase_round_count += 1
            phase_messages = self._msgs.build_phase_messages(
                state, messages, engine, AgentPhase.VERIFYING, user_text,
                system_prompt=self.config.system_prompt,
                skills_enabled=self.config.skills_enabled,
            )
            if on_stream_round_start:
                on_stream_round_start()

            # ── Incremental tool execution (interleaved with streaming) ──
            # result budget resets per-batch now
            self._pipeline.reset_incremental()

            def _on_tool_verify(tc):
                self._pipeline.add_tool(tc)

            msg, err = self._api.stream_completion(
                phase_messages, with_tools=True, tool_schemas=tool_schemas,
                on_stream_chunk=on_stream_chunk,
                on_tool_call=_on_tool_verify, stop_event=stop_event,
            )
            if err:
                if stop_event and stop_event.is_set():
                    self._pipeline.cancel()
                    return
                break

            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""

            all_hypotheses = engine.hypothesis_tracker.hypotheses
            all_resolved = (
                all(
                    h.status in (HypothesisStatus.CONFIRMED, HypothesisStatus.DENIED)
                    for h in all_hypotheses.values()
                )
                if all_hypotheses
                else False
            )

            ctx = PhaseContext(
                content=content,
                tool_calls=tool_calls,
                phase=AgentPhase.VERIFYING,
                round=budget.phase_round_count,
                total_tool_calls=budget.total_tool_calls,
                fresh_evidence=budget.fresh_evidence_count,
                stale_rounds=budget.consecutive_stale_rounds,
                phase_round_count=budget.phase_round_count,
                has_hypotheses_resolved=all_resolved,
                novel_addresses=self._extract_targets(tool_calls),
            )
            decision = self._decider.decide(ctx)

            # --- non-CONTINUE: flush incremental tools before breaking ---
            if decision != PhaseDecision.CONTINUE:
                if tool_calls:
                    verify_results = self._pipeline.wait_all()
                    _, must_stop = self._pipeline.process_incremental_results(
                        verify_results, tool_calls, messages, engine, budget,
                        recorder=None,
                        on_tool_trace=on_tool_trace,
                        trace=trace,
                    )
                    if must_stop:
                        self._conclusion_reached = True
                        break
                if decision == PhaseDecision.RETURN_TO_USER:
                    if content.strip():
                        messages.append({"role": "assistant", "content": content})
                        self._conclusion_reached = True
                elif decision in (PhaseDecision.FORCE_NEXT, PhaseDecision.REJECT_AND_RETRY):
                    if content.strip():
                        messages.append({"role": "assistant", "content": content})
                elif decision == PhaseDecision.SKIP_TOOLS:
                    messages.append({"role": "assistant", "content": content})
                    self._conclusion_reached = True
                break

            # CONTINUE
            if not tool_calls:
                messages.append({"role": "assistant", "content": content})
                break

            assistant_msg = self._msgs.make_assistant_msg(msg)
            messages.append(assistant_msg)

            # Incremental: tools are already executing/done by this point
            if tool_calls:
                verify_results = self._pipeline.wait_all()
                _, must_stop = self._pipeline.process_incremental_results(
                    verify_results, tool_calls, messages, engine, budget,
                    recorder=None,
                    on_tool_trace=on_tool_trace,
                    trace=trace,
                )
                if must_stop:
                    self._conclusion_reached = True
                    break
            budget.consecutive_stale_rounds += 1
            if budget.consecutive_stale_rounds >= 2:
                break

    # ── Helper methods ────────────────────────────────────────────────

    @staticmethod
    def _extract_targets(tool_calls: list) -> set:
        """从工具调用列表中提取本轮的探索目标 (地址/函数名)。

        用于新颖度追踪: 每个目标代表分析边界的一个点。
        新目标 = 边界扩张 = 高价值; 重复目标 = 绕圈 = 低价值。
        """
        targets = set()
        for tc in tool_calls:
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            # 地址类参数: 标准化为 hex 后加入目标集
            for key in ("address", "start_address", "function_address", "memory_address"):
                val = args.get(key, "")
                if isinstance(val, str) and val.strip():
                    try:
                        targets.add(f"{name}@{int(val.strip(), 0):#x}")
                    except ValueError:
                        pass
            # 名称类参数
            for key in ("name", "filter", "variable_name", "struct_name", "old_name", "new_name"):
                val = args.get(key, "")
                if isinstance(val, str) and val.strip():
                    targets.add(f"{name}@{val}")
        return targets

    def _extract_last_conclusion(self, messages: List[Dict]) -> str:
        """Extract the last assistant text as a conclusion."""
        for m in reversed(messages):
            if m.get("role") == "assistant":
                content = (m.get("content") or "").strip()
                if content:
                    return self._decider.strip_hallucinated_tool_calls(content)
        return ""

    def _return_question_to_user(
        self, messages: List[Dict], trace: List[Dict],
        on_tool_trace: ToolTraceCallback,
    ) -> Dict[str, Any]:
        question_text = ""
        for m in reversed(messages):
            if m.get("role") == "assistant":
                question_text = m.get("content", "") or ""
                break
        self.workflow_service._engine.current_phase = AgentPhase.COMPLETED
        return {
            "ok": True,
            "final": question_text or "[Assistant is asking for direction]",
            "trace": trace,
            "streamed": True,
            "trace_rendered_live": bool(on_tool_trace),
            "workflow_completed": True,
            "needs_user_input": True,
        }

    def _tool_schemas(self) -> List[Dict[str, Any]]:
        """Return current tool schemas from the manifest."""
        return self._tool_manifest.get_schemas(deferred_ok=False)

    # ── Backward-compatibility wrappers ───────────────────────────────
    # These methods are called directly by existing tests and external
    # code.  Each delegates to its extracted counterpart.

    @staticmethod
    def _make_assistant_msg(msg: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compat: delegates to ``MessageBuilder.make_assistant_msg``."""
        return MessageBuilder.make_assistant_msg(msg)

    def _run_tool_calls(
        self, tool_calls: List[Dict], messages: List[Dict],
        engine, budget: TurnBudget,
        on_tool_trace: ToolTraceCallback = None,
        trace: List[Dict] = None,
    ) -> int:
        """Backward-compat: delegates to ``ToolPipeline.execute_batch``."""
        fresh_count, _ = self._pipeline.execute_batch(
            tool_calls, messages, engine, budget,
            recorder=None,
            on_tool_trace=on_tool_trace,
            trace=trace,
        )
        return fresh_count

    def _check_diminishing_returns(self, budget: TurnBudget, engine) -> bool:
        """Backward-compat: delegates to ``PhaseDecisionEngine`` logic."""
        all_hypotheses = engine.hypothesis_tracker.hypotheses
        all_resolved = (
            all(
                h.status in (HypothesisStatus.CONFIRMED, HypothesisStatus.DENIED)
                for h in all_hypotheses.values()
            )
            if all_hypotheses
            else False
        )
        ctx = PhaseContext(
            stale_rounds=budget.consecutive_stale_rounds,
            phase_round_count=budget.phase_round_count,
            fresh_evidence=budget.fresh_evidence_count,
            total_tool_calls=budget.total_tool_calls,
            has_hypotheses_resolved=all_resolved,
        )
        if self._decider._is_diminishing(ctx):
            return True
        if budget.total_tool_calls >= 20:
            return True
        if budget.phase_round_count >= 10:
            return True
        return False

    def _execute_llm4decompile_refine(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compat: delegates to ``ToolPipeline._execute_llm4decompile_refine``."""
        return self._pipeline._execute_llm4decompile_refine(args)
