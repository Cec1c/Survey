"""Agent 工作流重构测试套件（简化版）

测试内容:
1. HypothesisTracker 生命周期（新格式）
2. AnalysisEngine plan 解析（[plan]:{...} 格式）
3. AnalysisEngine 证据提取
4. TurnBudget 收益递减检测
5. WorkflowService 向后兼容
6. AgentChatService 新方法
7. Phase 构建
"""

import json
import os
import sys

from app.gui.services.decision_engine import PhaseDecisionEngine, PhaseDecision, PhaseContext

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def test_hypothesis_extraction():
    """从LLM输出中提取假设（新格式）"""
    from app.gui.services.workflow_service import _HypothesisTracker

    tracker = _HypothesisTracker()

    # 测试基本提取
    content = """分析这个程序的加密算法。

[plan]:{先分析入口函数，然后搜索加密相关字符串}
[H1]:{该程序可能使用了AES加密}
[H2]:{可能存在密钥硬编码}"""

    result = tracker.extract_plan_and_hypotheses(content, current_turn=1)
    assert result == True, "Should extract plan and hypotheses"
    assert tracker.plan == "先分析入口函数，然后搜索加密相关字符串"
    assert len(tracker.hypotheses) == 2
    assert "H1" in tracker.hypotheses
    assert "H2" in tracker.hypotheses
    assert tracker.hypotheses["H1"].description == "该程序可能使用了AES加密"
    assert tracker.hypotheses["H1"].status.value == "ACTIVE"

    print("[PASS] test_hypothesis_extraction")


def test_hypothesis_status_update():
    """假设状态更新"""
    from app.gui.services.workflow_service import _HypothesisTracker

    tracker = _HypothesisTracker()

    # Turn 1: 提出假设
    content1 = "[plan]:{分析入口函数}\n[H1]:{可能使用AES加密}"
    tracker.extract_plan_and_hypotheses(content1, current_turn=1)
    assert tracker.hypotheses["H1"].status.value == "ACTIVE"

    # Turn 2: 确认假设
    content2 = "[plan]:{追踪密钥来源}\n[H1]:{可能使用AES加密} - CONFIRMED"
    tracker.extract_plan_and_hypotheses(content2, current_turn=2)
    assert tracker.hypotheses["H1"].status.value == "CONFIRMED"
    assert tracker.hypotheses["H1"].last_mentioned == 2

    print("[PASS] test_hypothesis_status_update")


def test_hypothesis_stale():
    """假设自动降级为STALE"""
    from app.gui.services.workflow_service import _HypothesisTracker

    tracker = _HypothesisTracker()

    # Turn 1: 提出假设
    content1 = "[plan]:{分析入口函数}\n[H1]:{可能使用AES加密}\n[H2]:{可能存在反调试}"
    tracker.extract_plan_and_hypotheses(content1, current_turn=1)

    # Turn 2: 只提及H1
    content2 = "[plan]:{继续分析}\n[H1]:{可能使用AES加密} - CONFIRMED"
    tracker.extract_plan_and_hypotheses(content2, current_turn=2)
    assert tracker.hypotheses["H1"].status.value == "CONFIRMED"
    assert tracker.hypotheses["H2"].status.value == "ACTIVE"  # 还未降级

    # Turn 3: 仍未提及H2，应该降级为STALE
    content3 = "[plan]:{分析密钥来源}"
    tracker.extract_plan_and_hypotheses(content3, current_turn=3)
    assert tracker.hypotheses["H2"].status.value == "STALE"

    print("[PASS] test_hypothesis_stale")


def test_hypothesis_denied():
    """假设被否决"""
    from app.gui.services.workflow_service import _HypothesisTracker

    tracker = _HypothesisTracker()

    # Turn 1: 提出假设
    content1 = "[plan]:{分析入口函数}\n[H1]:{可能使用AES加密}"
    tracker.extract_plan_and_hypotheses(content1, current_turn=1)

    # Turn 2: 否决假设
    content2 = "[plan]:{继续分析}\n[H1]:{可能使用AES加密} - DENIED"
    tracker.extract_plan_and_hypotheses(content2, current_turn=2)
    assert tracker.hypotheses["H1"].status.value == "DENIED"

    print("[PASS] test_hypothesis_denied")


