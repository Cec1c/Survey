import json
from typing import Any, Dict

from m2.bridge_protocol import BridgeClient, BridgeClientConfig, BridgeError


class MCPService:
    """Thin service wrapper for direct IDA bridge tool calls."""

    def __init__(self, host: str, port: int, timeout_seconds: float):
        self._config = BridgeClientConfig(host=host, port=port, timeout_seconds=timeout_seconds)
        self._client = BridgeClient(self._config)

    def update(self, host: str, port: int, timeout_seconds: float) -> None:
        self._config = BridgeClientConfig(host=host, port=port, timeout_seconds=timeout_seconds)
        self._client = BridgeClient(self._config)

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = self._client.call(tool_name, arguments)
            return {"ok": True, "result": result}
        except BridgeError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"UNEXPECTED: {exc}"}

    @staticmethod
    def pretty_json(data: Dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2)
