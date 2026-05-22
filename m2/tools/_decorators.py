"""@ida_tool decorator — single source of truth for tool definition.

Each decorated function auto-registers into TOOL_REGISTRY with full metadata:
name, description, parameters (from type hints), category, concurrency_safe,
deferred, unsafe, and ext group.
"""

import inspect
from typing import Any, Callable, Dict, get_type_hints

# ── Type hint → JSON Schema mapping ──────────────────────────────────────
_TYPE_MAP = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "dict": {"type": "object"},
    "list": {"type": "array"},
    "NoneType": {"type": "null"},
}


def _type_to_schema(py_type) -> dict:
    """Convert a Python type annotation to a JSON Schema fragment."""
    origin = getattr(py_type, "__origin__", None)
    if origin is not None:
        return {"type": "string"}  # fallback for complex types
    name = getattr(py_type, "__name__", str(py_type))
    return _TYPE_MAP.get(name, {"type": "string"})


def _build_parameters_schema(fn: Callable) -> dict:
    """Extract JSON Schema from function signature + docstring args."""
    hints = {}
    try:
        hints = get_type_hints(fn)
    except Exception:
        pass

    sig = inspect.signature(fn)
    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        py_type = hints.get(param_name, str)
        schema = _type_to_schema(py_type)

        # Extract description from docstring :param lines
        doc = (fn.__doc__ or "")
        for line in doc.split("\n"):
            line = line.strip()
            if line.startswith(f":param {param_name}:"):
                schema["description"] = line[len(f":param {param_name}:"):].strip()
                break

        if not schema.get("description"):
            schema["description"] = f"{param_name} parameter"

        properties[param_name] = schema
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


# ── Global registry ──────────────────────────────────────────────────────

class ToolMeta:
    """Metadata for a single IDA tool."""
    __slots__ = (
        "name", "fn", "description", "parameters", "category",
        "concurrency_safe", "deferred", "unsafe", "ext_group",
    )

    def __init__(
        self,
        name: str,
        fn: Callable,
        description: str,
        parameters: dict,
        category: str = "default",
        concurrency_safe: bool = True,
        deferred: bool = False,
        unsafe: bool = False,
        ext_group: str = "",
    ):
        self.name = name
        self.fn = fn
        self.description = description
        self.parameters = parameters
        self.category = category
        self.concurrency_safe = concurrency_safe
        self.deferred = deferred
        self.unsafe = unsafe
        self.ext_group = ext_group

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_mcp_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }


TOOL_REGISTRY: Dict[str, ToolMeta] = {}
EXT_GROUPS: Dict[str, set] = {}  # group → set of tool names


def ida_tool(
    category: str = "default",
    concurrency_safe: bool = True,
    deferred: bool = False,
    unsafe: bool = False,
):
    """Register a function as an IDA MCP tool with full metadata.

    Args:
        category: "analysis"|"memory"|"modify"|"query"|"struct"|"stack"|"meta"|"debug"|"python"
        concurrency_safe: False for write tools needing exclusive lock
        deferred: True for rarely-used tools (sent to LLM after round 3)
        unsafe: True to show a warning in the UI
    """

    def decorator(fn: Callable) -> Callable:
        name = fn.__name__
        desc = fn.__doc__ or ""
        # Extract first paragraph as description
        desc_line = desc.strip().split("\n")[0].strip()
        params = _build_parameters_schema(fn)

        meta = ToolMeta(
            name=name,
            fn=fn,
            description=desc_line,
            parameters=params,
            category=category,
            concurrency_safe=concurrency_safe,
            deferred=deferred,
            unsafe=unsafe,
        )
        TOOL_REGISTRY[name] = meta
        return fn

    return decorator


def ext(group: str):
    """Mark a tool as belonging to an extension group (hidden by default).

    Extension tools are only visible when explicitly enabled.
    Example: @ext("dbg") marks debugger tools that need ?ext=dbg.
    """

    def decorator(fn: Callable) -> Callable:
        if group not in EXT_GROUPS:
            EXT_GROUPS[group] = set()
        EXT_GROUPS[group].add(fn.__name__)
        return fn

    return decorator


def get_default_tools() -> Dict[str, ToolMeta]:
    """Return tools NOT in any extension group (visible by default)."""
    all_ext = set()
    for names in EXT_GROUPS.values():
        all_ext.update(names)
    return {k: v for k, v in TOOL_REGISTRY.items() if k not in all_ext}


def get_ext_tools(group: str) -> Dict[str, ToolMeta]:
    """Return tools in a specific extension group."""
    names = EXT_GROUPS.get(group, set())
    return {k: v for k, v in TOOL_REGISTRY.items() if k in names}
