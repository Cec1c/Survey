"""ToolManifest 纯函数测试 — 工具查询、schema 生成、安全并发判断"""

import pytest
from app.gui.services.tool_manifest import ToolManifest


class TestActiveTools:
    def test_default_tools_present(self):
        m = ToolManifest()
        names = m.get_names()
        assert "decompile_function" in names
        assert "disassemble_function" in names
        assert "get_xrefs_to" in names
        assert "rename_function" in names
        assert len(names) > 20  # 默认注册了大量工具

    def test_enable_ext_group_adds_debug_tools(self):
        m = ToolManifest()
        default_count = len(m)
        m.enable_ext_group("dbg")
        assert len(m) > default_count
        names = m.get_names()
        assert any("debug" in n.lower() or "breakpoint" in n.lower() for n in names)


class TestConcurrencySafety:
    def test_read_tools_are_safe(self):
        m = ToolManifest()
        assert m.is_concurrency_safe("decompile_function")
        assert m.is_concurrency_safe("get_xrefs_to")
        assert m.is_concurrency_safe("disassemble_function")

    def test_write_tools_are_not_safe(self):
        m = ToolManifest()
        assert not m.is_concurrency_safe("rename_function")
        assert not m.is_concurrency_safe("set_comment")

    def test_unknown_tool_defaults_to_safe(self):
        m = ToolManifest()
        assert m.is_concurrency_safe("nonexistent_tool_xyz")


class TestSchemas:
    def test_schema_has_openai_format(self):
        m = ToolManifest()
        schemas = m.get_schemas()
        assert len(schemas) > 0
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "description" in s["function"]
            assert "parameters" in s["function"]

    def test_deferred_schema_minimal(self):
        """延迟模式下 schema 仅含名称和空参数"""
        m = ToolManifest()
        schemas = m.get_schemas(deferred_ok=True)
        # 至少有一个 deferred 工具
        deferred_names = set()
        for s in schemas:
            if not s["function"]["parameters"].get("properties"):
                deferred_names.add(s["function"]["name"])
        # deferred 工具集不应为空
        assert len(deferred_names) > 0

    def test_resolve_deferred_returns_full(self):
        m = ToolManifest()
        full = m.resolve_deferred("decompile_function")
        assert full is not None
        assert "parameters" in full["function"]
        assert full["function"]["parameters"].get("properties")


class TestCategory:
    def test_get_category_known(self):
        m = ToolManifest()
        assert m.get_category("decompile_function") in ("analysis", "default")

    def test_get_category_unknown(self):
        m = ToolManifest()
        assert m.get_category("made_up_tool") == "default"
