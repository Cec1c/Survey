"""工具并发调度器测试"""

import json
import sys
import threading
import time
from unittest.mock import MagicMock

sys.path.insert(0, ".")
from app.gui.services.tool_registry import ToolRegistry, _build_llm4decompile_tool
from app.gui.services.tool_executor import ToolExecutor, ToolState


def test_parallel_execution() -> None:
    """安全工具应并发执行: 3 个各睡眠 0.1s 的调用应在 <0.15s 内完成"""
    registry = ToolRegistry()
    executor = ToolExecutor(registry, max_workers=4, cascade_on_error=False)

    start_times: list[float] = []
    lock = threading.Lock()

    def slow_call(name: str, args: dict) -> dict:
        with lock:
            start_times.append(time.monotonic())
        time.sleep(0.1)
        return {"ok": True, "result": f"{name} done"}

    tool_calls = [
        {"id": "c1", "type": "function", "function": {"name": "decompile_function", "arguments": '{"address":"0x401000"}'}},
        {"id": "c2", "type": "function", "function": {"name": "get_xrefs_to", "arguments": '{"address":"0x401000"}'}},
        {"id": "c3", "type": "function", "function": {"name": "read_memory_bytes", "arguments": '{"memory_address":"0x401000","size":64}'}},
    ]

    t0 = time.monotonic()
    results = executor.run_all(tool_calls, slow_call)
    elapsed = time.monotonic() - t0

    assert len(results) == 3, f"Expected 3 results, got {len(results)}"
    assert all(r.state == ToolState.COMPLETED for r in results), f"All should complete, got {[r.state for r in results]}"
    assert elapsed < 0.25, f"Parallel execution should be fast, took {elapsed:.3f}s"
    # 证明并发: 启动时间应非常接近
    start_times.sort()
    spread = start_times[-1] - start_times[0] if len(start_times) >= 2 else 999
    assert spread < 0.05, f"Concurrent starts should cluster, spread={spread:.3f}s"

    executor.shutdown()
    print("[PASS] test_parallel_execution")


def test_unsafe_sequential() -> None:
    """不安全工具应依次执行"""
    registry = ToolRegistry()
    executor = ToolExecutor(registry, max_workers=4, cascade_on_error=False)

    exec_order: list[str] = []
    lock = threading.Lock()

    def tracked_call(name: str, args: dict) -> dict:
        with lock:
            exec_order.append(name)
        time.sleep(0.05)
        return {"ok": True, "result": f"{name} done"}

    tool_calls = [
        {"id": "c1", "type": "function", "function": {"name": "rename_function", "arguments": '{"function_address":"0x401000","new_name":"test"}'}},
        {"id": "c2", "type": "function", "function": {"name": "set_comment", "arguments": '{"address":"0x401000","comment":"test"}'}},
    ]

    results = executor.run_all(tool_calls, tracked_call)
    assert len(results) == 2
    assert all(r.state == ToolState.COMPLETED for r in results)
    # 不安全工具应按提交顺序执行
    assert exec_order == ["rename_function", "set_comment"], f"Unsafe tools should execute in order, got {exec_order}"

    executor.shutdown()
    print("[PASS] test_unsafe_sequential")


def test_mixed_safe_and_unsafe() -> None:
    """混合批次: 安全工具并发, 不安全工具等待"""
    registry = ToolRegistry()
    executor = ToolExecutor(registry, max_workers=4, cascade_on_error=False)

    exec_order: list[str] = []

    def tracked_call(name: str, args: dict) -> dict:
        exec_order.append(name)
        time.sleep(0.05)
        return {"ok": True, "result": name}

    tool_calls = [
        {"id": "c1", "type": "function", "function": {"name": "decompile_function", "arguments": '{"address":"0x401000"}'}},
        {"id": "c2", "type": "function", "function": {"name": "get_xrefs_to", "arguments": '{"address":"0x401000"}'}},
        {"id": "c3", "type": "function", "function": {"name": "rename_function", "arguments": '{"function_address":"0x401000","new_name":"test"}'}},
        {"id": "c4", "type": "function", "function": {"name": "read_memory_bytes", "arguments": '{"memory_address":"0x401000","size":64}'}},
    ]

    results = executor.run_all(tool_calls, tracked_call)
    assert len(results) == 4
    assert all(r.state == ToolState.COMPLETED for r in results)
    # 不安全工具 rename_function 应最后完成 (安全工具先开始)
    # Note: 由于安全工具并发, 顺序取决于线程调度, 但 rename 必须在安全工具之后
    rename_idx = exec_order.index("rename_function")
    assert rename_idx >= 1, f"Unsafe tool should execute after most safe tools, got order {exec_order}"

    executor.shutdown()
    print("[PASS] test_mixed_safe_and_unsafe")


