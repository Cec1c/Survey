"""ToolPipeline 测试 — 纯函数 + mock MCP 并发控制"""

import json
import time
import pytest
from unittest.mock import MagicMock

from app.gui.services.tool_manifest import ToolManifest
from app.gui.services.tool_pipeline import ToolPipeline
from app.gui.services.tool_hooks import ToolHookChain
from app.gui.state.chat_state import TurnBudget


def _make_pipeline(mcp_call_fn=None, **kwargs):
    """创建测试用 ToolPipeline，mock MCP 调用"""
    manifest = ToolManifest()
    mcp = MagicMock()
    if mcp_call_fn:
        mcp.call_tool.side_effect = mcp_call_fn
    else:
        mcp.call_tool.return_value = {"ok": True, "result": "mock_result"}
    hooks = ToolHookChain()
    cache = {}
    return ToolPipeline(
        manifest=manifest,
        mcp_service=mcp,
        hook_chain=hooks,
        persistent_cache=cache,
        max_workers=kwargs.get("max_workers", 4),
        decompile_limit=kwargs.get("decompile_limit", 2),
        max_retries=kwargs.get("max_retries", 0),
        global_result_budget_chars=kwargs.get("global_result_budget_chars", 120_000),
    ), mcp


def _make_mock_engine():
    """创建 mock engine，提供 record_tool_call 和 hypothesis_tracker"""
    engine = MagicMock()
    engine.record_tool_call.return_value = None  # no evidence for basic tests
    engine.hypothesis_tracker.hypotheses = {}
    return engine


