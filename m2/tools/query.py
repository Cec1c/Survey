"""List query tools. filter parameter is optional; omit to get all items."""

from typing import Dict, Any, Optional

from m2.tools._decorators import ida_tool
from m2.tools.meta import _call


@ida_tool(category="query")
def list_functions(offset: int = 0, count: int = 200, filter: str = None) -> Dict[str, Any]:
    """List functions with optional name filter. Omit filter to get all functions.
    :param offset: Starting index (0-based)
    :param count: Maximum number of functions to return
    :param filter: Optional substring to match in function names (case-insensitive)
    """
    method = "list_functions_filter" if filter else "list_functions"
    params = {"offset": offset, "count": count}
    if filter:
        params["filter"] = filter
    return _call(method, params)


@ida_tool(category="query")
def list_globals(offset: int = 0, count: int = 200, filter: str = None) -> Dict[str, Any]:
    """List global variables with optional name filter. Omit filter to get all.
    :param offset: Starting index (0-based)
    :param count: Maximum number of results
    :param filter: Optional substring to match (case-insensitive)
    """
    method = "list_globals_filter" if filter else "list_globals"
    params = {"offset": offset, "count": count}
    if filter:
        params["filter"] = filter
    return _call(method, params)


@ida_tool(category="query")
def list_strings(offset: int = 0, count: int = 200, filter: str = None) -> Dict[str, Any]:
    """List strings with optional keyword filter. Omit filter to get all strings.
    :param offset: Starting index (0-based)
    :param count: Maximum number of results
    :param filter: Optional keyword to search for in string content
    """
    method = "list_strings_filter" if filter else "list_strings"
    params = {"offset": offset, "count": count}
    if filter:
        params["filter"] = filter
    return _call(method, params)


@ida_tool(category="query")
def list_imports(offset: int = 0, count: int = 200) -> Dict[str, Any]:
    """List imported functions and their modules.
    :param offset: Starting index (0-based)
    :param count: Maximum number of imports to return
    """
    return _call("list_imports", {"offset": offset, "count": count})


@ida_tool(category="query")
def get_global_value(query: str) -> Dict[str, Any]:
    """Read a global variable by address or name. Auto-detects hex address vs name.
    :param query: Hex address or exact name of the global variable
    """
    stripped = query.strip()
    if stripped.lower().startswith("0x"):
        return _call("get_global_variable_value_at_address", {"address": stripped})
    try:
        int(stripped, 16)
        return _call("get_global_variable_value_at_address", {"address": stripped})
    except ValueError:
        return _call("get_global_variable_value_by_name", {"variable_name": stripped})
