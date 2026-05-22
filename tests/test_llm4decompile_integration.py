"""LLM4Decompile 集成测试

测试内容:
1. LLM4DecompileService 基本功能 (无 vLLM 时的 graceful degradation)
2. LLM4DecompileService 伪代码预处理
3. AgentChatService 工具路由 (llm4decompile_refine 路由到本地服务)
4. LLMConfig 配置加载/保存 (含新字段)
5. CLI 命令正常启动
"""

import json
import os
import sys
import tempfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def test_llm4decompile_service_disabled():
    """测试：服务禁用时优雅降级"""
    from app.gui.services.llm4decompile_service import LLM4DecompileService

    svc = LLM4DecompileService(enabled=False)
    assert not svc.check_available(), "Disabled service should report unavailable"

    result = svc.refine_pseudocode("int test() { return 0; }", "test")
    assert not result["ok"], f"Disabled service should return failure: {result}"
    assert "未启用" in result.get("error", ""), f"Error should mention disabled: {result}"
    print("[PASS] test_llm4decompile_service_disabled")


def test_llm4decompile_service_unreachable():
    """测试：vLLM 不可达时优雅降级"""
    from app.gui.services.llm4decompile_service import LLM4DecompileService

    svc = LLM4DecompileService(
        base_url="http://127.0.0.1:19999/v1",  # 不可能存在的端口
        enabled=True,
        timeout=2.0,
    )
    assert not svc.check_available(), "Unreachable service should report unavailable"

    result = svc.refine_pseudocode("int test() { return 0; }", "test")
    assert not result["ok"], f"Unreachable service should return failure: {result}"
    assert "不可用" in result.get("error", "") or "连接失败" in result.get("error", ""), \
        f"Error should mention connection issue: {result}"
    print("[PASS] test_llm4decompile_service_unreachable")


def test_llm4decompile_service_empty_input():
    """测试：空伪代码输入 (mock 可用性绕过连接检查)"""
    from app.gui.services.llm4decompile_service import LLM4DecompileService

    svc = LLM4DecompileService(enabled=True)
    # Mock _available 为 True 绕过连接检查
    svc._available = True
    result = svc.refine_pseudocode("", "test")
    assert not result["ok"], f"Empty input should return failure, got: {result}"
    assert "为空" in result.get("error", ""), f"Error should mention empty: {result}"
    print("[PASS] test_llm4decompile_service_empty_input")


def test_normalize_pseudo():
    """测试：伪代码预处理（类型映射）"""
    from app.gui.services.llm4decompile_service import LLM4DecompileService

    svc = LLM4DecompileService(enabled=False)
    pseudo = """
    __fastcall __int64 func0(__int64 a1, _DWORD *a2)
    {
        _QWORD v3;
        _BYTE v4;
        return v3;
    }
    """
    normalized = svc._normalize_pseudo(pseudo)
    assert "__fastcall" not in normalized, f"__fastcall should be removed: {normalized}"
    assert "_QWORD" not in normalized, f"_QWORD should be replaced: {normalized}"
    assert "uint64_t" in normalized, f"uint64_t should appear: {normalized}"
    assert "uint32_t" in normalized, f"uint32_t should appear (for _DWORD): {normalized}"
    assert "uint8_t" in normalized, f"uint8_t should appear (for _BYTE): {normalized}"
    print(f"[PASS] test_normalize_pseudo\n  Original: {pseudo.strip()[:80]}...\n  Normalized: {normalized.strip()[:80]}...")


def test_prompt_building():
    """测试：prompt 构建"""
    from app.gui.services.llm4decompile_service import LLM4DecompileService

    svc = LLM4DecompileService(enabled=False)
    prompt = svc._build_prompt("push rbp\nmov rbp, rsp\nret", "my_func")
    assert "my_func" in prompt, f"Function name should be in prompt: {prompt}"
    assert "# This is the assembly code:" in prompt, f"Standard template should be used: {prompt}"
    assert "# What is the source code?" in prompt, f"Standard template should be used: {prompt}"
    print(f"[PASS] test_prompt_building\n  Prompt:\n{prompt}")


