"""Connection and metadata tools."""

from typing import Dict, Any

from m2.tools._decorators import ida_tool

_client = None


def set_client(client):
    global _client
    _client = client


def _call(method: str, params: dict = None) -> Dict[str, Any]:
    try:
        result = _client.call(method, params or {})
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@ida_tool(category="meta")
def check_connection() -> Dict[str, Any]:
    """Verify the IDA bridge is online and responsive."""
    return _call("check_connection")


@ida_tool(category="meta")
def get_metadata() -> Dict[str, Any]:
    """Get IDB metadata: file path, sha256 hash, image base, 64-bit flag, IDA version."""
    return _call("get_metadata")


@ida_tool(category="meta")
def get_current_address() -> Dict[str, Any]:
    """Get the cursor address currently selected in IDA."""
    return _call("get_current_address")


@ida_tool(category="meta")
def get_current_function() -> Dict[str, Any]:
    """Get the function containing the current cursor address in IDA."""
    return _call("get_current_function")


@ida_tool(category="meta")
def get_entry_points() -> Dict[str, Any]:
    """List all program entry points."""
    return _call("get_entry_points")
