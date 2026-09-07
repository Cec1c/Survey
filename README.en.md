<div align="center">

# Survey

[![Language: Python](https://img.shields.io/static/v1?label=Language&message=Python%203.10%2B&color=3776AB&style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![GUI: PyQt5](https://img.shields.io/static/v1?label=GUI&message=PyQt5&color=41CD52&style=flat-square&logo=qt&logoColor=white)](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
[![CLI: Click + Rich](https://img.shields.io/static/v1?label=CLI&message=Click%20%2B%20Rich&color=8B5CF6&style=flat-square)](#choose-an-interface)
[![Integration: IDA Pro](https://img.shields.io/static/v1?label=Integration&message=IDA%20Pro&color=F97316&style=flat-square)](#connect-ida-pro)
[![License: GPLv3](https://img.shields.io/static/v1?label=License&message=GPLv3&color=2563EB&style=flat-square)](LICENSE)
[![Stars](https://img.shields.io/github/stars/Cec1c/Survey?style=flat-square&label=Stars&color=E3B341)](https://github.com/Cec1c/Survey/stargazers)

An LLM-powered reverse engineering assistant for IDA Pro, with GUI and CLI interfaces.

Analyze programs using decompilation, cross-references, strings, and memory data. Use tools to assist with renaming, comments, and type refinement.

[简体中文](README.md) ｜ [Quick Start](#quick-start) ｜ [Connect IDA](#connect-ida-pro) ｜ [Tools and Extensions](#tools-and-extensions) ｜ [Feedback](https://github.com/Cec1c/Survey/issues)

</div>

> [!NOTE]
> Survey can serve as a general programming and reverse engineering learning assistant without IDA. Analyzing a specific binary requires opening the target in IDA Pro and starting this repository's Bridge plugin. Decompilation also requires an available Hex-Rays Decompiler.

## What Can It Do?

- **Understand program logic.** Read decompiled code, call relationships, and cross-references, and connect conclusions to actual tool results.
- **Organize an analysis session.** Inspect strings, imports, memory, and structures; assist with comments, names, and types.
- **Work through multi-step tasks.** GUI and CLI share services for streaming output, tool-calling loops, result caching, and plan–execute–verify workflows.
- **Extend the workflow.** Use the IDAPython references and UPX Skill, or connect a separately deployed LLM4Decompile service.

## Choose an Interface

| Interface | Command | Best for |
| --- | --- | --- |
| GUI · PyQt5 + Fluent Widgets | `python main.py` | Chat, model settings, Skills browsing, and tool-call displays |
| CLI · Click + Rich | `python -m cli.main run` | Multi-turn terminal sessions |
| CLI · Single question | `python -m cli.main ask "Explain the current function"` | Get one response and exit |

Run all commands from the project root with the virtual environment activated.

## Quick Start

### 1. Install Dependencies

Prepare **Python 3.10+** and a model service supporting OpenAI-compatible Chat Completions. The model must also support tool calling to use the IDA tools.

Windows PowerShell:

```powershell
git clone https://github.com/Cec1c/Survey.git
cd Survey
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On Linux / macOS, activate the environment with `source .venv/bin/activate` after creating it. The GUI also requires a working Qt desktop environment.

### 2. Configure a Model

For the first setup, copy the example to a local configuration file:

```powershell
Copy-Item app/config/llm_config.example.json app/config/llm_config.json
```

Edit `app/config/llm_config.json`:

| Field | Value |
| --- | --- |
| `base_url` | Your model service's API address |
| `api_key` | The service's access key |
| `model` | A model ID available to your account; the example value is optional |
| `use_ida_tools` | Whether to request IDA tool mode |
| `mcp_host` / `mcp_port` | IDA Bridge address; defaults to `127.0.0.1:31337` |

> [!WARNING]
> The local configuration contains your API key. Git ignores `app/config/llm_config.json`; remove the key before sharing configuration or reporting an issue.

### 3. Launch

Choose GUI or CLI:

```powershell
python main.py
```

```powershell
python -m cli.main run
```

Inspect the current model, Skills, and IDA connection status:

```powershell
python -m cli.main config
```

At startup, the CLI checks the Bridge: a successful connection enables IDA tools; an unavailable connection selects plain chat mode. The configured `use_ida_tools` value can differ from the effective runtime state. Check the `config` output for both.

## Connect IDA Pro

1. Copy [`rift_mcp_bridge_plugin.py`](m2/ida_plugin/rift_mcp_bridge_plugin.py) into IDA's `plugins/` directory.
2. Restart IDA, open the target file, and wait for auto-analysis to finish.
3. Check the IDA output window for `[survey-ida-mcp-bridge] socket listening`. The default address is `127.0.0.1:31337`.
4. Start or restart Survey. In the CLI, `/reload` checks the connection again. Then ask the model to call `check_connection`.

> [!IMPORTANT]
> The Bridge starts during IDA plugin initialization. Executing this file through `File → Script file...` alone does not invoke that initialization and does not replace the installation steps above. Survey's GUI / CLI connects directly to the TCP Bridge; normal use does not require a separate FastMCP server process.

Once connected, try asking:

> Analyze the current function's inputs, outputs, and main branches. Use its call relationships to explain its purpose. Read only; do not modify the database yet.

Renaming, type changes, patches, and IDAPython execution can change the analysis state. Save a copy of the IDA database and confirm the target before making changes.

## Tools and Extensions

[`m2/tools/`](m2/tools/) defines **52 IDA tools**, grouped by domain:

| Domain | Representative tools |
| --- | --- |
| Connection and navigation | `check_connection` · `get_metadata` · `get_current_function` |
| Decompilation and call relationships | `decompile_function` · `get_callers` · `get_callees` · `get_xrefs_to` |
| Data queries | `list_functions` · `list_strings` · `list_imports` · `read_bytes` |
| Types and stack frames | `get_struct_info` · `declare_c_type` · `get_stack_frame_variables` |
| Changes and scripting | `rename_function` · `set_comment` · `patch_asm` · `execute_python` |
| Debugging extension | `debug_start` · `debug_step_into` · `debug_get_registers` |

Debugging tools belong to the `dbg` extension group. The entry point, [`ToolManifest`](app/gui/services/tool_manifest.py), and deferred-loading policy determine which tools are exposed to the model; not every tool is enabled by default.

### Skills and LLM4Decompile

- **IDAPython:** [`skills/idapython/`](skills/idapython/) contains IDA Python API references.
- **UPX:** [`skills/upx_unpack/`](skills/upx_unpack/) includes an unpacking Skill and a Windows UPX binary. Other platforms need a matching executable and an updated `upx_path.txt`.
- **Custom Skills:** Add `name`, `description`, `category`, and instructions to `skills/<name>/SKILL.md`. Reload, then inspect it in the Skills page or with CLI `/skills`.
- **LLM4Decompile:** Disabled by default. Deploy the model service separately, then set `llm4decompile_enabled`, `llm4decompile_base_url`, and `llm4decompile_model`. The current model-facing tool is `llm4decompile_refine`; the underlying service also provides structure and identifier recovery methods.

### Common CLI Controls

| Command or key | Purpose |
| --- | --- |
| `/model` / `/models` | Show the current model / fetch available models |
| `/clear` | Clear conversation history and the tool cache |
| `/reload` | Reload configuration and check the IDA connection |
| `/skills` / `/help` | Inspect Skills / show full help |
| `Alt+Enter` | Insert a newline |
| `Ctrl+O` | Expand or collapse reasoning content |

## Development and Configuration Reference

GUI and CLI share model calls, context management, and tool execution in `app/gui/services/`. The IDA plugin performs operations inside IDA's process. [`m2/ida_mcp_server.py`](m2/ida_mcp_server.py) provides a separate FastMCP entry point.

| Topic | Reference |
| --- | --- |
| Example configuration and tuning parameters | [llm_config.example.json](app/config/llm_config.example.json) |
| Configuration fields and loading | [llm_config.py](app/gui/state/llm_config.py) |
| Tool registration, categories, and metadata | [_decorators.py](m2/tools/_decorators.py) |
| Caching, retries, and concurrency | [tool_pipeline.py](app/gui/services/tool_pipeline.py) |
| Optional decompilation model service | [llm4decompile_service.py](app/gui/services/llm4decompile_service.py) |

Run tests in an environment with the dependencies installed:

```powershell
python -m pytest
python -m pytest tests/test_tool_pipeline.py -v
```

Report problems through [Issues](https://github.com/Cec1c/Survey/issues) or submit a Pull Request. Register new tools with `@ida_tool`, and update the relevant tests and documentation.

## License and Credits

Licensed under the [GNU General Public License v3.0](LICENSE).

Thanks to [IDA Pro](https://hex-rays.com/ida-pro/), [MCP](https://modelcontextprotocol.io/), [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets), [Rich](https://github.com/Textualize/rich), and [LLM4Decompile](https://github.com/albertan017/LLM4Decompile) for their tools, components, and research.
