"""Memory reading tools. read_integer replaces data_read_byte/word/dword/qword."""

from typing import Dict, Any

from m2.tools._decorators import ida_tool
from m2.tools.meta import _call


@ida_tool(category="memory")
def read_integer(address: str, size: int = 4) -> Dict[str, Any]:
    """Read an integer at an address. size=1 for byte, 2 for word(16bit), 4 for dword(32bit), 8 for qword(64bit).
    :param address: Hex address to read from
    :param size: Integer size in bytes: 1, 2, 4, or 8
    """
    method_map = {1: "data_read_byte", 2: "data_read_word", 4: "data_read_dword", 8: "data_read_qword"}
    method = method_map.get(size)
    if not method:
        return {"ok": False, "error": f"Invalid size {size}. Use 1, 2, 4, or 8."}
    return _call(method, {"address": address})


@ida_tool(category="memory")
def read_string(address: str) -> Dict[str, Any]:
    """Read a null-terminated string at an address.
    :param address: Hex address of the string start
    """
    return _call("data_read_string", {"address": address})


@ida_tool(category="memory")
def read_bytes(address: str, size: int) -> Dict[str, Any]:
    """Read raw bytes from memory. Use this to inspect data at any address.
    :param address: Hex address to start reading from
    :param size: Number of bytes to read (max 4096)
    """
    return _call("read_memory_bytes", {"memory_address": address, "size": size})
