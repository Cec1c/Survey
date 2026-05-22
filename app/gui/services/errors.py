"""Structured error hierarchy for agent chat service.

Provides typed exceptions replacing bare {"ok": False, "error": str} dicts,
plus a helper to classify error strings into appropriate exception types.
"""

from typing import Any, Dict


class AgentError(Exception):
    """Base for all agent errors."""

    code: str = "AGENT"
    recoverable: bool = True
    retryable: bool = False

    def __init__(self, message: str, details: Dict[str, Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": self.message,
            "code": self.code,
            "recoverable": self.recoverable,
            "retryable": self.retryable,
            **self.details,
        }


class NetworkError(AgentError):
    """Timeout, connection refused, socket errors."""

    code = "NETWORK"
    recoverable = True
    retryable = True


class ToolError(AgentError):
    """IDA tool returned an error."""

    code = "TOOL"
    recoverable = True
    retryable = False


class ModelError(AgentError):
    """Model API error (400, 413, rate limit, insufficient tool messages)."""

    code = "MODEL"
    recoverable = True
    retryable = True


class FatalError(AgentError):
    """Unrecoverable error."""

    code = "FATAL"
    recoverable = False
    retryable = False


# -- Error classification helpers -------------------------------------------

_ERROR_CLASSIFIERS = [
    # Network-level failures (always retryable)
    ("timeout", NetworkError),
    ("connection refused", NetworkError),
    ("connection reset", NetworkError),
    ("connection aborted", NetworkError),
    ("socket error", NetworkError),
    ("no route to host", NetworkError),
    ("name or service not known", NetworkError),
    ("eof occurred", NetworkError),
    ("broken pipe", NetworkError),
    ("network is unreachable", NetworkError),
    # Model API errors (retryable)
    ("rate limit", ModelError),
    ("rate_limit", ModelError),
    ("insufficient tool messages", ModelError),
    ("http 400", ModelError),
    ("http 413", ModelError),
    ("http 429", ModelError),
    ("http 500", ModelError),
    ("http 502", ModelError),
    ("http 503", ModelError),
    ("model output is truncated", ModelError),
    ("model output 被截断", ModelError),
    ("request too large", ModelError),
    ("context length exceeded", ModelError),
    ("token limit", ModelError),
    # Tool-level failures (not retryable by default)
    ("tool execution failed", ToolError),
    ("tool call failed", ToolError),
    ("unknown tool", ToolError),
    ("blocked by", ToolError),
    ("ida error", ToolError),
    ("cancelled by sibling error", ToolError),
    ("not found", ToolError),
    ("invalid argument", ToolError),
    ("invalid args", ToolError),
    ("bad address", ToolError),
    ("permission denied", ToolError),
    ("undefined", ToolError),
    ("no such", ToolError),
    ("already exists", ToolError),
    ("cannot open", ToolError),
    ("not supported", ToolError),
    ("decompile_failed", ToolError),
    # Fatal (not recoverable)
    ("api key", FatalError),
    ("authentication", FatalError),
    ("unauthorized", FatalError),
    ("forbidden", FatalError),
    ("invalid api key", FatalError),
    ("key 未配置", FatalError),
    ("未配置", FatalError),
]


def classify_error(error_str: str) -> AgentError:
    """Analyze an error string and return the appropriate AgentError subclass.

    Checks known error patterns and returns the best matching error type.
    Defaults to ``AgentError`` (generic, recoverable, non-retryable) when no
    pattern matches.

    Examples::

        classify_error("timeout connecting to IDA")
        # -> NetworkError("timeout connecting to IDA")

        classify_error("insufficient tool messages in request")
        # -> ModelError("insufficient tool messages in request")

        classify_error("HTTP 500 Internal Server Error")
        # -> ModelError("HTTP 500 Internal Server Error")
    """
    lower = (error_str or "").lower()

    for keyword, exc_type in _ERROR_CLASSIFIERS:
        if keyword in lower:
            return exc_type(error_str)

    return AgentError(error_str)


def reclassify_on_retry_failure(error: AgentError) -> AgentError:
    """Promote a retryable error to non-retryable after retries are exhausted.

    When a ``NetworkError`` or ``ModelError`` has been retried and all
    attempts failed, wrap it in a ``FatalError`` so the caller stops
    retrying.
    """
    if error.retryable:
        return FatalError(
            f"Retry exhausted: {error.message}",
            details={"original_code": error.code, **error.details},
        )
    return error