def test_hypothesis_summary():
    """假设摘要生成"""
    from app.gui.services.workflow_service import _HypothesisTracker

    tracker = _HypothesisTracker()

    # 提出多个假设
    content = """[plan]:{分析程序}
[H1]:{可能使用AES加密}
[H2]:{可能存在反调试}
[H3]:{可能有网络通信} - DENIED"""
    tracker.extract_plan_and_hypotheses(content, current_turn=1)

    # 测试活跃假设摘要
    active_summary = tracker.get_active_summary()
    assert "H1" in active_summary
    assert "H2" in active_summary
    assert "H3" not in active_summary  # DENIED不在活跃摘要中

    # 测试完整摘要
    full_summary = tracker.get_full_summary()
    assert "H1" in full_summary
    assert "H2" in full_summary
    assert "H3" in full_summary  # DENIED在完整摘要中
    assert "已否决" in full_summary

    print("[PASS] test_hypothesis_summary")


def test_plan_extraction_valid():
    """有效的 plan 被正确解析"""
    from app.gui.services.workflow_service import _AnalysisEngine

    engine = _AnalysisEngine()
    content = """分析计划如下：

[plan]:{先检查元数据，然后反编译入口函数}
[H1]:{程序使用了AES加密}

以上为计划。"""

    assert engine.extract_plan_from_response(content), "Should extract valid plan"
    assert len(engine.steps) == 1
    assert engine.steps[0].description == "先检查元数据，然后反编译入口函数"
    assert len(engine.hypothesis_tracker.hypotheses) == 1
    assert "H1" in engine.hypothesis_tracker.hypotheses
    print("[PASS] test_plan_extraction_valid")


def test_plan_extraction_no_format():
    """无格式化内容时返回 False"""
    from app.gui.services.workflow_service import _AnalysisEngine

    engine = _AnalysisEngine()
    assert not engine.extract_plan_from_response("I will analyze the binary step by step...")
    assert not engine.plan_generated
    print("[PASS] test_plan_extraction_no_format")


def test_plan_extraction_no_hypotheses():
    """只有plan没有hypotheses也可以接受"""
    from app.gui.services.workflow_service import _AnalysisEngine

    engine = _AnalysisEngine()
    content = '[plan]:{先分析入口函数，然后搜索加密字符串}'
    assert engine.extract_plan_from_response(content)
    assert len(engine.steps) == 1
    assert len(engine.hypothesis_tracker.hypotheses) == 0
    print("[PASS] test_plan_extraction_no_hypotheses")


def test_evidence_extraction():
    """工具结果被正确提取为 Evidence"""
    from app.gui.services.workflow_service import _AnalysisEngine

    engine = _AnalysisEngine()
    result = {
        "ok": True,
        "result": {"name": "sub_401000", "address": "0x401000",
                    "pseudocode": "int v1; return v1;"}
    }
    evidence = engine.record_tool_call(
        "decompile_function", {"address": "0x401000"}, result, 1
    )
    assert evidence is not None, "Should extract evidence from valid result"
    assert "sub_401000" in evidence.fact_summary, f"Fact summary: {evidence.fact_summary}"
    assert evidence.tool_name == "decompile_function"
    assert evidence.round_number == 1

    # 检查证据是否添加到统一列表
    assert len(engine.evidence_summary) == 1
    assert "sub_401000" in engine.evidence_summary[0]

    # 失败的工具调用应返回 None evidence
    fail_result = {"ok": False, "error": "timed out"}
    evidence2 = engine.record_tool_call(
        "get_function_by_name", {"name": "nonexistent"}, fail_result, 2
    )
    assert evidence2 is None, "Failed tool call should not produce evidence"

    print("[PASS] test_evidence_extraction")


