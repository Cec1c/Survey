"""工具结果管理器和错误恢复测试"""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.gui.services.tool_registry import ToolRegistry
from app.gui.services.tool_result_manager import ToolResultManager
from app.gui.services.error_recovery import (
    ErrorRecoveryHandler, RecoveryStrategy,
    READONLY_STRATEGY, WRITE_STRATEGY, HEAVY_STRATEGY,
)


# ── ToolResultManager 测试 ────────────────────────────────────────────

def test_small_result_inline() -> None:
    """小结果应原样返回"""
    registry = ToolRegistry()
    mgr = ToolResultManager(registry, disk_storage_limit_mb=1)

    result = mgr.store("check_connection", {}, '{"ok":true,"result":"connected"}')
    assert "connected" in result
    assert "saved to" not in result  # 不应溢出到磁盘
    print("[PASS] test_small_result_inline")


def test_large_result_disk_overflow() -> None:
    """大结果 (>默认 1200 限制) 应溢出到磁盘。使用非 code 工具。"""
    registry = ToolRegistry()
    with tempfile.TemporaryDirectory() as td:
        mgr = ToolResultManager(registry, temp_dir=td, disk_storage_limit_mb=1)

        # read_memory_bytes 限制为 1200 (default), 5000 > 1200 应溢出
        big_text = "x" * 5000
        result = mgr.store("read_memory_bytes", {"memory_address": "0x401000", "size": 64}, big_text)

        assert "saved to" in result, f"Should save to disk, got preview only: {result[:100]}"
        assert len(result) < len(big_text), "Preview should be shorter than original"
        print("[PASS] test_large_result_disk_overflow")


def test_global_budget_truncation() -> None:
    """超出全局预算时截断"""
    registry = ToolRegistry()
    mgr = ToolResultManager(registry, disk_storage_limit_mb=1)
    # 手动设置很小的预算
    mgr._max_total = 500
    mgr._current_total = 400

    result = mgr.store("get_metadata", {}, "x" * 300)
    # 只能保留 100 字符
    assert len(result) < 300
    assert "truncated" in result.lower()
    print("[PASS] test_global_budget_truncation")


def test_reset_clears_budget() -> None:
    """reset() 应清零预算"""
    registry = ToolRegistry()
    mgr = ToolResultManager(registry)

    mgr._current_total = 50000
    mgr._entries.append(MagicMock())
    mgr.reset()

    assert mgr._current_total == 0
    assert len(mgr._entries) == 0
    print("[PASS] test_reset_clears_budget")


def test_budget_info_string() -> None:
    """get_budget_info 应返回格式化的状态字符串"""
    registry = ToolRegistry()
    mgr = ToolResultManager(registry)
    info = mgr.get_budget_info()

    assert "chars" in info
    assert "results" in info.lower() or "个结果" in info
    print("[PASS] test_budget_info_string")


def test_category_limits() -> None:
    """不同工具类型应有不同的结果大小限制"""
    registry = ToolRegistry()

    assert registry.get_max_result_chars("decompile_function") == 64_000
    assert registry.get_max_result_chars("get_xrefs_to") == 16_000
    assert registry.get_max_result_chars("check_connection") == 2_000
    assert registry.get_max_result_chars("read_memory_bytes") == 1_200
    print("[PASS] test_category_limits")


# ── ErrorRecovery 测试 ─────────────────────────────────────────────

def test_successful_call_no_recovery() -> None:
    """成功调用应直接返回, 不触发恢复"""
    handler = ErrorRecoveryHandler()

    def good_call(tool_name: str, args: dict) -> dict:
        return {"ok": True, "result": "success"}

    result, err = handler.run_with_recovery("decompile_function", {"address": "0x401000"}, good_call)
    assert err is None
    assert result["ok"] is True
    print("[PASS] test_successful_call_no_recovery")


