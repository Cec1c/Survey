import json
import socket
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib import error, request

from app.gui.state.llm_config import LLMConfig

# ── 回调类型 ──────────────────────────────────────────────────────────────

StreamChunkCallback = Optional[Callable[[str, str], None]]
"""on_stream_chunk(kind: "content"|"reasoning", text: str) -> None"""

ToolCallCallback = Optional[Callable[[Dict[str, Any]], None]]
"""on_tool_call(tc_dict: dict) -> None"""


# ── 辅助累加器 ───────────────────────────────────────────────────────────

class _ToolCallStreamAccumulator:
    """Merge OpenAI-style streaming tool_calls fragments by index.

    Supports early-completion detection for interleaved tool execution:
    ``pop_completed_calls()`` returns tool calls whose arguments have
    accumulated into valid JSON, so they can be dispatched before the
    full stream finishes.
    """

    def __init__(self) -> None:
        self._by_index: Dict[int, Dict[str, Any]] = {}
        self._reported_indices: Set[int] = set()

    def add_delta(self, parts: List[Dict[str, Any]]) -> None:
        for tc in parts:
            idx = int(tc.get("index", 0))
            if idx not in self._by_index:
                self._by_index[idx] = {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                }
            if tc.get("id"):
                self._by_index[idx]["id"] = tc["id"]
            if tc.get("type"):
                self._by_index[idx]["type"] = tc["type"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                self._by_index[idx]["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                self._by_index[idx]["function"]["arguments"] += fn["arguments"]

    def pop_completed_calls(self) -> List[Dict[str, Any]]:
        """Return tool calls whose arguments have become valid JSON since last check.

        Each tool call is reported at most once.  Used by ``on_tool_call``
        to dispatch executions early.
        """
        completed: List[Dict[str, Any]] = []
        for idx, data in self._by_index.items():
            if idx in self._reported_indices:
                continue
            args = data["function"]["arguments"]
            if args and self._is_json_complete(args):
                self._reported_indices.add(idx)
                completed.append({
                    "id": data["id"],
                    "type": data.get("type") or "function",
                    "function": {
                        "name": data["function"]["name"],
                        "arguments": args,
                    },
                })
        return completed

    def pop_unreported_calls(self) -> List[Dict[str, Any]]:
        """Force-report all remaining unreported calls (called at stream end)."""
        remaining: List[Dict[str, Any]] = []
        for idx, data in self._by_index.items():
            if idx in self._reported_indices:
                continue
            self._reported_indices.add(idx)
            remaining.append({
                "id": data["id"],
                "type": data.get("type") or "function",
                "function": {
                    "name": data["function"]["name"],
                    "arguments": data["function"]["arguments"],
                },
            })
        return remaining

    @staticmethod
    def _is_json_complete(s: str) -> bool:
        """Check if *s* is a syntactically complete JSON string."""
        try:
            json.loads(s)
            return True
        except json.JSONDecodeError:
            return False

    def as_openai_tool_calls(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for idx in sorted(self._by_index.keys()):
            d = self._by_index[idx]
            out.append(
                {
                    "id": d["id"],
                    "type": d.get("type") or "function",
                    "function": {
                        "name": d["function"]["name"],
                        "arguments": d["function"]["arguments"],
                    },
                }
            )
        return out

    def is_empty(self) -> bool:
        return not self._by_index


# ── ApiClient ────────────────────────────────────────────────────────────

class ApiClient:
    """OpenAI-compatible API client with SSE streaming.

    封装了与 LLM API 的 HTTP 通信和 SSE 流式解析逻辑，
    包括工具调用流累加、消息净化、重试、以及采样参数自适应。
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self.stop_event: Optional[threading.Event] = None
        self._current_resp = None  # 当前活跃的 HTTP 响应，用于停止时关闭 socket

    # ── 公共接口 ──────────────────────────────────────────────────────

    def stream_completion(
        self,
        messages: list,
        with_tools: bool,
        tool_schemas: list = None,
        on_stream_chunk: StreamChunkCallback = None,
        deferred_ok: bool = False,
        max_tokens: int = None,
        on_tool_call: ToolCallCallback = None,
        stop_event: threading.Event = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Stream a chat completion. Returns (msg_dict, error_string).

        Args:
            messages: 消息列表（将自动通过 sanitize_messages 净化）。
            with_tools: 是否包含工具注册（若为 False 则剥离所有 tool 字段）。
            tool_schemas: 工具 schema 列表（with_tools=True 时必填）。
            on_stream_chunk: 流式回调 (kind, text)。
            deferred_ok: 是否使用延迟加载模式（仅名称，无完整 schema）。
            max_tokens: 输出最大 token 数（默认 16384 无工具 / 8192 有工具）。
            on_tool_call: 可选回调，每当一个 tool call 的参数累积为完整 JSON
                          时立即触发，用于增量（interleaved）执行。
            stop_event: 停止信号。设置后 SSE 流将立即中断。

        Returns:
            (msg_dict, None)  —— 成功。msg_dict 包含 role, content,
                                 tool_calls, reasoning_content。
            (None, err_str)   —— 失败。err_str 为人类可读的错误描述。
        """
        out_tokens = max_tokens or 16384

        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": self.sanitize_messages(messages, with_tools=with_tools),
            "stream": True,
            "max_tokens": out_tokens,
        }
        if not ApiClient._model_rejects_sampling_params(self.config.model):
            payload["temperature"] = self.config.temperature
        if with_tools:
            payload["tools"] = tool_schemas or []
            if deferred_ok and self.config.tool_deferred_loading:
                # 延迟模式：非核心工具 schema 已在外部替换为仅名称
                pass
            payload["tool_choice"] = "auto"

        endpoint = self._chat_endpoint(self.config.base_url)
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        req = request.Request(endpoint, data=body, headers=headers, method="POST")

        # 合并实例级和调用级的 stop_event
        evt = stop_event or self.stop_event

        try:
            resp = request.urlopen(req, timeout=600)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # 400 "insufficient tool messages" —— 清理后重试一次
            if (
                exc.code == 400
                and "insufficient tool messages" in detail
            ):
                sanitized = ApiClient.sanitize_messages(messages, with_tools=with_tools)
                if sanitized != messages:
                    # 递归重试（最多一次）
                    return self._stream_completion_impl(
                        endpoint, sanitized, with_tools, tool_schemas,
                        on_stream_chunk, deferred_ok, out_tokens,
                        on_tool_call=on_tool_call, stop_event=evt,
                    )
            return None, f"[Error] HTTP {exc.code}: {detail}"
        except Exception as exc:
            return None, f"[Error] 请求失败: {exc}"

        self._current_resp = resp
        try:
            return self._parse_sse_stream(resp, on_stream_chunk, on_tool_call=on_tool_call, stop_event=evt)
        finally:
            self._current_resp = None

    def force_stop(self) -> None:
        """从外部线程调用，立即中断当前活跃的 SSE 流。"""
        self.stop_event = self.stop_event or threading.Event()
        self.stop_event.set()
        resp = self._current_resp
        if resp is not None:
            self._close_resp_socket(resp)

    @staticmethod
    def _close_resp_socket(resp) -> None:
        """关闭 HTTP 响应的底层 socket，使 readline() 立即返回。"""
        try:
            sock = resp.fp.raw._sock if hasattr(resp, 'fp') else None
            if sock:
                sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError, TypeError):
            pass

    # ── 消息净化 ──────────────────────────────────────────────────────

    @staticmethod
    def sanitize_messages(
        messages: List[Dict[str, Any]],
        with_tools: bool = True,
    ) -> List[Dict[str, Any]]:
        """确保发送给 API 的消息符合规范。

        - with_tools=True:  检查所有 assistant tool_calls 都有对应的 tool result。
        - with_tools=False: 无条件剥离所有 tool_calls 和 tool role 消息。
        """
        if not messages:
            return messages

        # 非工具模式：剥离所有 tool 相关内容
        if not with_tools:
            result: List[Dict[str, Any]] = []
            for m in messages:
                role = m.get("role", "")
                if role == "tool":
                    continue
                if role == "assistant" and m.get("tool_calls"):
                    cleaned = dict(m)
                    cleaned.pop("tool_calls", None)
                    result.append(cleaned)
                    continue
                result.append(m)
            return result

        # 工具模式：确保 tool_calls 都有匹配的 tool result
        seen_tool_ids: set = set()
        for m in messages:
            if m.get("role") == "tool":
                tid = m.get("tool_call_id", "")
                if tid:
                    seen_tool_ids.add(tid)

        result = []
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                tcs = m.get("tool_calls", [])
                missing = [
                    tc.get("id", "")
                    for tc in tcs
                    if tc.get("id", "") not in seen_tool_ids
                ]
                if missing:
                    stripped = dict(m)
                    stripped.pop("tool_calls", None)
                    matched = [tc for tc in tcs if tc.get("id", "") in seen_tool_ids]
                    if matched:
                        stripped["tool_calls"] = matched
                    result.append(stripped)
                    continue
            result.append(m)

        return result

    # ── 内部 SSE 实现 ────────────────────────────────────────────────

    def _stream_completion_impl(
        self,
        endpoint: str,
        messages: List[Dict[str, Any]],
        with_tools: bool,
        tool_schemas: list,
        on_stream_chunk: StreamChunkCallback,
        deferred_ok: bool,
        out_tokens: int,
        on_tool_call: ToolCallCallback = None,
        stop_event: threading.Event = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """HTTP 通信 + SSE 解析的内部分支，供递归重试时复用。"""
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "max_tokens": out_tokens,
        }
        if not ApiClient._model_rejects_sampling_params(self.config.model):
            payload["temperature"] = self.config.temperature
        if with_tools:
            payload["tools"] = tool_schemas or []
            payload["tool_choice"] = "auto"

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        req = request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            resp = request.urlopen(req, timeout=600)
        except Exception as exc:
            return None, f"[Error] 请求失败: {exc}"

        self._current_resp = resp
        try:
            return self._parse_sse_stream(resp, on_stream_chunk, on_tool_call=on_tool_call, stop_event=stop_event)
        finally:
            self._current_resp = None

    def _parse_sse_stream(
        self,
        resp,
        on_stream_chunk: StreamChunkCallback,
        on_tool_call: ToolCallCallback = None,
        stop_event: threading.Event = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """解析 SSE data: 行，累加 content / reasoning / tool_calls。

        When *on_tool_call* is provided, completed tool calls are dispatched
        as soon as their arguments become valid JSON, enabling interleaved
        tool execution while the stream is still in flight.

        When *stop_event* is set, the HTTP response socket is closed to
        immediately interrupt the blocking ``readline()`` call.
        """
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_acc = _ToolCallStreamAccumulator()
        finish_reason: Optional[str] = None

        try:
            while True:
                # 检查停止信号：关闭 socket 以中断阻塞的 readline
                if stop_event and stop_event.is_set():
                    self._close_resp_socket(resp)
                    return None, "[Stopped] 用户请求停止"

                raw_line = resp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = obj.get("choices") or []
                if not choices:
                    continue
                ch = choices[0]
                if ch.get("finish_reason"):
                    finish_reason = ch["finish_reason"]
                delta = ch.get("delta") or {}
                if delta.get("content") is not None:
                    piece = delta["content"]
                    if piece:
                        content_parts.append(piece)
                        if on_stream_chunk:
                            on_stream_chunk("content", piece)
                if delta.get("reasoning_content") is not None:
                    rp = delta["reasoning_content"]
                    if rp:
                        reasoning_parts.append(rp)
                        if on_stream_chunk:
                            on_stream_chunk("reasoning", rp)
                if delta.get("tool_calls"):
                    tool_acc.add_delta(delta["tool_calls"])
                    if on_tool_call:
                        for tc in tool_acc.pop_completed_calls():
                            on_tool_call(tc)
        finally:
            resp.close()

        # Flush any remaining unreported tool calls (stream ended or
        # finish_reason="tool_calls" arrived without a final delta)
        if on_tool_call:
            for tc in tool_acc.pop_unreported_calls():
                on_tool_call(tc)

        tool_calls = tool_acc.as_openai_tool_calls()
        if tool_calls:
            return (
                {
                    "role": "assistant",
                    "content": "".join(content_parts),
                    "tool_calls": tool_calls,
                    "reasoning_content": "".join(reasoning_parts)
                    if reasoning_parts
                    else None,
                },
                None,
            )

        msg: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        if reasoning_parts:
            msg["reasoning_content"] = "".join(reasoning_parts)

        if not content_parts and not reasoning_parts and finish_reason == "length":
            return None, "[Error] 模型输出被截断 (length)，请增大 max_tokens 或缩短对话。"

        return msg, None

    # ── 静态辅助方法 ──────────────────────────────────────────────────

    @staticmethod
    def _chat_endpoint(base_url: str) -> str:
        text = base_url.strip().rstrip("/")
        if text.endswith("/chat/completions"):
            return text
        if text.endswith("/v1"):
            return text + "/chat/completions"
        return text + "/v1/chat/completions"

    @staticmethod
    def _should_retry_without_tools(error_str: str) -> bool:
        lower = error_str.lower()
        if "http 400" in lower:
            return True
        for key in (
            "tools",
            "tool_choice",
            "tool_calls",
            "function call",
            "not support",
            "temperature",
        ):
            if key in lower:
                return True
        return False

    @staticmethod
    def _model_rejects_sampling_params(model: str) -> bool:
        m = (model or "").lower()
        # 思考/推理模型不接受 temperature/top_p 参数
        return any(kw in m for kw in ("reasoner", "v4-pro", "r1"))

    @staticmethod
    def extract_assistant_text(msg: Dict[str, Any]) -> str:
        raw_content = msg.get("content")
        c = raw_content.strip() if isinstance(raw_content, str) else ""
        return c
