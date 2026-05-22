# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Survey is a dual-interface (GUI + CLI) LLM-powered reverse engineering assistant. It connects to IDA Pro via a custom MCP JSON-RPC bridge to let an LLM call IDA analysis tools (decompile, disassemble, rename, cross-reference, etc.). The GUI uses PyQt5 + qfluentwidgets; the CLI uses Click + Rich + prompt_toolkit. Both share the same backend services and state models.

## Commands

```bash
# Activate venv (Windows)
D:/rift/venv/Scripts/activate

# GUI
python main.py

# CLI — interactive REPL
python -m cli.main run
# CLI — single question
python -m cli.main ask "analyze this function"
# CLI — show config
python -m cli.main config

# Or via batch files:
cli.bat           # CLI REPL
start.bat         # GUI
```

No formal test suite, no linter config, no build system. Dependencies are installed ad-hoc in the venv.

## Architecture

### Three-layer MCP tool-call chain

```
m2/bridge_protocol.py (BridgeClient: JSON-RPC over TCP)
  → app/gui/services/mcp_service.py (MCPService: thin wrapper, returns {ok, result|error})
    → app/gui/services/chat_service.py (AgentChatService: OpenAI-compatible streaming + tool-calling loop)
```

### m2/ — standalone MCP bridge (no imports from app/)

- `m2/bridge_protocol.py` — `BridgeClient` class. Sends JSON-RPC requests over TCP to the IDA plugin.
- `m2/ida_mcp_server.py` — FastMCP server. Imports tools from `m2/tools/` and registers them via `mcp.add_tool()`.
- `m2/tools/` — Tool definitions organized by domain. Each module uses `@ida_tool` decorator which auto-registers into `TOOL_REGISTRY`. The registry is the single source of truth for tool metadata (name, description, parameters, concurrency_safe, deferred, unsafe, ext_group).
  - `_decorators.py` — `@ida_tool(category, concurrency_safe, deferred, unsafe)` and `@ext(group)` decorators
  - `meta.py` — 5 tools: check_connection, get_metadata, get_current_address, get_current_function, get_entry_points
  - `analysis.py` — 6 tools: decompile_function, disassemble_function, get_function, get_callers, get_callees, get_xrefs_to
  - `memory.py` — 3 tools: read_integer (replaces data_read_byte/word/dword/qword), read_string, read_bytes
  - `query.py` — 5 tools: list_functions, list_globals, list_strings (each with optional filter param), list_imports, get_global_value
  - `structures.py` — 6 tools: get_defined_structures, search_structures, get_struct_info, get_struct_at_address, list_local_types, declare_c_type
  - `modify.py` — 8 tools: rename_function, rename_global_variable, rename_local_variable, set_comment, set_function_prototype, set_global_variable_type, set_local_variable_type, patch_asm
  - `stack.py` — 5 tools: get_stack_frame_variables, create_stack_frame_variable, rename_stack_frame_variable, set_stack_frame_variable_type, delete_stack_frame_variable
  - `python.py` — 1 tool: execute_python (execute arbitrary IDAPython in IDA)
  - `debug.py` — 13 tools (behind `@ext("dbg")`): debug_start, debug_exit, debug_continue, debug_step_into, debug_step_over, debug_run_to, debug_list_breakpoints, debug_add_breakpoint, debug_delete_breakpoint, debug_get_registers, debug_get_stacktrace, debug_read_memory, debug_write_memory
- `m2/ida_plugin/rift_mcp_bridge_plugin.py` — IDA Pro plugin (`SurveyMcpBridgePlugin`). Runs a TCP JSON-RPC server inside IDA's process. Single-file for easy installation (~1100 lines).

### app/ — shared backend

- **`AgentChatService`** (`app/gui/services/chat_service.py`): The central class. `run_turn()` → `_run_agent_turn()` loops: stream completion → if tool_calls → execute via MCPService → append results → repeat (max `agent_max_tool_rounds`). Has persistent tool-result cache (`_persistent_tool_cache`) to avoid duplicate calls. Composes `MCPService`, `SkillsService`, `WorkflowService`.
  - `_stream_chat_completion()`: Raw SSE streaming parser. Returns `{role, content, tool_calls?, reasoning_content?}`.
  - `_build_messages()`: Reconstructs API message list from `ChatState`. Critical: must preserve `reasoning_content` on assistant messages for models like DeepSeek that require it round-tripped.
  - `_prune_agent_tool_messages()`: Drops oldest tool-call pairs to stay under token limits.