def _make_tc(idx: str, name: str, args: dict) -> dict:
    return {
        "id": idx,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


# ═══════════════════════════════════════════════════════════════════
# _partition
# ═══════════════════════════════════════════════════════════════════

class TestPartition:
    def test_all_safe(self):
        pipeline, _ = _make_pipeline()
        calls = [
            _make_tc("1", "decompile_function", {"address": "0x1"}),
            _make_tc("2", "get_xrefs_to", {"address": "0x2"}),
        ]
        safe, unsafe = pipeline._partition(calls)
        assert len(safe) == 2
        assert len(unsafe) == 0

    def test_all_unsafe(self):
        pipeline, _ = _make_pipeline()
        calls = [
            _make_tc("1", "rename_function", {"function_address": "0x1", "new_name": "x"}),
            _make_tc("2", "set_comment", {"address": "0x2", "comment": "c"}),
        ]
        safe, unsafe = pipeline._partition(calls)
        assert len(safe) == 0
        assert len(unsafe) == 2

    def test_mixed(self):
        pipeline, _ = _make_pipeline()
        calls = [
            _make_tc("1", "decompile_function", {"address": "0x1"}),      # safe
            _make_tc("2", "rename_function", {"function_address": "0x1", "new_name": "x"}),  # unsafe
        ]
        safe, unsafe = pipeline._partition(calls)
        assert len(safe) == 1
        assert safe[0]["function"]["name"] == "decompile_function"
        assert len(unsafe) == 1
        assert unsafe[0]["function"]["name"] == "rename_function"


# ═══════════════════════════════════════════════════════════════════
# _is_valid_hex
# ═══════════════════════════════════════════════════════════════════

class TestIsValidHex:
    @pytest.mark.parametrize("val,expected", [
        ("0x401000", True),
        ("0x0", True),
        ("0", True),
        ("255", True),
        ("0xABCDEF", True),
        ("dword_431754", False),
        ("", False),
        ("   ", False),
        ("0x_401000", False),
        ("not_hex", False),
    ])
    def test_valid_hex(self, val, expected):
        assert ToolPipeline._is_valid_hex(val) == expected


# ═══════════════════════════════════════════════════════════════════
# _make_cache_key
# ═══════════════════════════════════════════════════════════════════

class TestMakeCacheKey:
    def test_same_args_same_key(self):
        k1 = ToolPipeline._make_cache_key("f", {"a": 1, "b": 2})
        k2 = ToolPipeline._make_cache_key("f", {"b": 2, "a": 1})
        assert k1 == k2

    def test_different_tool_different_key(self):
        k1 = ToolPipeline._make_cache_key("f1", {"a": 1})
        k2 = ToolPipeline._make_cache_key("f2", {"a": 1})
        assert k1 != k2

    def test_different_args_different_key(self):
        k1 = ToolPipeline._make_cache_key("f", {"a": 1})
        k2 = ToolPipeline._make_cache_key("f", {"a": 2})
        assert k1 != k2


# ═══════════════════════════════════════════════════════════════════
# _is_transient
# ═══════════════════════════════════════════════════════════════════

class TestIsTransient:
    @pytest.mark.parametrize("error,expected", [
        ("connection timeout", True),
        ("Connection reset by peer", True),
        ("IDA is busy", True),
        ("database is locked", True),
        ("retry later", True),
        ("invalid address 0x401000", False),
        ("tool not found", False),
        ("permission denied", False),
    ])
    def test_transient_detection(self, error, expected):
        assert ToolPipeline._is_transient(error) == expected


# ═══════════════════════════════════════════════════════════════════
# execute_batch — 并发控制
# ═══════════════════════════════════════════════════════════════════

class TestExecuteBatchConcurrency:
    def test_empty_batch(self):
        pipeline, _ = _make_pipeline()
        budget = TurnBudget()
        result = pipeline.execute_batch([], [], None, budget)
        assert result == 0

    def test_safe_tools_run(self):
        """安全工具并发执行，验证 MCP 被调用"""
        pipeline, mcp = _make_pipeline()
        budget = TurnBudget()
        calls = [
            _make_tc("1", "decompile_function", {"address": "0x401000"}),
            _make_tc("2", "get_xrefs_to", {"address": "0x401000"}),
        ]
        engine = _make_mock_engine()
        pipeline.execute_batch(calls, [], engine, budget)
        assert mcp.call_tool.call_count == 2

    def test_unsafe_tools_sequential(self):
        """不安全工具依次执行，exec_order 与 call_count 匹配"""
        pipeline, mcp = _make_pipeline()
        budget = TurnBudget()
        calls = [
            _make_tc("1", "rename_function", {"function_address": "0x1", "new_name": "a"}),
            _make_tc("2", "set_comment", {"address": "0x2", "comment": "c"}),
            _make_tc("3", "rename_global_variable", {"variable_name": "x", "new_name": "y"}),
        ]
        engine = _make_mock_engine()
        pipeline.execute_batch(calls, [], engine, budget)
        assert mcp.call_tool.call_count == 3

    def test_results_preserve_input_order(self):
        """结果顺序 = 输入 tool_calls 顺序"""
        pipeline, mcp = _make_pipeline()
        mcp.call_tool.side_effect = [
            {"ok": True, "result": "first"},
            {"ok": True, "result": "second"},
            {"ok": True, "result": "third"},
        ]
        budget = TurnBudget()
        calls = [
            _make_tc("a", "get_xrefs_to", {"address": "0x1"}),
            _make_tc("b", "get_xrefs_to", {"address": "0x2"}),
            _make_tc("c", "get_xrefs_to", {"address": "0x3"}),
        ]
        engine = _make_mock_engine()
        pipeline.execute_batch(calls, [], engine, budget)
        assert mcp.call_tool.call_count == 3

    def test_decompile_semaphore_limits_concurrency(self):
        """反编译受信号量限制: 5 个并发最多 2 个同时执行"""
        pipeline, mcp = _make_pipeline(max_workers=8, decompile_limit=2)

        running = [0]
        max_running = [0]
        def sem_tracked(name, args):
            running[0] += 1
            if running[0] > max_running[0]:
                max_running[0] = running[0]
            time.sleep(0.05)
            running[0] -= 1
            return {"ok": True, "result": f"{name}={args.get('address','')}"}

        mcp.call_tool.side_effect = sem_tracked
        budget = TurnBudget()
        calls = [
            _make_tc(str(i), "decompile_function", {"address": f"0x40100{i}"})
            for i in range(5)
        ]
        engine = _make_mock_engine()
        pipeline.execute_batch(calls, [], engine, budget)
        assert max_running[0] <= 2, f"Semaphore should cap at 2, got {max_running[0]}"


# ═══════════════════════════════════════════════════════════════════
# _filter_tool_result
# ═══════════════════════════════════════════════════════════════════

class TestFilterToolResult:
    def test_ok_result_preserved(self):
        pipeline, _ = _make_pipeline()
        result = pipeline._filter_tool_result("get_xrefs_to", {"ok": True, "result": "some data"})
        assert result.get("ok") is True
        assert result.get("result") == "some data"

    def test_failed_result_condensed(self):
        pipeline, _ = _make_pipeline()
        result = pipeline._filter_tool_result("f", {"ok": False, "error": "something broke"})
        assert result.get("ok") is False
        assert "something broke" in result.get("error", "")

    def test_non_dict_passthrough(self):
        pipeline, _ = _make_pipeline()
        result = pipeline._filter_tool_result("f", "raw_string")
        assert result == "raw_string"


# ═══════════════════════════════════════════════════════════════════
# execute_single — 重试
# ═══════════════════════════════════════════════════════════════════

class TestExecuteSingle:
    def test_execute_batch_caches_result(self):
        """通过 execute_batch 路径验证缓存: 第二次调用不触发 MCP"""
        pipeline, mcp = _make_pipeline()
        mcp.call_tool.return_value = {"ok": True, "result": "cached_data"}
        engine = _make_mock_engine()
        budget = TurnBudget()
        tc = _make_tc("1", "get_xrefs_to", {"address": "0x1"})

        pipeline.execute_batch([tc], [], engine, budget)
        assert mcp.call_tool.call_count == 1

        mcp.call_tool.reset_mock()
        pipeline.execute_batch([tc], [], engine, budget)
        # Cached: no additional MCP call
        assert mcp.call_tool.call_count == 0

    def test_execute_single_retries_once(self):
        pipeline, mcp = _make_pipeline(max_retries=1)
        mcp.call_tool.side_effect = [
            Exception("connection timeout"),
            {"ok": True, "result": "recovered"},
        ]
        result = pipeline.execute_single("get_xrefs_to", {"address": "0x1"})
        assert result.get("ok") is True
        assert result.get("result") == "recovered"
        assert mcp.call_tool.call_count == 2
