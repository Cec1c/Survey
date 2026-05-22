"""Structure and type definition tools."""

from typing import Dict, Any

from m2.tools._decorators import ida_tool
from m2.tools.meta import _call


@ida_tool(category="struct")
def get_defined_structures() -> Dict[str, Any]:
    """List all defined structures in the IDB."""
    return _call("get_defined_structures")


@ida_tool(category="struct")
def search_structures(filter: str) -> Dict[str, Any]:
    """Search structures by name substring.
    :param filter: Substring to match in structure names
    """
    return _call("search_structures", {"filter": filter})


@ida_tool(category="struct")
def get_struct_info(name: str) -> Dict[str, Any]:
    """Get structure info: size, member names, offsets, and types.
    :param name: Exact structure name
    """
    return _call("get_struct_info_simple", {"name": name})


@ida_tool(category="struct")
def get_struct_at_address(address: str, struct_name: str) -> Dict[str, Any]:
    """Read structure fields from a specific memory address.
    :param address: Hex address where the structure instance lives
    :param struct_name: Name of the structure type
    """
    return _call("get_struct_at_address", {"address": address, "struct_name": struct_name})


@ida_tool(category="struct", deferred=True)
def list_local_types() -> Dict[str, Any]:
    """List all local type definitions in the IDB."""
    return _call("list_local_types")


@ida_tool(category="struct", concurrency_safe=False, deferred=True)
def declare_c_type(c_declaration: str) -> Dict[str, Any]:
    """Declare a C type from a declaration string.
    :param c_declaration: C type declaration (e.g. "struct Foo { int x; char* name; };")
    """
    return _call("declare_c_type", {"c_declaration": c_declaration})