def test_turn_budget_diminishing_returns():
    """收益递减检测"""
    from app.gui.state.chat_state import TurnBudget, HypothesisStatus
    from app.gui.services.workflow_service import _AnalysisEngine
    from app.gui.services.chat_service import AgentChatService
    from unittest.mock import MagicMock

    # 场景1: 连续空轮次 >= 阈值
    budget = TurnBudget()
    budget.consecutive_stale_rounds = 5
    engine = _AnalysisEngine()

    # 创建一个 mock agent 来调用 _check_diminishing_returns
    from app.gui.state.llm_config import LLMConfig
    from app.gui.services.mcp_service import MCPService
    config = LLMConfig()
    config.agent_stale_round_limit = 3
    mcp = MCPService(host="127.0.0.1", port=31337, timeout_seconds=2.0)
    agent = AgentChatService(config=config, mcp_service=mcp)

    assert agent._check_diminishing_returns(budget, engine), \
        "Should detect diminishing returns with 5 stale rounds (limit=3)"

    # 场景2: 新颖度追踪替代了旧的多轮无证据检测
    for r in (1, 2, 3):
        ctx = PhaseContext(
            tool_calls=[{'id':'x','function':{'name':'decompile_function','arguments':'{}'}}],
            novel_addresses={'decompile_function@0x401000'},
            phase_round_count=r,
            stale_rounds=0,
        )
        d = agent._decider.decide(ctx)
        agent._decider.record_targets({'decompile_function@0x401000'}, r)
        if r == 3:
            assert d == PhaseDecision.FORCE_NEXT, \
                f"Novelty tracking should force next on round {r}, got {d}"

    # 场景3: 所有假设已解决
    budget3 = TurnBudget()
    budget3.phase_round_count = 3
    budget3.consecutive_stale_rounds = 0
    budget3.fresh_evidence_count = 3
    engine3 = _AnalysisEngine()
    # 手动添加假设并设置状态
    from app.gui.state.chat_state import Hypothesis
    engine3.hypothesis_tracker.hypotheses["H1"] = Hypothesis(
        hid="H1", description="test", turn_number=1, last_mentioned=1,
        status=HypothesisStatus.CONFIRMED
    )
    assert agent._check_diminishing_returns(budget3, engine3), \
        "Should detect diminishing returns when all hypotheses resolved"

    print("[PASS] test_turn_budget_diminishing_returns")


def test_workflow_service_backward_compat():
    """WorkflowService 向后兼容接口"""
    from app.gui.services.workflow_service import WorkflowService, WorkflowStage

    ws = WorkflowService()

    # 旧接口
    ws.create_workflow("analyze binary")
    assert ws.current_workflow is not None
    assert ws.current_stage == WorkflowStage.PLANNING
    assert ws.should_continue_execution() is True

    # record_tool_call
    ws.record_tool_call("get_metadata", {}, {"ok": True, "result": {"ida_version": "9.0"}})
    calls = ws.get_recent_tool_calls(5)
    assert len(calls) > 0
    assert calls[0]["tool_name"] == "get_metadata"

    # has_tool_been_called
    assert ws.has_tool_been_called("get_metadata", {})
    assert not ws.has_tool_been_called("nonexistent", {})

    # workflow_progress
    progress = ws.get_workflow_progress()
    assert "current_stage" in progress
    assert "total_steps" in progress

    # summary
    summary = ws.get_workflow_summary()
    assert isinstance(summary, str)

    # reset
    ws.reset_workflow()
    assert len(ws._engine.steps) == 0
    assert len(ws._engine.hypothesis_tracker.hypotheses) == 0

    print("[PASS] test_workflow_service_backward_compat")


def test_agent_phase_methods():
    """验证 AgentChatService 新方法能正常工作"""
    from app.gui.state.llm_config import LLMConfig
    from app.gui.state.chat_state import ChatState, AgentPhase
    from app.gui.services.mcp_service import MCPService
    from app.gui.services.chat_service import AgentChatService

    config = LLMConfig()
    config.use_ida_tools = False
    mcp = MCPService(host="127.0.0.1", port=31337, timeout_seconds=2.0)
    agent = AgentChatService(config=config, mcp_service=mcp)
    state = ChatState()
    engine = agent.workflow_service._engine

    # 测试各阶段 prompt 生成
    pp = engine.planning_system_prompt()
    assert "计划" in pp
    assert "[plan]:{" in pp

    ep = engine.executing_system_prompt()
    assert "执行" in ep

    vp = engine.verifying_system_prompt()
    assert "验证" in vp

    sp = engine.synthesizing_system_prompt()
    assert "总结" in sp

    print("[PASS] test_agent_phase_methods")


