"""Modification tools: rename, set type, comment, patch."""

from typing import Dict, Any

from m2.tools._decorators import ida_tool
from m2.tools.meta import _call


@ida_tool(category="modify", concurrency_safe=False)
def rename_function(function_address: str, new_name: str) -> Dict[str, Any]:
    """Rename a function at the given address.
    :param function_address: Hex address of the function
    :param new_name: New function name (must be a valid C identifier)
    """
    return _call("rename_function", {"function_address": function_address, "new_name": new_name})


@ida_tool(category="modify", concurrency_safe=False, deferred=True)
def rename_global_variable(old_name: str, new_name: str) -> Dict[str, Any]:
    """Rename a global variable.
    :param old_name: Current name of the variable
    :param new_name: New name to assign
    """
    return _call("rename_global_variable", {"old_name": old_name, "new_name": new_name})


@ida_tool(category="modify", concurrency_safe=False, deferred=True)
def rename_local_variable(function_address: str, old_name: str, new_name: str) -> Dict[str, Any]:
    """Rename a local variable in a function.
    :param function_address: Hex address of the function
    :param old_name: Current variable name
    :param new_name: New name to assign
    """
    return _call("rename_local_variable", {
        "function_address": function_address, "old_name": old_name, "new_name": new_name,
    })


@ida_tool(category="modify", concurrency_safe=False)
def set_comment(address: str, comment: str) -> Dict[str, Any]:
    """Set a comment at an address (both disassembly and decompiler views).
    :param address: Hex address to annotate
    :param comment: Comment text to add
    """
    return _call("set_comment", {"address": address, "comment": comment})


@ida_tool(category="modify", concurrency_safe=False, deferred=True)
def set_function_prototype(function_address: str, prototype: str) -> Dict[str, Any]:
    """Set a function's C prototype/type signature.
    :param function_address: Hex address of the function
    :param prototype: C function prototype (e.g. "int __cdecl main(int argc, char** argv)")
    """
    return _call("set_function_prototype", {"function_address": function_address, "prototype": prototype})


@ida_tool(category="modify", concurrency_safe=False, deferred=True)
def set_global_variable_type(variable_name: str, new_type: str) -> Dict[str, Any]:
    """Set the C type of a global variable.
    :param variable_name: Name of the variable
    :param new_type: C type string (e.g. "int", "DWORD", "char*")
    """
    return _call("set_global_variable_type", {"variable_name": variable_name, "new_type": new_type})


@ida_tool(category="modify", concurrency_safe=False, deferred=True)
def set_local_variable_type(function_address: str, variable_name: str, new_type: str) -> Dict[str, Any]:
    """Set the C type of a local variable in a function.
    :param function_address: Hex address of the function
    :param variable_name: Current name of the local variable
    :param new_type: C type string to assign
    """
    return _call("set_local_variable_type", {
        "function_address": function_address, "variable_name": variable_name, "new_type": new_type,
    })


@ida_tool(category="modify", concurrency_safe=False, deferred=True)
def patch_asm(address: str, instructions: str) -> Dict[str, Any]:
    """Patch assembly instructions at an address.
    :param address: Hex address to patch
    :param instructions: Assembly instruction(s), separated by newlines or semicolons
    """
    return _call("patch_address_assembles", {"address": address, "instructions": instructions})
