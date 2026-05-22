<div align="center">

# 🔬 Survey

**LLM-Powered Reverse Engineering Assistant**

*双界面（GUI + CLI）智能逆向分析助手，通过 MCP 协议连接 IDA Pro*

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-Fluent-7a3ff2?logo=qt&logoColor=white)
![IDA Pro](https://img.shields.io/badge/IDA%20Pro-MCP%20Bridge-fc6a26?logo=data:image/svg+xml;base64,)
![License](https://img.shields.io/badge/License-GPLv3-green)

</div>

---

## 📖 简介

Survey 是一个 **LLM 驱动的逆向工程助手**，让大语言模型直接调用 IDA Pro 的分析工具来帮你做逆向。

它不是一个简单的聊天机器人——它可以 **反编译函数、追踪交叉引用、重命名变量、分析内存布局**，并基于工具返回的真实数据给出有理有据的分析结论。

> 🧠 **核心理念**：LLM 负责推理，工具负责取证，一切结论都有证据支撑。

---

## ✨ 特性一览

### 🖥️ 双界面

| 界面 | 启动方式 | 适合场景 |
|------|---------|---------|
| **GUI** (PyQt5 + Fluent) | `python main.py` 或 `start_venv.bat` | 图形化操作、实时流式输出、可视化工具面板 |
| **CLI** (Click + Rich) | `python -m cli.main run` 或 `cli.bat` | 终端工作流、脚本集成、SSH 远程使用 |

### 🛠️ 50+ IDA Pro 工具

通过 MCP (Model Context Protocol) 桥接，LLM 可以调用覆盖逆向全流程的工具：

| 分类 | 工具 | 说明 |
|------|------|------|
| 📋 **元数据** | `check_connection` `get_metadata` `get_entry_points` | 连接状态、二进制信息 |
| 🔍 **分析** | `decompile_function` `disassemble_function` `get_callers` `get_callees` | 反编译、反汇编、调用链追踪 |
| 🧠 **查询** | `list_functions` `list_strings` `list_imports` `list_globals` | 全局搜索与过滤 |
| ✏️ **修改** | `rename_function` `set_comment` `set_function_prototype` `patch_asm` | 重命名、注释、类型修改 |
| 🏗️ **结构体** | `get_defined_structures` `search_structures` `declare_c_type` | 类型系统操作 |
| 📦 **内存** | `read_integer` `read_string` `read_bytes` | 内存数据读取 |
| 🗂️ **栈帧** | `get_stack_frame_variables` `rename_stack_frame_variable` | 栈变量分析 |
| 🐍 **脚本** | `execute_python` | 在 IDA 中执行任意 IDAPython 脚本 |
| 🐛 **调试** | `debug_start` `debug_step_into` `debug_add_breakpoint` `debug_get_registers` | 完整调试器控制 |

### 🤖 智能 Agent 循环

```
用户提问 → LLM 思考 → 调用工具 → 获取结果 → 继续推理 → ... → 输出结论
```

- **多轮工具调用**：最多 48 轮自动工具调用，支持复杂分析任务
- **假设驱动分析**：自动形成假设、收集证据、验证结论
- **计划-执行-验证**：三阶段状态机，确保分析有条理
- **工具结果缓存**：避免重复调用，节省 token 开销
- **并发控制**：安全工具并行执行，修改类工具串行保护

### 📚 Skills 系统

通过 Markdown 文件扩展 LLM 的知识库：

- 🐍 **IDAPython** — 50+ IDA Python API 参考文档
- 📦 **UPX 解壳** — UPX 压缩壳检测与脱壳工具

### 🔬 LLM4Decompile 集成（可选）

接入本地 vLLM 服务器运行 LLM4Decompile 模型，实现：
- 伪代码优化（Refine）
- 结构体恢复（Recover Structure）
- 标识符恢复（Recover Identifiers）

---

## 🚀 快速开始

### 📋 前置要求

- **Python 3.10+**
- **IDA Pro 7.x / 8.x / 9.x**（可选，不连接 IDA 也能当通用助手用）
- 一个 LLM API 密钥（DeepSeek / OpenAI / 其他兼容 API）

### ⚙️ 安装

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/survey.git
cd survey

# 2. 创建虚拟环境
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate    # Linux/macOS

# 3. 安装依赖
pip install -r requirements.txt
```

### 🔑 配置

```bash
# 从模板创建你的配置文件
copy app\config\llm_config.example.json app\config\llm_config.json
```

编辑 `app/config/llm_config.json`，至少修改以下字段：

```jsonc
{
  "api_key": "sk-your-actual-api-key",   // ← 填入你的 API Key
  "base_url": "https://api.deepseek.com", // ← 或其他兼容 API 地址
  "model": "deepseek-v4-pro",             // ← 模型名称
  "use_ida_tools": true                    // ← false 则不需要 IDA
}
```

> ⚠️ **注意**：`llm_config.json` 已被 `.gitignore` 忽略，不会提交到仓库。请勿将含有真实 API Key 的配置文件推送至公开仓库。

### 🏃 运行

```bash
# GUI 模式 — 图形界面
python main.py
# 或者直接双击 start_venv.bat（自动创建 venv 并安装依赖）

# CLI 模式 — 终端交互
python -m cli.main run
# 或者双击 cli.bat

# CLI 单次提问
python -m cli.main ask "分析这个函数的逻辑"

# 查看 CLI 配置
python -m cli.main config
```

---

## 🏛️ 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    用户界面层                                │
│   ┌──────────────────┐    ┌──────────────────┐             │
│   │   GUI (PyQt5)    │    │   CLI (Click)    │             │
│   │  main_window.py  │    │   cli/runner.py  │             │
│   └────────┬─────────┘    └────────┬─────────┘             │
│            │                       │                        │
├────────────┼───────────────────────┼────────────────────────┤
│            ▼         服务层        ▼                        │
│   ┌─────────────────────────────────────────┐              │
│   │       AgentChatService (协调器)          │              │
│   │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │              │
│   │  │ApiClient │ │Message   │ │Context   │ │              │
│   │  │(SSE流式) │ │Builder   │ │Manager   │ │              │
│   │  └──────────┘ └──────────┘ └──────────┘ │              │
│   │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │              │
│   │  │ToolPipe  │ │Decision  │ │Skills    │ │              │
│   │  │line      │ │Engine    │ │Service   │ │              │
│   │  └──────────┘ └──────────┘ └──────────┘ │              │
│   └────────────────────┬────────────────────┘              │
│                        │                                    │
├────────────────────────┼────────────────────────────────────┤
│                        ▼        MCP 桥接层                  │
│   ┌─────────────────────────────────────────┐              │
│   │   MCPService → BridgeClient (TCP)       │              │
│   │        JSON-RPC over TCP :31337         │              │
│   └────────────────────┬────────────────────┘              │
│                        │                                    │
├────────────────────────┼────────────────────────────────────┤
│                        ▼        IDA Pro 插件                │
│   ┌─────────────────────────────────────────┐              │
│   │   SurveyMcpBridgePlugin (IDA 内运行)     │              │
│   │   50+ @ida_tool 工具，覆盖逆向全流程      │              │
│   └─────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────┘
```

### 📁 目录结构

```
survey/
├── main.py                    # GUI 入口
├── cli/                       # CLI 界面
│   ├── main.py                # Click 命令组
│   ├── runner.py              # CLI 运行器
│   └── ui.py                  # Rich 终端渲染
├── app/                       # 共享后端
│   ├── config/
│   │   ├── llm_config.json        # ← 你的配置（gitignore 忽略）
│   │   └── llm_config.example.json # ← 配置模板
│   └── gui/
│       ├── main_window.py     # FluentWindow 主窗口
│       ├── pages/             # 4 个标签页（Chat/Skills/Model/Settings）
│       ├── services/          # 核心服务层
│       │   ├── chat_service.py    # Agent 协调器
│       │   ├── mcp_service.py     # MCP 客户端
│       │   ├── tool_pipeline.py   # 工具执行管线
│       │   └── ...
│       └── state/             # 状态模型（LLMConfig, ChatState）
├── m2/                        # 独立 MCP 桥接层（无 app/ 依赖）
│   ├── bridge_protocol.py     # TCP JSON-RPC 客户端
│   ├── ida_mcp_server.py      # FastMCP 服务器
│   ├── tools/                 # 50+ IDA 工具定义
│   │   ├── analysis.py        # 反编译/反汇编
│   │   ├── query.py           # 搜索/列表
│   │   ├── modify.py          # 重命名/修改
│   │   ├── debug.py           # 调试器控制
│   │   └── ...
│   └── ida_plugin/
│       └── rift_mcp_bridge_plugin.py  # IDA 插件（单文件安装）
├── skills/                    # Skills 知识库
│   ├── idapython/             # IDAPython API 参考
│   └── upx_unpack/            # UPX 脱壳工具
├── tests/                     # 测试套件
├── requirements.txt
├── start_venv.bat             # 一键启动 GUI（自动配环境）
└── cli.bat                    # 一键启动 CLI
```

---

## 🔌 IDA Pro 插件安装

1. 打开 IDA Pro，进入 `File → Script file...`
2. 运行 `m2/ida_plugin/rift_mcp_bridge_plugin.py`
3. 插件会在 `127.0.0.1:31337` 启动 TCP JSON-RPC 服务器
4. Survey 自动连接并激活全部工具

> 💡 **提示**：也可以将插件脚本放入 IDA 的 `plugins/` 目录实现自动加载。

---

## 🎯 使用示例

### GUI 模式

启动后你会看到 4 个标签页：

| 标签 | 功能 |
|------|------|
| 💬 **Chat** | 主对话界面，支持流式输出、思考气泡、工具调用面板 |
| 🧩 **Skills** | 管理和浏览已加载的 Skills |
| ⚙️ **Model** | 切换模型、配置 API 参数 |
| 🔧 **Settings** | 全局设置、MCP 连接配置 |

### CLI 模式

```
> 分析一下 main 函数的逻辑

 🔧 调用工具: decompile_function(0x401000)
 ✅ 工具完成

 📋 分析结果：

 main 函数是一个简单的入口函数，执行以下操作：
 1. 调用 init_network() 初始化网络模块
 2. 调用 load_config("config.ini") 加载配置
 3. 进入主循环 event_loop()，处理用户输入
 ...
```

### CLI 快捷键

| 按键 | 功能 |
|------|------|
| `Ctrl+O` | 切换显示/隐藏 LLM 推理过程 |
| `Ctrl+C` | 中断当前生成 |
| `/model` | 切换模型 |
| `/clear` | 清空对话历史 |
| `/help` | 显示帮助 |

---

## ⚡ 高级配置

`llm_config.json` 中还有许多可调参数：

```jsonc
{
  // Agent 行为
  "agent_max_tool_rounds": 48,          // 最大工具调用轮数
  "agent_enable_planning": true,        // 启用计划-执行-验证循环
  "agent_enable_hypothesis_tracking": true,  // 假设追踪

  // 上下文管理
  "agent_compaction_threshold_chars": 25000, // 触发上下文压缩的阈值
  "agent_max_context_chars": 80000,         // 最大上下文长度
  "tool_result_global_budget_chars": 120000, // 工具结果总预算

  // 工具执行
  "tool_executor_max_workers": 8,       // 并发工具执行数
  "tool_executor_decompile_limit": 2,   // 反编译并发限制
  "tool_deferred_loading": true,        // 延迟加载不常用工具

  // MCP 连接
  "mcp_host": "127.0.0.1",
  "mcp_port": 31337,
  "mcp_timeout_seconds": 20.0,

  // LLM4Decompile（可选）
  "llm4decompile_enabled": false,
  "llm4decompile_base_url": "http://localhost:8080/v1",
  "llm4decompile_model": "llm4decompile-9b-v2"
}
```

---

## 🧪 运行测试

```bash
# 激活虚拟环境后
pytest

# 运行特定测试
pytest tests/test_tool_pipeline.py -v
```

---

## 🤝 贡献

欢迎贡献！无论是修 bug、加新工具、还是改进文档：

1. Fork 本仓库
2. 创建你的分支 (`git checkout -b feature/awesome-tool`)
3. 提交更改 (`git commit -m 'Add awesome tool'`)
4. 推送到远程 (`git push origin feature/awesome-tool`)
5. 创建 Pull Request

### 🛠️ 添加新工具

在 `m2/tools/` 下创建新函数，使用 `@ida_tool` 装饰器即可自动注册：

```python
from m2.tools._decorators import ida_tool

@ida_tool("my_category", concurrency_safe=True)
def my_new_tool(address: str) -> dict:
    """工具描述，LLM 会看到这段文字"""
    # 你的实现
    return {"result": "data"}
```

---

## 📝 依赖

| 包 | 用途 |
|---|------|
| `PyQt5` | GUI 框架 |
| `pyqt-fluent-widgets` | Fluent Design 组件库 |
| `click` | CLI 命令行框架 |
| `rich` | 终端富文本渲染 |
| `prompt-toolkit` | CLI 交互式输入 |
| `mcp` | Model Context Protocol SDK |
| `pytest` | 测试框架 |

---

## 📜 许可证

本项目基于 [GNU General Public License v3.0](LICENSE) 开源。

---

## 🙏 致谢

- [IDA Pro](https://hex-rays.com/ida-pro/) — 逆向工程的黄金标准
- [MCP](https://modelcontextprotocol.io/) — Model Context Protocol，让 LLM 调用工具变得优雅
- [qfluentwidgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) — 精美的 Fluent Design 组件库
- [Rich](https://github.com/Textualize/rich) — 让终端输出不再无聊
- [LLM4Decompile](https://github.com/albertan017/LLM4Decompile) — 二进制分析 LLM 研究

---

<div align="center">

**如果这个项目对你有帮助，给个 ⭐ 吧！**

*Made with 🔬 and ☕*

</div>
