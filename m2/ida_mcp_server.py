"""Survey IDA MCP Server — FastMCP 暴露 IDA Pro 分析/修改/调试工具。

架构: LLM (MCP) → FastMCP → BridgeClient (TCP:31337) → IDA Plugin → IDA API

工具定义在 m2/tools/ 各模块中，通过 @ida_tool 装饰器注册。
本文件负责: 连接 Bridge, 将 TOOL_REGISTRY 中的工具注册到 FastMCP。
"""

import os
import sys
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

# BridgeClient import with fallback for direct execution
if __package__ is None or __package__ == "":
    sys.path.insert(0, os.path.dirname(__file__))
    from bridge_protocol import BridgeClient, BridgeClientConfig
else:
    from m2.bridge_protocol import BridgeClient, BridgeClientConfig

mcp = FastMCP("Survey-IDA-MCP")

_client = BridgeClient(
    BridgeClientConfig(
        host=os.getenv("RIFT_IDA_BRIDGE_HOST", "127.0.0.1"),
        port=int(os.getenv("RIFT_IDA_BRIDGE_PORT", "31337")),
        timeout_seconds=float(os.getenv("RIFT_IDA_BRIDGE_TIMEOUT", "20.0")),
        retries=int(os.getenv("RIFT_IDA_BRIDGE_RETRIES", "1")),
        retry_delay_seconds=float(os.getenv("RIFT_IDA_BRIDGE_RETRY_DELAY", "0.25")),
    )
)

# Inject BridgeClient into all tool modules
from m2.tools import set_client
set_client(_client)

# Register all tools from TOOL_REGISTRY with FastMCP
from m2.tools._decorators import TOOL_REGISTRY

for name, meta in TOOL_REGISTRY.items():
    mcp.add_tool(
        fn=meta.fn,
        name=meta.name,
        description=meta.description,
        meta={
            "category": meta.category,
            "concurrency_safe": meta.concurrency_safe,
            "deferred": meta.deferred,
            "unsafe": meta.unsafe,
            "ext_group": meta.ext_group,
        },
    )

if __name__ == "__main__":
    mcp.run()
