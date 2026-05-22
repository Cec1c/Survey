"""Stack frame variable tools."""

from typing import Dict, Any

from m2.tools._decorators import ida_tool
from m2.tools.meta import _call


@ida_tool(category="stack")
def get_stack_frame_variables(function_address: str) -> Dict[str, Any]:
    """Get all stack frame variables for a function.
    :param function_address: Hex address of the function
    """
    return _call("get_stack_frame_variables", {"function_address": function_address})


@ida_tool(category="stack", concurrency_safe=False, deferred=True)
def create_stack_frame_variable(
    function_address: str, offset: str, variable_name: str, type_name: str
) -> Dict[str, Any]:
    """Create a new stack variable in a function's frame.
    :param function_address: Hex address of the function
    :param offset: Stack offset (e.g. "-0x8" for local var, "0x8" for arg)
    :param variable_name: Name for the new variable
    :param type_name: C type string
    """
    return _call("create_stack_frame_variable", {
        "function_address": function_address, "offset": offset,
        "variable_name": variable_name, "type_name": type_name,
    })


@ida_tool(category="stack", concurrency_safe=False, deferred=True)
def rename_stack_frame_variable(
    function_address: str, old_name: str, new_name: str
) -> Dict[str, Any]:
    """Rename a stack frame variable.
    :param function_address: Hex address of the function
    :param old_name: Current variable name
    :param new_name: New name to assign
    """
    return _call("rename_stack_frame_variable", {
        "function_address": function_address, "old_name": old_name, "new_name": new_name,
    })


@ida_tool(category="stack", concurrency_safe=False, deferred=True)
def set_stack_frame_variable_type(
    function_address: str, variable_name: str, type_name: str
) -> Dict[str, Any]:
    """Set the C type of a stack frame variable.
    :param function_address: Hex address of the function
    :param variable_name: Name of the stack variable
    :param type_name: C type string
    """
    return _call("set_stack_frame_variable_type", {
        "function_address": function_address, "variable_name": variable_name, "type_name": type_name,
    })


@ida_tool(category="stack", concurrency_safe=False, deferred=True)
def delete_stack_frame_variable(function_address: str, variable_name: str) -> Dict[str, Any]:
    """Delete a stack frame variable.
    :param function_address: Hex address of the function
    :param variable_name: Name of the variable to remove
    """
    return _call("delete_stack_frame_variable", {
        "function_address": function_address, "variable_name": variable_name,
    })
