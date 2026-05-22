"""M2 IDA Tools — modular MCP tool definitions.

Each module defines tools for a specific domain. Tools are registered via @ida_tool
decorator and collected in TOOL_REGISTRY.
"""

from m2.tools._decorators import (
    TOOL_REGISTRY, EXT_GROUPS, ToolMeta,
    ida_tool, ext, get_default_tools, get_ext_tools,
)

from m2.tools import meta as meta_tools
from m2.tools import analysis as analysis_tools
from m2.tools import memory as memory_tools
from m2.tools import query as query_tools
from m2.tools import structures as structure_tools
from m2.tools import modify as modify_tools
from m2.tools import stack as stack_tools
from m2.tools import python as python_tools
from m2.tools import debug as debug_tools


def set_client(client):
    """Inject BridgeClient into all tool modules that need it."""
    for mod in (
        meta_tools, analysis_tools, memory_tools, query_tools,
        structure_tools, modify_tools, stack_tools, python_tools, debug_tools,
    ):
        if hasattr(mod, "set_client"):
            mod.set_client(client)
