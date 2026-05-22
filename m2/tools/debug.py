"""Dynamic debugging tools. Hidden by default, enable with ext=dbg."""

from typing import Dict, Any

from m2.tools._decorators import ida_tool, ext
from m2.tools.meta import _call


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False, unsafe=True)
def debug_start() -> Dict[str, Any]:
    """Start the debugger on the current IDB file."""
    return _call("debug_start")


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False)
def debug_exit() -> Dict[str, Any]:
    """Stop the debugger and exit the debugging session."""
    return _call("debug_exit")


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False)
def debug_continue() -> Dict[str, Any]:
    """Continue execution until next breakpoint or termination."""
    return _call("debug_continue")


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False)
def debug_step_into() -> Dict[str, Any]:
    """Step into the next instruction (follow function calls)."""
    return _call("debug_step_into")


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False)
def debug_step_over() -> Dict[str, Any]:
    """Step over the next instruction (skip function calls)."""
    return _call("debug_step_over")


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False)
def debug_run_to(address: str) -> Dict[str, Any]:
    """Run until the specified address is reached.
    :param address: Hex address to run to (e.g. "0x401000")
    """
    return _call("debug_run_to", {"address": address})


@ext("dbg")
@ida_tool(category="debug")
def debug_list_breakpoints() -> Dict[str, Any]:
    """List all current breakpoints."""
    return _call("debug_list_breakpoints")


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False)
def debug_add_breakpoint(address: str) -> Dict[str, Any]:
    """Add a breakpoint at the specified address.
    :param address: Hex address for breakpoint (e.g. "0x401000")
    """
    return _call("debug_add_breakpoint", {"address": address})


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False)
def debug_delete_breakpoint(address: str) -> Dict[str, Any]:
    """Delete the breakpoint at the specified address.
    :param address: Hex address of breakpoint to remove
    """
    return _call("debug_delete_breakpoint", {"address": address})


@ext("dbg")
@ida_tool(category="debug")
def debug_get_registers() -> Dict[str, Any]:
    """Get current register values for the suspended thread."""
    return _call("debug_get_registers")


@ext("dbg")
@ida_tool(category="debug")
def debug_get_stacktrace() -> Dict[str, Any]:
    """Get the current call stack (stack trace) of the debugged process."""
    return _call("debug_get_stacktrace")


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False)
def debug_read_memory(address: str, size: int) -> Dict[str, Any]:
    """Read memory from the debugged process at runtime.
    :param address: Hex address to read from
    :param size: Number of bytes to read
    """
    return _call("debug_read_memory", {"address": address, "size": size})


@ext("dbg")
@ida_tool(category="debug", concurrency_safe=False, unsafe=True)
def debug_write_memory(address: str, data: str) -> Dict[str, Any]:
    """Write data to the debugged process memory at runtime. Use with caution.
    :param address: Hex address to write to
    :param data: Hex data to write (space-separated hex bytes, e.g. "90 90")
    """
    return _call("debug_write_memory", {"address": address, "data": data})
