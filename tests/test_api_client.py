"""API 客户端纯函数测试 — sanitize_messages, _ToolCallStreamAccumulator, 端点构造, 模型参数检测"""

import json
import pytest
from app.gui.services.api_client import ApiClient, _ToolCallStreamAccumulator


# ═══════════════════════════════════════════════════════════════════
# sanitize_messages
# ═══════════════════════════════════════════════════════════════════

class TestSanitizeMessages:
    def test_empty_messages(self):
        assert ApiClient.sanitize_messages([]) == []
        assert ApiClient.sanitize_messages([], with_tools=False) == []

    def test_no_tools_mode_strips_all(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "t1", "content": "result"},
        ]
        result = ApiClient.sanitize_messages(msgs, with_tools=False)
        # tool role removed; assistant tool_calls stripped; user kept
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert "tool_calls" not in result[1]

    def test_no_tools_preserves_reasoning(self):
        msgs = [
            {"role": "assistant", "content": "x", "reasoning_content": "thinking...",
             "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        ]
        result = ApiClient.sanitize_messages(msgs, with_tools=False)
        assert result[0]["reasoning_content"] == "thinking..."
        assert "tool_calls" not in result[0]

    def test_with_tools_paired_passes_through(self):
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
            {"role": "assistant", "content": "answer"},
        ]
        result = ApiClient.sanitize_messages(msgs)
        assert len(result) == 4
        assert result[1]["tool_calls"][0]["id"] == "call_1"

    def test_with_tools_orphan_tool_calls_stripped(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "orphan_1", "type": "function", "function": {"name": "nope", "arguments": "{}"}}
            ]},
        ]
        result = ApiClient.sanitize_messages(msgs)
        # orphan tool_call fully stripped → assistant message has no tool_calls
        assert len(result) == 1
        assert "tool_calls" not in result[0]

    def test_with_tools_mixed_paired_and_orphan(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "paired", "type": "function", "function": {"name": "ok", "arguments": "{}"}},
                {"id": "orphan", "type": "function", "function": {"name": "bad", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "paired", "content": "result"},
        ]
        result = ApiClient.sanitize_messages(msgs)
        # only paired kept; orphan removed
        assert len(result) == 2
        tcs = result[0].get("tool_calls", [])
        assert len(tcs) == 1
        assert tcs[0]["id"] == "paired"


# ═══════════════════════════════════════════════════════════════════
# _ToolCallStreamAccumulator
# ═══════════════════════════════════════════════════════════════════

class TestToolCallStreamAccumulator:
    def test_single_tool_call_accretion(self):
        acc = _ToolCallStreamAccumulator()
        acc.add_delta([{"index": 0, "id": "call_1", "function": {"name": "decompile"}}])
        acc.add_delta([{"index": 0, "function": {"arguments": '{"addr'}}])
        acc.add_delta([{"index": 0, "function": {"arguments": '": "0x401000"}'}}])

        calls = acc.as_openai_tool_calls()
        assert len(calls) == 1
        assert calls[0]["id"] == "call_1"
        assert calls[0]["function"]["name"] == "decompile"
        # arguments should be valid JSON
        args = json.loads(calls[0]["function"]["arguments"])
        assert args["addr"] == "0x401000"

    def test_multi_tool_parallel_accretion(self):
        acc = _ToolCallStreamAccumulator()
        acc.add_delta([
            {"index": 0, "id": "t1", "function": {"name": "f1", "arguments": '{"a":1}'}},
            {"index": 1, "id": "t2", "function": {"name": "f2", "arguments": '{"b":2}'}},
        ])
        calls = acc.as_openai_tool_calls()
        assert len(calls) == 2
        assert {c["function"]["name"] for c in calls} == {"f1", "f2"}

    def test_pop_completed_returns_valid_json_early(self):
        acc = _ToolCallStreamAccumulator()
        acc.add_delta([{"index": 0, "id": "t1", "function": {"name": "read", "arguments": '{"addr":"0x1000"}'}}])

        completed = acc.pop_completed_calls()
        assert len(completed) == 1
        assert completed[0]["id"] == "t1"

    def test_pop_completed_skips_partial_json(self):
        acc = _ToolCallStreamAccumulator()
        acc.add_delta([{"index": 0, "id": "t1", "function": {"name": "read", "arguments": '{"addr":"0x'}}])

        completed = acc.pop_completed_calls()
        assert len(completed) == 0  # not complete yet

    def test_pop_completed_only_reports_once(self):
        acc = _ToolCallStreamAccumulator()
        acc.add_delta([{"index": 0, "id": "t1", "function": {"name": "f", "arguments": "{}"}}])
        assert len(acc.pop_completed_calls()) == 1
        assert len(acc.pop_completed_calls()) == 0  # second call: already reported

    def test_pop_unreported_returns_remainder(self):
        acc = _ToolCallStreamAccumulator()
        acc.add_delta([
            {"index": 0, "function": {"name": "f1", "arguments": "{}"}},
            {"index": 1, "function": {"name": "f2", "arguments": '{"x":'}},  # partial
        ])
        # pop completed (index 0)
        completed = acc.pop_completed_calls()
        assert len(completed) == 1

        # pop unreported (index 1, still partial)
        remaining = acc.pop_unreported_calls()
        assert len(remaining) == 1
        assert remaining[0]["function"]["name"] == "f2"

    def test_as_openai_returns_sorted_by_index(self):
        acc = _ToolCallStreamAccumulator()
        acc.add_delta([
            {"index": 2, "id": "t3", "function": {"name": "f3", "arguments": "{}"}},
            {"index": 0, "id": "t1", "function": {"name": "f1", "arguments": "{}"}},
            {"index": 1, "id": "t2", "function": {"name": "f2", "arguments": "{}"}},
        ])
        calls = acc.as_openai_tool_calls()
        assert [c["id"] for c in calls] == ["t1", "t2", "t3"]


# ═══════════════════════════════════════════════════════════════════
# _model_rejects_sampling_params
# ═══════════════════════════════════════════════════════════════════

class TestModelRejectsSampling:
    @pytest.mark.parametrize("model,expected", [
        ("deepseek-reasoner", True),
        ("deepseek-v4-pro", True),
        ("deepseek-r1", True),
        ("DEEPSEEK-V4-PRO", True),  # case insensitive
        ("deepseek-v4-flash", False),
        ("deepseek-chat", False),
        ("gpt-4o", False),
        ("gpt-4o-mini", False),
        ("claude-3-5-sonnet", False),
    ])
    def test_model_sampling_params(self, model, expected):
        assert ApiClient._model_rejects_sampling_params(model) == expected


# ═══════════════════════════════════════════════════════════════════
# 端点构造 (task_page 静态方法)
# ═══════════════════════════════════════════════════════════════════

from app.gui.pages.task_page import TaskPage


class TestEndpointConstruction:
    @pytest.mark.parametrize("base_url,expected", [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1/chat/completions", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
        ("http://localhost:8080/v1", "http://localhost:8080/v1/chat/completions"),
    ])
    def test_chat_endpoint(self, base_url, expected):
        assert TaskPage._get_chat_endpoint(base_url) == expected

    @pytest.mark.parametrize("base_url,expected", [
        ("https://api.deepseek.com", "https://api.deepseek.com/v1/models"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/models"),
        ("https://api.deepseek.com/v1/chat/completions", "https://api.deepseek.com/v1/models"),
        ("https://api.openai.com/v1", "https://api.openai.com/v1/models"),
    ])
    def test_models_endpoint(self, base_url, expected):
        assert TaskPage._get_models_endpoint(base_url) == expected