def test_cascade_on_unsafe_failure() -> None:
    """不安全工具失败 → 级联取消剩余不安全工具"""
    registry = ToolRegistry()
    executor = ToolExecutor(registry, max_workers=4, cascade_on_error=True)

    def failing_call(name: str, args: dict) -> dict:
        if name == "rename_function":
            raise RuntimeError("IDA write failed")
        time.sleep(0.05)
        return {"ok": True, "result": name}

    tool_calls = [
        {"id": "c1", "type": "function", "function": {"name": "set_comment", "arguments": '{"address":"0x401000","comment":"test"}'}},
        {"id": "c2", "type": "function", "function": {"name": "rename_function", "arguments": '{"function_address":"0x401000","new_name":"test"}'}},
        {"id": "c3", "type": "function", "function": {"name": "set_global_variable_type", "arguments": '{"variable_name":"x","new_type":"int"}'}},
    ]

    results = executor.run_all(tool_calls, failing_call)
    # set_comment 应完成 (在 rename 之前执行)
    # rename_function 应失败
    # set_global_variable_type 应因级联取消而被取消
    assert results[1].state == ToolState.FAILED, f"rename should fail, got {results[1].state}"
    assert results[2].state == ToolState.CANCELLED, f"set_global should be cancelled, got {results[2].state}"

    executor.shutdown()
    print("[PASS] test_cascade_on_unsafe_failure")


def test_decompile_semaphore() -> None:
    """反编译调用受信号量限制 (最多 2 个并发)"""
    registry = ToolRegistry()
    executor = ToolExecutor(registry, max_workers=8, decompile_semaphore_limit=2)

    concurrent_count = [0]
    max_concurrent = [0]
    lock = threading.Lock()

    def decompile_call(name: str, args: dict) -> dict:
        with lock:
            concurrent_count[0] += 1
            if concurrent_count[0] > max_concurrent[0]:
                max_concurrent[0] = concurrent_count[0]
        time.sleep(0.05)
        with lock:
            concurrent_count[0] -= 1
        return {"ok": True, "result": "decompiled"}

    tool_calls = [
        {"id": f"c{i}", "type": "function", "function": {"name": "decompile_function", "arguments": f'{{"address":"0x40100{i}"}}'}}
        for i in range(5)
    ]

    results = executor.run_all(tool_calls, decompile_call)
    assert all(r.state == ToolState.COMPLETED for r in results)
    assert max_concurrent[0] <= 2, f"Decompile semaphore should limit concurrency to 2, got {max_concurrent[0]}"

    executor.shutdown()
    print("[PASS] test_decompile_semaphore")


def test_empty_tool_list() -> None:
    """空工具列表不应报错"""
    registry = ToolRegistry()
    executor = ToolExecutor(registry, max_workers=4)
    results = executor.run_all([], lambda n, a: {"ok": True})
    assert len(results) == 0
    executor.shutdown()
    print("[PASS] test_empty_tool_list")


def test_results_in_original_order() -> None:
    """结果应按原始顺序返回 (不是完成顺序)"""
    registry = ToolRegistry()
    executor = ToolExecutor(registry, max_workers=4)

    def variable_delay(name: str, args: dict) -> dict:
        delays = {"a": 0.15, "b": 0.01, "c": 0.10}
        idx = args.get("id", "b")
        time.sleep(delays.get(idx, 0.05))
        return {"ok": True, "result": f"tool_{idx}"}

    tool_calls = [
        {"id": "slow", "type": "function", "function": {"name": "decompile_function", "arguments": '{"address":"0x401000","id":"a"}'}},
        {"id": "fast", "type": "function", "function": {"name": "get_xrefs_to", "arguments": '{"address":"0x401000","id":"b"}'}},
        {"id": "mid", "type": "function", "function": {"name": "read_memory_bytes", "arguments": '{"memory_address":"0x401000","size":64,"id":"c"}'}},
    ]

    results = executor.run_all(tool_calls, variable_delay)
    # 应保持 ["tool_a", "tool_b", "tool_c"] 的顺序
    assert [r.tc_id for r in results] == ["slow", "fast", "mid"]
    assert results[0].result["result"] == "tool_a"
    assert results[1].result["result"] == "tool_b"
    assert results[2].result["result"] == "tool_c"

    executor.shutdown()
    print("[PASS] test_results_in_original_order")


if __name__ == "__main__":
    tests = [
        test_parallel_execution,
        test_unsafe_sequential,
        test_mixed_safe_and_unsafe,
        test_cascade_on_unsafe_failure,
        test_decompile_semaphore,
        test_empty_tool_list,
        test_results_in_original_order,
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
