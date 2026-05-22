"""Execute arbitrary IDAPython code in IDA. Use when no existing tool covers the operation."""

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


@ida_tool(category="python", concurrency_safe=False, unsafe=True)
def execute_python(code: str) -> Dict[str, Any]:
    """Execute IDAPython code in IDA. Use when no existing tool covers the operation.
    WARNING: This executes arbitrary Python code inside IDA. Only use when standard tools are insufficient.
    :param code: IDAPython code to execute. Example: "import idautils; [hex(ea) for ea in idautils.Functions()]"
    """
    return _call("execute_python", {"code": code})
