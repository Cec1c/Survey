"""Tool manifest — single source for tool metadata in the service layer.

Reads tool definitions from m2.tools._decorators.TOOL_REGISTRY (the authoritative
source created by @ida_tool). Provides schema generation, concurrency safety
queries, deferred loading, and extension group filtering.
"""

from typing import Any, Dict, List, Optional, Set

from m2.tools._decorators import (
    TOOL_REGISTRY, EXT_GROUPS, ToolMeta,
    get_default_tools, get_ext_tools,
)


class ToolManifest:
    """Query tool metadata from the authoritative @ida_tool registry."""

    DEFERRED_TOOLS: Set[str] = {
        name for name, meta in TOOL_REGISTRY.items() if meta.deferred
    }

    def __init__(self, enable_ext_groups: Optional[Set[str]] = None):
        """
        Args:
            enable_ext_groups: Set of extension groups to enable (e.g. {"dbg"}).
                               Tools in enabled groups become visible.
        """
        self._enabled_ext = enable_ext_groups or set()
        self._conditional: Dict[str, ToolMeta] = {}  # llm4decompile, upx_unpack

    @property
    def active_tools(self) -> Dict[str, ToolMeta]:
        """All currently active tools (default + enabled ext groups + conditional)."""
        tools = dict(get_default_tools())
        for group in self._enabled_ext:
            tools.update(get_ext_tools(group))
        tools.update(self._conditional)
        return tools

    def get_schemas(self, deferred_ok: bool = False) -> List[Dict[str, Any]]:
        """Return OpenAI tool schemas for all active tools.

        When deferred_ok is True, deferred tools only include name (no full schema),
        reducing prompt token count in early rounds.
        """
        schemas = []
        for name, meta in self.active_tools.items():
            if meta.deferred and deferred_ok:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": meta.description,
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    },
                })
            else:
                schemas.append(meta.to_openai_schema())
        return schemas

    def resolve_deferred(self, name: str) -> Optional[Dict[str, Any]]:
        meta = self.active_tools.get(name)
        if meta is None:
            return None
        return meta.to_openai_schema()

    def is_concurrency_safe(self, name: str) -> bool:
        meta = self.active_tools.get(name)
        return meta.concurrency_safe if meta else True

    def is_unsafe(self, name: str) -> bool:
        meta = self.active_tools.get(name)
        return meta.unsafe if meta else False

    def get_category(self, name: str) -> str:
        meta = self.active_tools.get(name)
        return meta.category if meta else "default"

    def get_names(self) -> List[str]:
        return list(self.active_tools.keys())

    def register_conditional(self, meta: ToolMeta) -> None:
        self._conditional[meta.name] = meta

    def unregister_conditional(self, name: str) -> None:
        self._conditional.pop(name, None)

    def enable_ext_group(self, group: str) -> None:
        self._enabled_ext.add(group)

    def disable_ext_group(self, group: str) -> None:
        self._enabled_ext.discard(group)

    def __len__(self) -> int:
        return len(self.active_tools)
