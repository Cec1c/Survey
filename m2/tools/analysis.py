"""Decompilation, disassembly, and cross-reference tools."""

from typing import Dict, Any

from m2.tools._decorators import ida_tool
from m2.tools.meta import _call


@ida_tool(category="analysis")
def decompile_function(address: str) -> Dict[str, Any]:
    """Decompile a function to C pseudocode using Hex-Rays.
    :param address: Hex address within the target function (e.g. "0x401000")
    """
    return _call("decompile_function", {"address": address})


@ida_tool(category="analysis")
def disassemble_function(start_address: str) -> Dict[str, Any]:
    """Disassemble a function to assembly listing. Includes pseudocode when available.
    :param start_address: Start address of the function (e.g. "0x401000")
    """
    return _call("disassemble_function", {"start_address": start_address})


@ida_tool(category="analysis")
def get_function(query: str) -> Dict[str, Any]:
    """Get function details by address or name. Auto-detects hex addresses, sub_xxx patterns, and function names.
    :param query: Hex address (0x401000), sub_xxx name, or function name (main, CheckSerial)
    """
    return _call("get_function", {"query": query})


@ida_tool(category="analysis")
def get_callers(function_address: str) -> Dict[str, Any]:
    """Get all functions that call the given function.
    :param function_address: Hex address of the target function
    """
    return _call("get_callers", {"function_address": function_address})


@ida_tool(category="analysis")
def get_callees(function_address: str) -> Dict[str, Any]:
    """Get all functions called by the given function.
    :param function_address: Hex address of the target function
    """
    return _call("get_callees", {"function_address": function_address})


@ida_tool(category="analysis")
def get_xrefs_to(address: str) -> Dict[str, Any]:
    """Get all cross-references (code + data) pointing to an address.
    :param address: Hex address to find references to
    """
    return _call("get_xrefs_to", {"address": address})
