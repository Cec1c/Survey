"""Single tool execution pipeline with integrated concurrency, retry, and result mgmt.

Merges the former ToolExecutor, ToolResultManager, and ErrorRecoveryHandler into one
orchestrator. Uses ToolManifest for concurrency-safety queries.
"""

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from app.gui.services.tool_hooks import HookContext, ToolHookChain
from app.gui.services.mcp_service import MCPService
from app.gui.services.tool_manifest import ToolManifest
from app.gui.state.chat_state import TurnBudget


class ToolPipeline:
    """Executes tool calls: hook -> cache -> (retry + MCP call) -> post-hook.

    Pipeline:
        pre-hooks -> cache lookup -> retry(MCP call) -> post-hooks -> result trim

    Also handles batch execution with concurrency control (safe tools in parallel,
    unsafe tools sequentially).
    """

    # Per-category string limits for LLM results
    CATEGORY_LIMITS = {
        "analysis": 64_000,
        "memory": 4_000,
        "modify": 2_000,
        "query": 8_000,
        "struct": 8_000,
        "stack": 4_000,
        "meta": 2_000,
        "python": 16_000,
        "debug": 8_000,
        "default": 1_200,
    }

    _upx_path: str = ""

    def __init__(
        self,
        manifest: ToolManifest,
        mcp_service: MCPService,
        hook_chain: ToolHookChain,
        persistent_cache: Dict[str, Any],
        llm4decompile_service=None,
        max_workers: int = 8,
        decompile_limit: int = 2,
        max_retries: int = 2,
        global_result_budget_chars: int = 120_000,
    ):
        self.manifest = manifest
        self.mcp = mcp_service
        self.hooks = hook_chain
        self.cache = persistent_cache
        self._llm4decompile = llm4decompile_service
        self.max_workers = max_workers
        self.max_retries = max_retries
        self._decompile_sem = threading.BoundedSemaphore(max(1, decompile_limit))
        self._global_budget = global_result_budget_chars
        self._global_used = 0
        self._binary_sha256: str = ""

        # Incremental execution state
        self._inc_futures: dict = {}
        self._inc_lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────────────

    def execute_single(self, tool_name: str, args: Dict) -> Any:
        """Execute one tool: pre-hook -> cache -> retry(MCP) -> post-hook."""
        # 1. Pre hooks
        hook_ctx = HookContext(phase="")
        hook_result = self.hooks.run_pre(tool_name, args, hook_ctx)
        if isinstance(hook_result, dict) and hook_result.get("blocked"):
            return {"ok": False, "error": hook_result.get("error", "Blocked by pre-tool hook")}
        if hook_result is not None:
            args = hook_result

        # 2. Cache check (get_metadata always bypasses cache to detect binary changes)
        cache_key = self._make_cache_key(tool_name, args)
        if tool_name != "get_metadata" and cache_key in self.cache:
            cached = self.cache[cache_key]
            cached["from_cache"] = True
            return cached

        # 3. Execute (with special routing for local tools)
        if tool_name == "llm4decompile_refine":
            result = self._execute_llm4decompile_refine(args)
        elif tool_name == "upx_unpack":
            result = self._execute_upx_unpack(args)
        else:
            result = self._execute_with_retry(tool_name, args)

        # 4. Post hooks
        result = self.hooks.run_post(tool_name, args, result, hook_ctx)

        # 5. Binary change detection: if the IDA binary changed, invalidate all cached results
        if tool_name == "get_metadata" and isinstance(result, dict) and result.get("ok"):
            meta = result.get("result") or {}
            sha256 = str(meta.get("sha256", ""))
            if sha256:
                if self._binary_sha256 and sha256 != self._binary_sha256:
                    self.cache.clear()
                self._binary_sha256 = sha256

        return result

    def _execute_with_retry(self, tool_name: str, args: Dict) -> Dict:
        """Call MCP with up to max_retries attempts for transient errors."""
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = self.mcp.call_tool(tool_name, args)
                if isinstance(raw, dict) and raw.get("ok") is False:
                    err_msg = str(raw.get("error", ""))
                    if self._is_transient(err_msg):
                        if attempt < self.max_retries:
                            time.sleep(0.5 * (attempt + 1))
                            continue
                    return raw
                return raw
            except Exception as exc:
                last_err = str(exc)
                if self._is_transient(last_err) and attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return {"ok": False, "error": last_err}
        return {"ok": False, "error": last_err or "unknown"}

    @staticmethod
    def _is_transient(error_msg: str) -> bool:
        """Check if an error is likely transient (timeout, connection drop)."""
        low = error_msg.lower()
        return any(kw in low for kw in ("timeout", "connection", "reset", "busy", "lock", "retry"))

    # ── Batch execution with concurrency control ─────────────────────

    def execute_batch(
        self,
        tool_calls: List[Dict],
        messages: List[Dict],
        engine,
        budget: TurnBudget,
        recorder=None,
        on_tool_trace=None,
        trace=None,
    ) -> tuple:
        """Execute a batch of tool calls with safe/unsafe concurrency control.

        Safe tools run in parallel; unsafe tools run sequentially.
        Returns (fresh_count, must_stop).
        """
        self._global_used = 0
        safe_calls, unsafe_calls = self._partition(tool_calls)

        # Unsafe first (sequential)
        all_results: List[Any] = [None] * len(tool_calls)
        idx_map = {id(tc): i for i, tc in enumerate(tool_calls)}

        for tc in unsafe_calls:
            idx = idx_map[id(tc)]
            name = tc.get("function", {}).get("name", "")
            try:
                args = json.loads(tc.get("function", {}).get("arguments", "{}"))
            except Exception:
                args = {}
            all_results[idx] = _ToolCallResult(name, args, self.execute_single(name, args))

        # Safe in parallel
        if safe_calls:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {}
                for tc in safe_calls:
                    name = tc.get("function", {}).get("name", "")
                    try:
                        args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                    except Exception:
                        args = {}
                    fut = pool.submit(self._execute_with_semaphore, name, args)
                    futures[fut] = (idx_map[id(tc)], name, args)

                for fut in as_completed(futures):
                    idx, name, args = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:
                        result = {"ok": False, "error": str(exc)}
                    all_results[idx] = _ToolCallResult(name, args, result)

        return self._process_results(
            tool_calls, all_results, messages, engine, budget, recorder, on_tool_trace, trace
        )

    def process_incremental_results(
        self,
        results: list,
        tool_calls: List[Dict],
        messages: List[Dict],
        engine,
        budget: TurnBudget,
        recorder=None,
        on_tool_trace=None,
        trace=None,
    ) -> tuple:
        """Process pre-computed incremental results (same loop as execute_batch)."""
        self._global_used = 0
        return self._process_results(
            tool_calls, results, messages, engine, budget, recorder, on_tool_trace, trace
        )

    # ── Incremental execution (streaming mode) ───────────────────────

    def reset_incremental(self, call_tool_fn=None):
        with self._inc_lock:
            self._inc_futures.clear()

    def add_tool(self, tc: Dict):
        name = tc.get("function", {}).get("name", "")
        try:
            args = json.loads(tc.get("function", {}).get("arguments", "{}"))
        except Exception:
            args = {}
        if self.manifest.is_concurrency_safe(name):
            pool = getattr(self, "_inc_pool", None)
            if pool is None:
                self._inc_pool = ThreadPoolExecutor(max_workers=self.max_workers)
                pool = self._inc_pool
            fut = pool.submit(self._execute_with_semaphore, name, args)
            with self._inc_lock:
                self._inc_futures[id(tc)] = (name, args, fut)
        else:
            result = self.execute_single(name, args)
            with self._inc_lock:
                self._inc_futures[id(tc)] = (name, args, _DoneFuture(result))

    def wait_all(self) -> list:
        results = []
        with self._inc_lock:
            items = list(self._inc_futures.items())
        for tc_id, (name, args, fut) in items:
            try:
                result = fut.result() if hasattr(fut, "result") else fut.result()
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            results.append(_ToolCallResult(name, args, result))
        # Shutdown the incremental pool
        pool = getattr(self, "_inc_pool", None)
        if pool:
            pool.shutdown(wait=False)
            self._inc_pool = None
        return results

    def cancel(self):
        """取消所有待执行的增量工具调用。"""
        with self._inc_lock:
            for tc_id, (name, args, fut) in self._inc_futures.items():
                if hasattr(fut, 'cancel'):
                    fut.cancel()
            self._inc_futures.clear()

    # ── Internal helpers ─────────────────────────────────────────────

    def _execute_with_semaphore(self, tool_name: str, args: Dict) -> Dict:
        if tool_name == "decompile_function":
            with self._decompile_sem:
                return self.execute_single(tool_name, args)
        return self.execute_single(tool_name, args)

    def _partition(self, tool_calls: List[Dict]) -> tuple:
        safe, unsafe = [], []
        for tc in tool_calls:
            name = tc.get("function", {}).get("name", "")
            if self.manifest.is_concurrency_safe(name):
                safe.append(tc)
            else:
                unsafe.append(tc)
        return safe, unsafe

    def _process_results(
        self, tool_calls, results, messages, engine, budget,
        recorder, on_tool_trace, trace,
    ) -> tuple:
        fresh_count = 0
        must_stop = False
        for tc, result in zip(tool_calls, results):
            if result is None:
                continue
            tool_name = result.name
            args_obj = result.arguments

            if isinstance(result, _ToolCallResult) and result.error:
                tool_result = {"ok": False, "error": result.error}
            elif isinstance(result, _ToolCallResult):
                raw = result.result
                filtered = self._filter_tool_result(tool_name, raw)
                if isinstance(raw, dict) and raw.get("from_cache"):
                    filtered["from_cache"] = True
                self.cache[self._make_cache_key(tool_name, args_obj)] = filtered
                tool_result = filtered
            else:
                tool_result = result if isinstance(result, dict) else {"ok": False, "error": str(result)}

            # Evidence tracking (simplified: unified evidence list)
            evidence = engine.record_tool_call(
                tool_name, args_obj, tool_result, budget.phase_round_count
            )
            if evidence:
                fresh_count += 1

            budget.total_tool_calls += 1

            if recorder:
                recorder.record_tool_call(
                    tool_name, args_obj, tool_result,
                    cached=tool_result.get("from_cache", False),
                )

            trace_entry = {"tool": tool_name, "arguments": args_obj, "result": tool_result}
            if trace is not None:
                trace.append(trace_entry)
            if on_tool_trace:
                on_tool_trace(tool_name, args_obj, tool_result)

            # Build tool message for LLM
            raw_json = json.dumps(tool_result, ensure_ascii=False)
            tool_content = self._trim_result(raw_json, tool_name)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": tool_content,
            })

            if not tool_result.get("ok"):
                self._inject_error_feedback(tool_name, tool_result, args_obj, messages)
            if self._check_must_stop(tool_result, messages):
                must_stop = True

        return fresh_count, must_stop

    def _trim_result(self, raw_json: str, tool_name: str = "") -> str:
        """Trim result to per-category and global budget limits."""
        cat = self.manifest.get_category(tool_name)
        # Use per-category limit for individual results
        limit = self.CATEGORY_LIMITS.get(cat, 1_200)
        # Apply global budget
        remaining = self._global_budget - self._global_used
        if remaining <= 0:
            return '{"ok":true,"result":"[result omitted: context budget exceeded]"}'
        limit = min(limit, remaining)
        if len(raw_json) <= limit:
            self._global_used += len(raw_json)
            return raw_json
        trimmed = raw_json[:limit] + f'... [truncated {len(raw_json) - limit} chars]'
        self._global_used += len(trimmed)
        return trimmed

    def _inject_error_feedback(self, tool_name, tool_result, args_obj, messages):
        err = tool_result.get("error", "")
        known = self.manifest.get_names()

        if tool_name not in known and tool_name not in ("llm4decompile_refine", "upx_unpack"):
            hint = (
                f"Tool '{tool_name}' does not exist. Available: {', '.join(sorted(known))}. "
                f"For reading memory, use read_bytes or read_integer. "
                f"Do NOT invent tool names -- use only those listed above."
            )
            messages.append({"role": "system", "content": hint})
            return

        if tool_name in known:
            base_hint = f"Error from {tool_name}: {str(err)[:300]}."
            for key in ("address", "start_address", "function_address", "memory_address", "query"):
                addr_val = args_obj.get(key, "")
                if isinstance(addr_val, str) and not self._is_valid_hex(addr_val):
                    base_hint += (
                        f" '{addr_val}' is NOT a valid hex address. "
                        f"Addresses must be hex numbers like 0x401000. "
                        f"Do NOT use variable names (dword_xxx, byte_xxx) as addresses. "
                        f"Use list_functions or get_global_value to find actual addresses."
                    )
                    break
            else:
                base_hint += (
                    " Do NOT retry with the same arguments. "
                    "Try a different approach or move on to the next analysis step."
                )
            messages.append({"role": "system", "content": base_hint})

    @staticmethod
    def _make_cache_key(tool_name: str, args: Dict) -> str:
        try:
            args_norm = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            args_norm = str(args)
        digest = hashlib.sha1(args_norm.encode("utf-8", errors="replace")).hexdigest()
        return f"{tool_name}:{digest}"

    @staticmethod
    def _check_must_stop(tool_result: Dict, messages: List[Dict]) -> bool:
        if not isinstance(tool_result, dict) or not tool_result.get("ok"):
            return False
        payload = tool_result.get("result", {})
        if isinstance(payload, dict) and payload.get("must_stop"):
            messages.append({
                "role": "system",
                "content": (
                    "[System] UPX解壳完成。请简短告知用户：\n"
                    "1. 解壳成功，文件已修改\n"
                    "2. 请关闭IDA并重新打开解壳后的文件\n"
                    "3. 给出文件路径\n"
                    "不要调用任何工具，直接输出以上信息。"
                ),
            })
            return True
        return False

    @staticmethod
    def _is_valid_hex(val: str) -> bool:
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

    # ── Result filtering ─────────────────────────────────────────────

    def _filter_tool_result(self, tool_name: str, tool_result: Any) -> Any:
        if not isinstance(tool_result, dict):
            return tool_result
        ok = bool(tool_result.get("ok", False))
        if not ok:
            return {"ok": False, "error": str(tool_result.get("error", "tool call failed"))}
        payload = tool_result.get("result")
        if payload is None:
            return {"ok": True, "result": None}
        return {"ok": True, "result": self._extract_key_facts(payload, 0, "")}

    def _extract_key_facts(self, value: Any, depth: int = 0, key: str = "") -> Any:
        if depth > 4:
            return "...(max depth)"
        if isinstance(value, str):
            lim = self._string_truncate_limit_for_key(key)
            return self._truncate_text(value, lim)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [self._extract_key_facts(v, depth + 1, key) for v in value[:256]]
        if isinstance(value, dict):
            important_keys = {
                "address", "addr", "start_address", "end_address", "image_base",
                "input_path", "input_name", "path", "filename", "file",
                "name", "size", "offset", "total", "items", "connected",
                "ida_version", "is_64bit", "sha256",
                "pseudocode", "lines", "line", "xrefs", "xref", "callers", "callees",
                "registers", "regs", "thread_id",
                "tid", "rip", "eip", "rax", "rbx", "rcx", "rdx", "rsp", "rbp",
                "data", "value", "symbol", "module", "error", "ok", "filter",
            }
            out: Dict[str, Any] = {}
            for k, v in value.items():
                lk = str(k).lower()
                if (
                    lk in important_keys
                    or "addr" in lk or "reg" in lk or "ref" in lk
                    or lk.endswith("name") or lk.endswith("path")
                ):
                    out[k] = self._extract_key_facts(v, depth + 1, str(k))
            if not out:
                for k, v in list(value.items())[:10]:
                    out[k] = self._extract_key_facts(v, depth + 1, str(k))
            return out
        return str(value)

    @staticmethod
    def _string_truncate_limit_for_key(key: str) -> int:
        lk = (key or "").lower()
        if lk in ("pseudocode", "decompilation", "disassembly", "assembly"):
            return 64_000
        if lk in ("lines", "text", "body"):
            return 32_000
        return 1_200

    @staticmethod
    def _truncate_text(text: str, limit: int = 1200) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"... [truncated {len(text) - limit} chars]"

    # ── Local tools ──────────────────────────────────────────────────

    def _execute_llm4decompile_refine(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not self._llm4decompile:
            return {"ok": False, "error": "LLM4Decompile service not available"}
        address = args.get("address", "")
        mode = args.get("mode", "refine")
        if not address:
            return {"ok": False, "error": "address is required"}
        decompile_result = self.mcp.call_tool("decompile_function", {"address": address})
        if not decompile_result.get("ok"):
            return {"ok": False, "error": f"IDA decompile failed: {decompile_result.get('error')}"}
        pseudocode = decompile_result.get("result", {}).get("pseudocode", "")
        function_name = decompile_result.get("result", {}).get("name", "")
        if not pseudocode:
            return {"ok": False, "error": "IDA returned empty pseudocode"}
        if mode == "two_phase":
            llm4_result = self._llm4decompile.refine_two_phase(pseudocode, function_name)
        else:
            llm4_result = self._llm4decompile.refine_pseudocode(pseudocode, function_name, address)
        if not llm4_result.get("ok"):
            return {
                "ok": True, "result": {
                    "address": address, "function_name": function_name,
                    "original_pseudocode": pseudocode, "refined_code": None,
                    "note": f"LLM4Decompile failed ({llm4_result.get('error')}), returning original",
                    "model": "IDA Hex-Rays (unrefined)",
                },
            }
        return {
            "ok": True, "result": {
                "address": address, "function_name": function_name,
                "original_pseudocode": pseudocode[:3000],
                "refined_code": llm4_result.get("refined_code") or llm4_result.get("source_code"),
                "struct_ir": llm4_result.get("struct_ir"),
                "model": llm4_result.get("model") or llm4_result.get("phase2_model") or self._llm4decompile.model,
                "mode": mode,
            },
        }

    @classmethod
    def set_upx_path(cls, path: str) -> None:
        cls._upx_path = path

    def _execute_upx_unpack(self, args: Dict[str, Any]) -> Dict[str, Any]:
        import os as _os, shutil, subprocess as _subprocess
        upx = _os.path.abspath(self._upx_path) if self._upx_path else ""
        if not upx:
            # 从skills目录读取 (4层dirname: services → gui → app → rift)
            _project_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
            _skills_dir = _os.path.join(_project_root, "skills", "upx_unpack")
            _path_file = _os.path.join(_skills_dir, "upx_path.txt")
            if _os.path.exists(_path_file):
                with open(_path_file, 'r', encoding='utf-8') as _f:
                    upx = _f.read().strip()
                # 如果是相对路径，转换为绝对路径
                if not _os.path.isabs(upx):
                    upx = _os.path.join(_project_root, upx)
            else:
                upx = _os.path.join(_skills_dir, "upx-5.0.2-win64", "upx.exe")
        if not _os.path.isfile(upx):
            return {"ok": False, "error": f"UPX not found at {upx}"}
        file_path = args.get("file_path", "").strip()
        if not file_path:
            meta = self.mcp.call_tool("get_metadata", {})
            if isinstance(meta, dict) and meta.get("ok"):
                file_path = meta.get("result", {}).get("input_path", "")
            if not file_path:
                return {"ok": False, "error": "No file path and cannot get from IDA"}
        file_path = _os.path.abspath(file_path)
        if not _os.path.isfile(file_path):
            return {"ok": False, "error": f"File not found: {file_path}"}
        try:
            backup = file_path + ".backup"
            if not _os.path.exists(backup):
                shutil.copy2(file_path, backup)
            orig_size = _os.path.getsize(file_path)
            info_result = _subprocess.run([upx, "-l", file_path], capture_output=True, text=True, timeout=15)
            is_packed = info_result.returncode == 0 and "NotPackedException" not in info_result.stderr
            if not is_packed:
                return {"ok": True, "result": {"file_path": file_path, "is_packed": False, "message": "Not UPX packed"}}
            t0 = time.monotonic()
            result = _subprocess.run([upx, "-d", file_path], capture_output=True, text=True, timeout=60)
            elapsed = time.monotonic() - t0
            if result.returncode != 0:
                result = _subprocess.run([upx, "-d", "--force", file_path], capture_output=True, text=True, timeout=60)
            unpacked_size = _os.path.getsize(file_path) if _os.path.isfile(file_path) else 0
            ok = result.returncode == 0
            return {
                "ok": ok, "result": {
                    "file_path": file_path, "backup_path": backup, "is_packed": True,
                    "original_size": orig_size, "unpacked_size": unpacked_size,
                    "duration_seconds": round(elapsed, 2),
                    "message": (
                        f"Unpacked ({orig_size} -> {unpacked_size} bytes, {elapsed:.1f}s). Re-open in IDA: {file_path}"
                        if ok else f"Unpack failed: {result.stderr[:300]}"
                    ),
                    "must_stop": ok,
                },
            }
        except _subprocess.TimeoutExpired:
            return {"ok": False, "error": "UPX unpack timeout (60s)"}
        except Exception as exc:
            return {"ok": False, "error": f"UPX unpack error: {exc}"}


# ── Internal helper types ────────────────────────────────────────────

class _ToolCallResult:
    """Result of a single tool call (replaces former ToolCallResult from ToolExecutor)."""
    __slots__ = ("name", "arguments", "result", "error")
    def __init__(self, name: str, arguments: Dict, result: Any = None, error: str = None):
        self.name = name
        self.arguments = arguments
        self.result = result
        self.error = error


class _DoneFuture:
    """A future-like wrapper for already-completed results."""
    def __init__(self, result):
        self._result = result
    def result(self, timeout=None):
        return self._result
