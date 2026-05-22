import json
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional


class BridgeError(RuntimeError):
    """Raised when the IDA bridge returns an error."""


@dataclass
class BridgeClientConfig:
    host: str = "127.0.0.1"
    port: int = 31337
    timeout_seconds: float = 20.0
    retries: int = 1
    retry_delay_seconds: float = 0.25


class BridgeClient:
    """Tiny JSON-RPC-over-TCP client used by the FastMCP server."""

    def __init__(self, config: Optional[BridgeClientConfig] = None):
        self.config = config or BridgeClientConfig()

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        req_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        raw_response = self._send(payload, method)
        try:
            response = json.loads(raw_response)
        except Exception as exc:
            raise BridgeError(f"INVALID_JSON_RESPONSE({method}/{req_id}): {exc}")

        if "error" in response and response["error"]:
            err = response["error"]
            raise BridgeError(
                f"{err.get('code', 'UNKNOWN')}({method}/{req_id}): {err.get('message', 'unknown error')}"
            )
        return response.get("result")

    def _send(self, payload: Dict[str, Any], method: str) -> str:
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        attempts = max(0, int(self.config.retries)) + 1
        last_err: Optional[Exception] = None
        for idx in range(attempts):
            try:
                t0 = time.monotonic()
                with socket.create_connection(
                    (self.config.host, self.config.port), timeout=self.config.timeout_seconds
                ) as sock:
                    sock.sendall(body)
                    sock.shutdown(socket.SHUT_WR)
                    chunks = []
                    while True:
                        buf = sock.recv(8192)
                        if not buf:
                            break
                        chunks.append(buf)
                if not chunks:
                    raise BridgeError(f"EMPTY_RESPONSE({method}): no data from IDA bridge")
                _ = time.monotonic() - t0
                return b"".join(chunks).decode("utf-8")
            except TimeoutError as exc:
                last_err = exc
                if idx < attempts - 1:
                    time.sleep(self.config.retry_delay_seconds)
                    continue
                raise BridgeError(f"TIMEOUT({method}): {exc}")
            except ConnectionRefusedError as exc:
                last_err = exc
                if idx < attempts - 1:
                    time.sleep(self.config.retry_delay_seconds)
                    continue
                raise BridgeError(f"CONNECTION_REFUSED({method}): {exc}")
            except OSError as exc:
                last_err = exc
                if idx < attempts - 1:
                    time.sleep(self.config.retry_delay_seconds)
                    continue
                raise BridgeError(f"SOCKET_ERROR({method}): {exc}")
        raise BridgeError(f"UNKNOWN_BRIDGE_ERROR({method}): {last_err}")
