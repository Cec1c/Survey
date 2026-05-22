"""Context manager for tool result filtering, truncation, and compact feedback.

Extracted from ``AgentChatService`` to provide a standalone class that manages
the size of tool results sent back to the LLM.  Prevents context-window
bloat while preserving salient evidence for model reasoning.
"""

import json
from typing import Any, Dict, Set


class ContextManager:
    """Manages tool result size: filtering, truncation, and compact feedback.

    Usage::

        ctx = ContextManager()
        result = {"ok": True, "result": {"pseudocode": "..." * 5000, "address": 0x401000}}
        filtered = ctx.filter_result("decompile_function", result)
        feedback = ctx.compact_feedback("decompile_function", filtered)
    """

    # Keys considered important enough to keep when filtering a tool result
    # dict.  Everything else is dropped.
    IMPORTANT_KEYS: Set[str] = {
        "address",
        "addr",
        "start_address",
        "end_address",
        "image_base",
        "input_path",
        "input_name",
        "path",
        "filename",
        "file",
        "name",
        "size",
        "offset",
        "total",
        "items",
        "connected",
        "ida_version",
        "is_64bit",
        "sha256",
        "pseudocode",
        "lines",
        "line",
        "xrefs",
        "xref",
        "callers",
        "callees",
        "registers",
        "regs",
        "thread_id",
        "tid",
        "rip",
        "eip",
        "rax",
        "rbx",
        "rcx",
        "rdx",
        "rsp",
        "rbp",
        "data",
        "value",
        "symbol",
        "module",
        "error",
        "ok",
        "filter",
    }

    # per-key truncation limits ---------------------------------------------------

    _KEY_LIMITS: Dict[str, int] = {
        "pseudocode": 64_000,
        "decompilation": 64_000,
        "disassembly": 64_000,
        "assembly": 64_000,
        "lines": 32_000,
        "text": 32_000,
        "body": 32_000,
    }

    _DEFAULT_TRUNCATE_LIMIT: int = 1200

    def __init__(self, important_keys: Set[str] = None) -> None:
        """Create a ``ContextManager`` with an optional custom important-keys set.

        When *important_keys* is ``None`` the built-in ``IMPORTANT_KEYS``
        constant is used.
        """
        self.important_keys = important_keys if important_keys is not None else self.IMPORTANT_KEYS

    # -- public API -----------------------------------------------------------

    def truncate_text(self, text: str, limit: int = 1200) -> str:
        """Truncate *text* at *limit* chars, appending a truncated-count note."""
        if len(text) <= limit:
            return text
        return text[:limit] + f"... [truncated {len(text) - limit} chars]"

    def string_truncate_limit_for_key(self, key: str) -> int:
        """Return the per-key truncation limit.

        Code bodies (pseudocode, disassembly) get much larger limits so the
        model retains enough evidence for reasoning.
        """
        lk = (key or "").lower()
        return self._KEY_LIMITS.get(lk, self._DEFAULT_TRUNCATE_LIMIT)

    def filter_result(self, tool_name: str, tool_result: Any) -> Any:
        """Keep only salient debugging facts from a tool result.

        * If *tool_result* is not a dict it is returned unchanged.
        * If the call failed (``ok`` is falsy) only the error is kept.
        * Otherwise ``extract_key_facts`` is applied to the result payload.
        """
        if not isinstance(tool_result, dict):
            return tool_result

        ok = bool(tool_result.get("ok", False))
        if not ok:
            return {"ok": False, "error": str(tool_result.get("error", "tool call failed"))}

        payload = tool_result.get("result")
        if payload is None:
            return {"ok": True, "result": None}

        return {"ok": True, "result": self.extract_key_facts(payload, 0, "")}

    def extract_key_facts(self, value: Any, depth: int = 0, key: str = "") -> Any:
        """Recursively extract important fields from a tool-result value.

        * Strings are truncated per-key.
        * Lists are truncated to 256 elements.
        * Dicts are filtered to *important_keys* (plus fuzzy matches for
          ``addr``, ``reg``, ``ref``, ``*name``, ``*path``).  Empty results
          fall back to a compact projection of the first 10 keys.
        * Max recursion depth is 4.
        """
        if depth > 4:
            return "...(max depth)"

        if isinstance(value, str):
            lim = self.string_truncate_limit_for_key(key)
            return self.truncate_text(value, lim)

        if isinstance(value, (int, float, bool)) or value is None:
            return value

        if isinstance(value, list):
            truncated = value[:256]
            return [self.extract_key_facts(v, depth + 1, key) for v in truncated]

        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for k, v in value.items():
                lk = str(k).lower()
                if (
                    lk in self.important_keys
                    or "addr" in lk
                    or "reg" in lk
                    or "ref" in lk
                    or lk.endswith("name")
                    or lk.endswith("path")
                ):
                    out[k] = self.extract_key_facts(v, depth + 1, str(k))
            if not out:
                # Nothing matched -- keep a compact projection.
                for k, v in list(value.items())[:10]:
                    out[k] = self.extract_key_facts(v, depth + 1, str(k))
            return out

        return str(value)

    def compact_feedback(self, tool_name: str, tool_result: Any) -> str:
        """Serialize *tool_result* to a short JSON string for the model.

        Tool content returned to the model must stay compact or later rounds
        quickly exceed the context window.  Results longer than 4000 chars
        are truncated.
        """
        try:
            text = json.dumps(tool_result, ensure_ascii=False)
        except Exception:
            text = str(tool_result)

        limit = 4000
        if len(text) > limit:
            text = text[:limit] + f"... [truncated {len(text) - limit} chars]"
        return text
