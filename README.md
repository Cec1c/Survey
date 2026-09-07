<div align="center">

# Survey

[![语言：Python](https://img.shields.io/static/v1?label=%E8%AF%AD%E8%A8%80&message=Python%203.10%2B&color=3776AB&style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![图形界面：PyQt5](https://img.shields.io/static/v1?label=%E5%9B%BE%E5%BD%A2%E7%95%8C%E9%9D%A2&message=PyQt5&color=41CD52&style=flat-square&logo=qt&logoColor=white)](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
[![终端：Click + Rich](https://img.shields.io/static/v1?label=%E7%BB%88%E7%AB%AF&message=Click%20%2B%20Rich&color=8B5CF6&style=flat-square)](#选择使用方式)
[![集成：IDA Pro](https://img.shields.io/static/v1?label=%E9%9B%86%E6%88%90&message=IDA%20Pro&color=F97316&style=flat-square)](#连接-ida-pro)
[![许可：GPLv3](https://img.shields.io/static/v1?label=%E8%AE%B8%E5%8F%AF&message=GPLv3&color=2563EB&style=flat-square)](LICENSE)
[![Stars](https://img.shields.io/github/stars/Cec1c/Survey?style=flat-square&label=Stars&color=E3B341)](https://github.com/Cec1c/Survey/stargazers)

面向 IDA Pro 的 LLM 逆向分析助手，提供图形界面和命令行两种入口。

让模型结合反编译、交叉引用、字符串与内存数据分析程序，也可使用工具辅助重命名、注释和类型整理。

[English](README.en.md) ｜ [快速开始](#快速开始) ｜ [连接 IDA](#连接-ida-pro) ｜ [工具与扩展](#工具与扩展) ｜ [反馈](https://github.com/Cec1c/Survey/issues)

</div>

> [!NOTE]
> 不连接 IDA 时，Survey 可以作为普通编程与逆向学习助手使用。分析具体二进制需要在 IDA Pro 中打开目标，并启动本仓库的 Bridge 插件；反编译功能还需要可用的 Hex-Rays Decompiler。

## 能做什么？

- **理解程序逻辑。** 读取反编译结果、函数调用关系和交叉引用，把分析结论关联到实际工具输出。
- **整理分析现场。** 查询字符串、导入表、内存与结构体，辅助添加注释、重命名和修改类型。
- **连续完成分析任务。** GUI 与 CLI 共用服务层，支持流式输出、多轮工具调用、结果缓存和计划—执行—验证流程。
- **扩展分析能力。** 使用 IDAPython 参考与 UPX Skill，也可接入独立部署的 LLM4Decompile 服务。

## 选择使用方式

| 入口 | 启动命令 | 适合场景 |
| --- | --- | --- |
| GUI · PyQt5 + Fluent Widgets | `python main.py` | 对话、模型设置、Skills 浏览和工具调用展示 |
| CLI · Click + Rich | `python -m cli.main run` | 终端中的多轮交互 |
| CLI · 单次提问 | `python -m cli.main ask "分析当前函数的逻辑"` | 获取一次回答后退出 |

所有命令均在项目根目录、已激活的虚拟环境中执行。

## 快速开始

### 1. 安装依赖

准备 **Python 3.10+**，以及支持 OpenAI-compatible Chat Completions 的模型服务。使用 IDA 工具时，模型还需支持工具调用。

Windows PowerShell：

```powershell
git clone https://github.com/Cec1c/Survey.git
cd Survey
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux / macOS 创建虚拟环境后使用 `source .venv/bin/activate` 激活；GUI 还需要可用的 Qt 桌面环境。

### 2. 配置模型

首次配置时，从模板复制本地配置文件：

```powershell
Copy-Item app/config/llm_config.example.json app/config/llm_config.json
```

编辑 `app/config/llm_config.json`：

| 字段 | 填写内容 |
| --- | --- |
| `base_url` | 模型服务的 API 地址 |
| `api_key` | 该服务的访问密钥 |
| `model` | 账户实际可用的模型 ID，不必沿用模板示例 |
| `use_ida_tools` | 是否请求启用 IDA 工具模式 |
| `mcp_host` / `mcp_port` | IDA Bridge 地址，默认 `127.0.0.1:31337` |

> [!WARNING]
> 本地配置包含真实 API Key。`app/config/llm_config.json` 已被 Git 忽略；分享配置或提交问题时，请先移除密钥。

### 3. 启动

选择 GUI 或 CLI：

```powershell
python main.py
```

```powershell
python -m cli.main run
```

查看当前模型、Skills 和 IDA 连接状态：

```powershell
python -m cli.main config
```

CLI 启动时会检测 Bridge：连接成功则启用 IDA 工具，连接不可用则使用普通对话模式。配置中的 `use_ida_tools` 与运行时实际状态可能不同，以 `config` 输出为准。

## 连接 IDA Pro

1. 将 [`rift_mcp_bridge_plugin.py`](m2/ida_plugin/rift_mcp_bridge_plugin.py) 复制到 IDA 的 `plugins/` 目录。
2. 重启 IDA，打开待分析文件并等待自动分析完成。
3. 确认 IDA 输出窗口出现 `[survey-ida-mcp-bridge] socket listening`；默认监听 `127.0.0.1:31337`。
4. 启动或重启 Survey。在 CLI 中可用 `/reload` 重新检测连接，再让模型调用 `check_connection`。

> [!IMPORTANT]
> Bridge 由 IDA 的插件初始化流程启动。仅通过 `File → Script file...` 执行这个文件不会自动调用插件初始化，因此不能替代上面的安装步骤。Survey 的 GUI / CLI 直接连接 TCP Bridge，日常使用无需额外启动 FastMCP 服务。

连接成功后，可以这样提问：

> 分析当前函数的输入、输出和主要分支，结合调用关系解释用途。先只读取，不修改数据库。

重命名、类型修改、补丁和 IDAPython 执行会改变分析状态；操作前保存 IDA 数据库副本，并确认目标文件。

## 工具与扩展

[`m2/tools/`](m2/tools/) 定义了 **52 个 IDA 工具**，按领域组织：

| 领域 | 代表工具 |
| --- | --- |
| 连接与定位 | `check_connection` · `get_metadata` · `get_current_function` |
| 反编译与调用关系 | `decompile_function` · `get_callers` · `get_callees` · `get_xrefs_to` |
| 数据查询 | `list_functions` · `list_strings` · `list_imports` · `read_bytes` |
| 类型与栈帧 | `get_struct_info` · `declare_c_type` · `get_stack_frame_variables` |
| 修改与脚本 | `rename_function` · `set_comment` · `patch_asm` · `execute_python` |
| 调试扩展 | `debug_start` · `debug_step_into` · `debug_get_registers` |

调试工具属于 `dbg` 扩展组。实际提供给模型的工具由运行入口、[`ToolManifest`](app/gui/services/tool_manifest.py) 和延迟加载策略决定，并非所有工具都默认启用。

### Skills 与 LLM4Decompile

- **IDAPython**：[`skills/idapython/`](skills/idapython/) 提供 IDA Python API 参考。
- **UPX**：[`skills/upx_unpack/`](skills/upx_unpack/) 包含解壳 Skill 和 Windows 版 UPX；其他平台需准备对应可执行文件，并调整 `upx_path.txt`。
- **自定义 Skill**：在 `skills/<名称>/SKILL.md` 中填写 `name`、`description`、`category` 和正文，重载后通过 Skills 页面或 CLI `/skills` 查看。
- **LLM4Decompile**：默认关闭。单独部署对应模型服务后，再配置 `llm4decompile_enabled`、`llm4decompile_base_url` 和 `llm4decompile_model`。当前模型工具入口是 `llm4decompile_refine`；底层服务还提供结构体和标识符恢复方法。

### CLI 常用操作

| 命令或按键 | 用途 |
| --- | --- |
| `/model` / `/models` | 查看当前模型 / 获取可用模型列表 |
| `/clear` | 清空对话历史与工具缓存 |
| `/reload` | 重载配置并检测 IDA 连接 |
| `/skills` / `/help` | 查看 Skills / 完整帮助 |
| `Alt+Enter` | 插入换行 |
| `Ctrl+O` | 展开或折叠推理内容 |

## 开发与配置参考

GUI 与 CLI 共用 `app/gui/services/` 中的模型调用、上下文管理和工具执行服务。IDA 插件负责在 IDA 进程内执行操作；[`m2/ida_mcp_server.py`](m2/ida_mcp_server.py) 则提供独立的 FastMCP 入口。

| 内容 | 入口 |
| --- | --- |
| 配置模板与可调参数 | [llm_config.example.json](app/config/llm_config.example.json) |
| 配置字段与加载逻辑 | [llm_config.py](app/gui/state/llm_config.py) |
| 工具注册、分类与元数据 | [_decorators.py](m2/tools/_decorators.py) |
| 缓存、重试与并发控制 | [tool_pipeline.py](app/gui/services/tool_pipeline.py) |
| 可选反编译模型服务 | [llm4decompile_service.py](app/gui/services/llm4decompile_service.py) |

在已安装依赖的环境中运行测试：

```powershell
python -m pytest
python -m pytest tests/test_tool_pipeline.py -v
```

欢迎通过 [Issues](https://github.com/Cec1c/Survey/issues) 反馈问题或提交 Pull Request。新增工具时，使用 `@ida_tool` 注册函数，并更新相应测试与文档。

## 许可与致谢

本项目采用 [GNU General Public License v3.0](LICENSE)。

感谢 [IDA Pro](https://hex-rays.com/ida-pro/)、[MCP](https://modelcontextprotocol.io/)、[PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)、[Rich](https://github.com/Textualize/rich) 和 [LLM4Decompile](https://github.com/albertan017/LLM4Decompile) 提供的工具、组件与研究成果。