def test_transient_error_retries() -> None:
    """临时错误应触发重试"""
    handler = ErrorRecoveryHandler()
    calls = [0]

    def flaky_call(tool_name: str, args: dict) -> dict:
        calls[0] += 1
        if calls[0] < 3:
            raise ConnectionRefusedError("temporary connection failure")
        return {"ok": True, "result": "finally worked"}

    result, err = handler.run_with_recovery("decompile_function", {"address": "0x401000"}, flaky_call)
    assert calls[0] == 3, f"Should retry 2 times (3 total calls), got {calls[0]}"
    assert result["ok"] is True
    print("[PASS] test_transient_error_retries")


def test_permanent_error_no_retry() -> None:
    """永久错误不应重试"""
    handler = ErrorRecoveryHandler()
    calls = [0]

    def bad_call(tool_name: str, args: dict) -> dict:
        calls[0] += 1
        return {"ok": False, "error": "invalid argument: not found"}

    result, err = handler.run_with_recovery("rename_function", {"function_address": "0x401000", "new_name": ""}, bad_call)
    assert calls[0] == 1, f"Permanent error should NOT retry, got {calls[0]} calls"
    print("[PASS] test_permanent_error_no_retry")


def test_withheld_errors_accumulate() -> None:
    """隐匿错误应累积到 _withheld 列表"""
    handler = ErrorRecoveryHandler()

    def always_fail(tool_name: str, args: dict) -> dict:
        raise TimeoutError("connection timed out")

    _, err1 = handler.run_with_recovery("decompile_function", {"address": "0x401000"}, always_fail)
    _, err2 = handler.run_with_recovery("get_xrefs_to", {"address": "0x401000"}, always_fail)

    # 隐匿模式下 err 应为 ""
    assert err1 == ""
    assert err2 == ""
    withheld = handler.pop_withheld_errors()
    assert len(withheld) == 2, f"Should have 2 withheld errors, got {len(withheld)}"
    assert withheld[0]["tool"] == "decompile_function"
    assert withheld[1]["tool"] == "get_xrefs_to"
    print("[PASS] test_withheld_errors_accumulate")


def test_write_strategy_no_withhold() -> None:
    """WRITE_STRATEGY 不应隐匿错误"""
    handler = ErrorRecoveryHandler(default_strategy=WRITE_STRATEGY)

    def fail(tool_name: str, args: dict) -> dict:
        raise RuntimeError("write failed")

    result, err = handler.run_with_recovery("rename_function", {"function_address": "0x401000", "new_name": "test"}, fail)
    assert err != ""  # 写入工具不应隐匿
    assert result["ok"] is False
    withheld = handler.pop_withheld_errors()
    assert len(withheld) == 0, "Write strategy should NOT withhold"
    print("[PASS] test_write_strategy_no_withhold")


def test_build_withheld_context() -> None:
    """build_withheld_context 应格式化隐匿错误"""
    handler = ErrorRecoveryHandler()

    def fail(tool_name: str, args: dict) -> dict:
        raise TimeoutError("timeout")

    handler.run_with_recovery("decompile_function", {"address": "0x401000"}, fail)

    ctx = handler.build_withheld_context()
    assert "decompile_function" in ctx
    assert "timeout" in ctx.lower()
    print("[PASS] test_build_withheld_context")


def test_specific_tool_strategies() -> None:
    """每个工具的策略应正确配置"""
    handler = ErrorRecoveryHandler()
    handler.set_tool_strategy("decompile_function", HEAVY_STRATEGY)
    handler.set_tool_strategy("rename_function", WRITE_STRATEGY)

    assert handler.get_strategy("decompile_function").max_retries == 3
    assert handler.get_strategy("rename_function").max_retries == 0
    # 默认策略
    assert handler.get_strategy("unknown_tool").max_retries == 2
    print("[PASS] test_specific_tool_strategies")


if __name__ == "__main__":
    tests = [
        test_small_result_inline,
        test_large_result_disk_overflow,
        test_global_budget_truncation,
        test_reset_clears_budget,
        test_budget_info_string,
        test_category_limits,
        test_successful_call_no_recovery,
        test_transient_error_retries,
        test_permanent_error_no_retry,
        test_withheld_errors_accumulate,
        test_write_strategy_no_withhold,
        test_build_withheld_context,
        test_specific_tool_strategies,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {test.__name__}: {e}")
    print(f"\nTotal: {passed} passed, {failed} failed, {passed + failed} total")