def test_llm_config_new_fields():
    """测试：LLMConfig 新字段的加载和保存"""
    from app.gui.state.llm_config import LLMConfig

    # 测试默认值 (现在默认为 False — 可选模块)
    c = LLMConfig()
    assert c.llm4decompile_enabled is False, "Should be disabled by default"
    assert c.llm4decompile_base_url == "http://localhost:8080/v1"
    assert c.llm4decompile_model == "llm4decompile-9b-v2"
    assert c.llm4decompile_timeout == 60.0

    # 测试从 JSON 加载
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({
            "llm4decompile_enabled": False,
            "llm4decompile_base_url": "http://gpu-server:9999/v1",
            "llm4decompile_model": "llm4decompile-6.7b-v2",
            "llm4decompile_timeout": 120.0,
        }, f)
        tmp_path = f.name

    try:
        c2 = LLMConfig.load(tmp_path)
        assert c2.llm4decompile_enabled is False
        assert c2.llm4decompile_base_url == "http://gpu-server:9999/v1"
        assert c2.llm4decompile_model == "llm4decompile-6.7b-v2"
        assert c2.llm4decompile_timeout == 120.0
    finally:
        os.unlink(tmp_path)

    # 测试保存
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        save_path = f.name

    try:
        c3 = LLMConfig(llm4decompile_enabled=False)
        c3.save(save_path)
        with open(save_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved.get("llm4decompile_enabled") is False
        assert "llm4decompile_base_url" in saved
    finally:
        os.unlink(save_path)

    print("[PASS] test_llm_config_new_fields")


def test_config_json_file():
    """测试：实际配置文件包含新字段"""
    config_path = os.path.join(_PROJECT_ROOT, "app", "config", "llm_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "llm4decompile_enabled" in data, "Config JSON missing llm4decompile_enabled"
    assert "llm4decompile_base_url" in data, "Config JSON missing llm4decompile_base_url"
    assert "llm4decompile_model" in data, "Config JSON missing llm4decompile_model"
    assert "llm4decompile_timeout" in data, "Config JSON missing llm4decompile_timeout"
    print(f"[PASS] test_config_json_file\n  Config has all llm4decompile fields")


def test_agent_service_has_llm4decompile():
    """测试：AgentChatService 正确初始化 LLM4DecompileService (默认关闭)"""
    from app.gui.state.llm_config import LLMConfig
    from app.gui.services.mcp_service import MCPService
    from app.gui.services.chat_service import AgentChatService

    config = LLMConfig()
    config.use_ida_tools = False
    mcp = MCPService(host="127.0.0.1", port=31337, timeout_seconds=5.0)
    agent = AgentChatService(config=config, mcp_service=mcp)

    assert hasattr(agent, "llm4decompile_service"), "Agent should have llm4decompile_service"
    assert agent.llm4decompile_service is not None, "llm4decompile_service should not be None"
    assert agent.llm4decompile_service.enabled is False, "llm4decompile should be disabled by default"
    print("[PASS] test_agent_service_has_llm4decompile")


def test_tool_schemas_llm4decompile_disabled_by_default():
    """测试：默认关闭时 _tool_schemas() 不包含 llm4decompile_refine"""
    from app.gui.state.llm_config import LLMConfig
    from app.gui.services.mcp_service import MCPService
    from app.gui.services.chat_service import AgentChatService

    config = LLMConfig()
    mcp = MCPService(host="127.0.0.1", port=31337, timeout_seconds=2.0)
    agent = AgentChatService(config=config, mcp_service=mcp)

    schemas = agent._tool_schemas()
    tool_names = [s["function"]["name"] for s in schemas]
    assert "llm4decompile_refine" not in tool_names, \
        f"llm4decompile_refine should NOT be in tool schemas when disabled: {tool_names}"
    print(f"[PASS] test_tool_schemas_llm4decompile_disabled_by_default\n  Available tools: {tool_names}")


def test_tool_schemas_llm4decompile_enabled():
    """测试：启用时 _tool_schemas() 包含 llm4decompile_refine（需 mock 可用性）"""
    from app.gui.state.llm_config import LLMConfig
    from app.gui.services.mcp_service import MCPService
    from app.gui.services.chat_service import AgentChatService

    config = LLMConfig()
    config.llm4decompile_enabled = True
    mcp = MCPService(host="127.0.0.1", port=31337, timeout_seconds=2.0)
    agent = AgentChatService(config=config, mcp_service=mcp)
    # mock 可用性
    agent.llm4decompile_service._available = True

    schemas = agent._tool_schemas()
    tool_names = [s["function"]["name"] for s in schemas]
    assert "llm4decompile_refine" in tool_names, \
        f"llm4decompile_refine should be in tool schemas when enabled and available: {tool_names}"
    print(f"[PASS] test_tool_schemas_llm4decompile_enabled\n  Available tools: {tool_names}")


def test_llm4decompile_refine_no_ida():
    """测试：无 IDA 连接时 llm4decompile_refine 的 graceful handling"""
    from app.gui.state.llm_config import LLMConfig
    from app.gui.services.mcp_service import MCPService
    from app.gui.services.chat_service import AgentChatService

    config = LLMConfig()
    config.use_ida_tools = False
    config.llm4decompile_enabled = True
    mcp = MCPService(host="127.0.0.1", port=31337, timeout_seconds=2.0)
    agent = AgentChatService(config=config, mcp_service=mcp)

    # 模拟调用 llm4decompile_refine (会先尝试 IDA 反编译，然后失败)
    result = agent._execute_llm4decompile_refine({"address": "0x401000"})
    assert not result["ok"], f"Should fail without IDA: {result}"
    assert "decompile" in result.get("error", "").lower(), f"Should mention decompile failure: {result}"
    print(f"[PASS] test_llm4decompile_refine_no_ida\n  Error: {result.get('error')}")


def test_llm4decompile_refine_missing_address():
    """测试：缺少 address 参数时的处理"""
    from app.gui.state.llm_config import LLMConfig
    from app.gui.services.mcp_service import MCPService
    from app.gui.services.chat_service import AgentChatService

    config = LLMConfig()
    mcp = MCPService(host="127.0.0.1", port=31337, timeout_seconds=2.0)
    agent = AgentChatService(config=config, mcp_service=mcp)

    result = agent._execute_llm4decompile_refine({})
    assert not result["ok"], f"Should fail without address: {result}"
    print(f"[PASS] test_llm4decompile_refine_missing_address\n  Error: {result.get('error')}")


def test_service_init_export():
    """测试：__init__.py 正确导出 LLM4DecompileService"""
    from app.gui.services import LLM4DecompileService
    svc = LLM4DecompileService(enabled=False)
    assert svc is not None
    print("[PASS] test_service_init_export")


if __name__ == "__main__":
    print("=" * 60)
    print("LLM4Decompile 集成测试套件")
    print("=" * 60)
    print()

    tests = [
        ("LLM4DecompileService 禁用降级", test_llm4decompile_service_disabled),
        ("LLM4DecompileService 不可达降级", test_llm4decompile_service_unreachable),
        ("LLM4DecompileService 空输入", test_llm4decompile_service_empty_input),
        ("伪代码预处理", test_normalize_pseudo),
        ("Prompt 构建", test_prompt_building),
        ("LLMConfig 新字段", test_llm_config_new_fields),
        ("配置文件完整性", test_config_json_file),
        ("AgentChatService 初始化", test_agent_service_has_llm4decompile),
        ("Tool Schemas 默认不含 LLM4D", test_tool_schemas_llm4decompile_disabled_by_default),
        ("Tool Schemas 启用后含 LLM4D", test_tool_schemas_llm4decompile_enabled),
        ("llm4decompile_refine 无 IDA", test_llm4decompile_refine_no_ida),
        ("llm4decompile_refine 缺参数", test_llm4decompile_refine_missing_address),
        ("Service 导出", test_service_init_export),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()

    print()
    print("=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