- **`MCPService`** (`mcp_service.py`): Thin wrapper, `call_tool(name, args)` → `BridgeClient.call()`.
- **`ToolManifest`** (`tool_manifest.py`): Reads tool metadata from `m2.tools._decorators.TOOL_REGISTRY` (the single source of truth). Provides `get_schemas()`, `is_concurrency_safe()`, and extension group filtering. Replaces the former `ToolRegistry`.
- **`ToolPipeline`** (`tool_pipeline.py`): Merged orchestrator — handles hook chain, caching, retry, concurrency control (safe tools parallel, unsafe sequential), result trimming, and error feedback. Inlines what was formerly `ToolExecutor`, `ToolResultManager`, and `ErrorRecoveryHandler`.
- **`SkillsService`** (`skills_service.py`): Scans `skills/` directory for Markdown files with YAML frontmatter (`name`, `description`, `category`). Injects matching skill content into system prompts.
- **`WorkflowService`** (`workflow_service.py`): Plan-execute-verify cycle (`WorkflowStage` enum). Auto-generates multi-step workflows from keyword detection. Tracks which tools have been called and their results.
- **State** (`app/gui/state/`): `LLMConfig` is a dataclass loaded from JSON; `ChatState` holds `ChatMessage` list (`role`, `content`, `timestamp`, `reasoning_content`).
- **Config**: `app/config/llm_config.json` — model, API key, MCP host/port, skills directory, agent settings.

### cli/ — CLI interface (parallel consumer, not a GUI subprocess)

- `cli/main.py` — Click command group (`run`, `ask`, `config`).
- `cli/runner.py` — `CLIRunner`: creates same `MCPService` + `AgentChatService` instances as GUI. Manages streaming callbacks, tool-call panel rendering, `/slash` commands, model switching. Stores messages to `ChatState` for multi-turn context.
- `cli/ui.py` — Terminal rendering with Rich Console + prompt_toolkit `PromptSession`. **Ctrl+O reasoning toggle**: uses `\033[s` (save cursor) in `print_reasoning_indicator()` and `\033[u\033[J` (restore + clear to end) in `print_reasoning_toggle(event)`. Calls `event.app.invalidate()` to force prompt_toolkit redraw after terminal manipulation. State tracked via module globals (`_response_reasoning`, `_response_content`, `_response_expanded`).

### GUI (app/gui/)

- `main_window.py` — FluentWindow with 4 navigation tabs (Chat, Skills, Model, Settings).
- `pages/chat_page.py` — Chat UI with streaming, thought bubble widget for reasoning, tool-call panels.
- `pages/skills_page.py`, `pages/task_page.py`, `pages/settings_page.py` — Corresponding tab pages.

### skills/ — skills system

Markdown files with YAML frontmatter. Currently one skill: `idapython` with 50+ reference docs for IDA Python APIs. Scanned by `SkillsService.load_skills()` and injected into LLM system prompts for relevant queries.

### LLM4Decompile integration

- **`LLM4DecompileService`** (`app/gui/services/llm4decompile_service.py`): Wraps a local vLLM server running LLM4Decompile models. Three inference modes: `refine_pseudocode()` (V2 Ref single-pass optimization), `recover_structure()` (SK2Decompile Phase 1), `recover_identifiers()` (SK2Decompile Phase 2), plus `refine_two_phase()` for combined pipeline.
- **MCP tool `llm4decompile_refine`**: Registered in `_tool_schemas()` but routed locally (not via MCP bridge). First calls IDA `decompile_function`, then feeds pseudocode to LLM4Decompile. Falls back gracefully when vLLM is unreachable.
- **Config fields**: `llm4decompile_enabled`, `llm4decompile_base_url`, `llm4decompile_model`, `llm4decompile_timeout` in `LLMConfig` and `app/config/llm_config.json`.
- **Requires**: vLLM serving `LLM4Binary/llm4decompile-9b-v2` (or similar) at the configured URL. GPU with >=16GB VRAM recommended.

## Key constraints

- **`reasoning_content` must be round-tripped**: DeepSeek R1/V4 models return `reasoning_content` in streaming and require it passed back in subsequent requests. The agent loop and `_build_messages` must include it. `ChatMessage.reasoning_content` stores it across turns.
- **Windows console encoding**: Both `cli/main.py` and `cli/runner.py` force `sys.stdout/stderr` to UTF-8 with `errors="replace"` to survive emoji in win32 console.
- **Emoji ban**: CLI system prompt appends `"Do not use emoji in your responses. 请勿在回复中使用任何 emoji 表情符号。"` — Windows consoles can't render them.
- **`_model_rejects_sampling_params`**: Models with "reasoner" in the name skip `temperature`/`top_p` in API calls.
- **IDA connection is optional**: `use_ida_tools: false` in config disables MCP bridge. Connection refused errors when IDA is not running are expected, not bugs.