def test_tool_call_execution():
    """工具调用执行 (无 IDA 连接时的降级)"""
    from app.gui.state.llm_config import LLMConfig
    from app.gui.state.chat_state import TurnBudget
    from app.gui.services.mcp_service import MCPService
    from app.gui.services.chat_service import AgentChatService

    config = LLMConfig()
    config.use_ida_tools = True  # 启用工具 (但 IDA 不会连接)
    mcp = MCPService(host="127.0.0.1", port=31337, timeout_seconds=2.0)
    agent = AgentChatService(config=config, mcp_service=mcp)
    engine = agent.workflow_service._engine
    budget = TurnBudget()

    # 模拟工具调用 (会因连接超时而失败，但不应崩溃)
    tool_calls = [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_metadata", "arguments": "{}"},
    }]
    messages = []
    fresh = agent._run_tool_calls(tool_calls, messages, engine, budget)
    assert isinstance(fresh, int), f"Should return int, got {type(fresh)}"
    assert len(messages) > 0, "Should append tool result to messages"

    print("[PASS] test_tool_call_execution")


def test_make_assistant_msg():
    """_make_assistant_msg 正确构造消息"""
    from app.gui.services.chat_service import AgentChatService

    msg = {
        "role": "assistant",
        "content": "Hello",
        "reasoning_content": "thinking...",
        "tool_calls": [{"id": "1", "function": {"name": "test", "arguments": "{}"}}],
    }
    result = AgentChatService._make_assistant_msg(msg)
    assert result["role"] == "assistant"
    assert result["content"] == "Hello"
    assert result["reasoning_content"] == "thinking..."
    assert "tool_calls" in result
    print("[PASS] test_make_assistant_msg")


def test_turn_conclusion():
    """Turn结论记录"""
    from app.gui.services.workflow_service import _AnalysisEngine

    engine = _AnalysisEngine()
    engine.add_turn_conclusion("入口函数调用了AES初始化")
    engine.add_turn_conclusion("密钥来自注册表")

    assert len(engine.turn_history) == 2
    assert "Turn 1" in engine.turn_history[0]
    assert "Turn 2" in engine.turn_history[1]
    assert engine._current_turn == 3

    print("[PASS] test_turn_conclusion")


def test_evidence_trail():
    """证据追踪摘要"""
    from app.gui.services.workflow_service import _HypothesisTracker

    tracker = _HypothesisTracker()
    evidence_summary = [
        "decompile_function: name=sub_401000",
        "list_strings: 5 items",
        "get_xrefs_to: 3 items",
    ]

    trail = tracker.get_evidence_trail(evidence_summary, max_items=2)
    assert "已收集的证据" in trail
    assert "list_strings" in trail
    assert "get_xrefs_to" in trail
    assert "decompile_function" not in trail  # 被max_items截断

    print("[PASS] test_evidence_trail")


if __name__ == "__main__":
    print("=" * 60)
    print("Agent 工作流重构测试套件（简化版）")
    print("=" * 60)
    print()

    tests = [
        ("假设提取（新格式）", test_hypothesis_extraction),
        ("假设状态更新", test_hypothesis_status_update),
        ("假设自动降级", test_hypothesis_stale),
        ("假设否决", test_hypothesis_denied),
        ("假设摘要生成", test_hypothesis_summary),
        ("Plan 提取（有效）", test_plan_extraction_valid),
        ("无格式降级", test_plan_extraction_no_format),
        ("无假设 Plan", test_plan_extraction_no_hypotheses),
        ("证据提取", test_evidence_extraction),
        ("收益递减检测", test_turn_budget_diminishing_returns),
        ("WorkflowService 向后兼容", test_workflow_service_backward_compat),
        ("Phase 方法", test_agent_phase_methods),
        ("工具调用执行", test_tool_call_execution),
        ("Assistant 消息构造", test_make_assistant_msg),
        ("Turn 结论记录", test_turn_conclusion),
        ("证据追踪摘要", test_evidence_trail),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()

    print()
    print("=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
