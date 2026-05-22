"""LLMConfig 加载/保存/规范化测试"""

import json
import os
import tempfile
import pytest
from app.gui.state.llm_config import LLMConfig


# ═══════════════════════════════════════════════════════════════════
# load
# ═══════════════════════════════════════════════════════════════════

class TestLoad:
    def test_load_valid_config(self):
        config = LLMConfig.load("app/config/llm_config.json")
        assert config.model == "deepseek-v4-pro"
        assert "deepseek" in config.base_url
        assert config.api_key.startswith("sk-")

    def test_load_missing_file_returns_defaults(self):
        config = LLMConfig.load("nonexistent_file_12345.json")
        assert config.model == "gpt-4o-mini"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.api_key == ""

    def test_load_malformed_json_returns_defaults(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        config = LLMConfig.load(str(bad))
        assert config.model == "gpt-4o-mini"  # graceful fallback

    def test_load_with_unknown_fields_does_not_crash(self, tmp_path):
        """JSON 中包含 dataclass 未定义的字段不应导致崩溃（曾导致静默回退到默认值的 bug）"""
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({
            "base_url": "https://custom.api.com/v1",
            "api_key": "sk-test",
            "model": "custom-model",
            "unknown_field_1": "should be ignored",
            "unknown_field_2": 12345,
            "nested_unknown": {"a": 1, "b": 2},
        }), encoding="utf-8")
        config = LLMConfig.load(str(cfg))
        assert config.model == "custom-model"
        assert config.base_url == "https://custom.api.com/v1"
        assert config.api_key == "sk-test"


# ═══════════════════════════════════════════════════════════════════
# save + reload roundtrip
# ═══════════════════════════════════════════════════════════════════

class TestSaveReload:
    def test_roundtrip_preserves_all_fields(self, tmp_path):
        config = LLMConfig()
        config.model = "test-model-v2"
        config.base_url = "https://test.api.com/v1"
        config.api_key = "sk-roundtrip-test"
        config.temperature = 0.7
        config.mcp_host = "10.0.0.1"
        config.mcp_port = 9999
        config.use_ida_tools = True
        config.skills_enabled = False

        save_path = tmp_path / "roundtrip.json"
        config.save(str(save_path))

        loaded = LLMConfig.load(str(save_path))
        assert loaded.model == "test-model-v2"
        assert loaded.base_url == "https://test.api.com/v1"
        assert loaded.api_key == "sk-roundtrip-test"
        assert loaded.temperature == 0.7
        assert loaded.mcp_host == "10.0.0.1"
        assert loaded.mcp_port == 9999
        assert loaded.use_ida_tools is True
        assert loaded.skills_enabled is False

    def test_roundtrip_agent_workflow_fields(self, tmp_path):
        config = LLMConfig()
        config.agent_enable_planning = False
        config.agent_max_execute_rounds = 10
        config.agent_compaction_threshold_chars = 5000

        save_path = tmp_path / "agent_cfg.json"
        config.save(str(save_path))

        loaded = LLMConfig.load(str(save_path))
        assert loaded.agent_enable_planning is False
        assert loaded.agent_max_execute_rounds == 10
        assert loaded.agent_compaction_threshold_chars == 5000

    def test_save_creates_parent_dirs(self, tmp_path):
        config = LLMConfig()
        deep = tmp_path / "a" / "b" / "cfg.json"
        config.save(str(deep))
        assert deep.exists()


# ═══════════════════════════════════════════════════════════════════
# _normalize_base_url
# ═══════════════════════════════════════════════════════════════════

from app.gui.pages.task_page import TaskPage


class TestNormalizeBaseUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://api.deepseek.com/v1", "https://api.deepseek.com"),
        ("https://api.deepseek.com", "https://api.deepseek.com"),
        ("https://api.deepseek.com/", "https://api.deepseek.com"),
        ("https://api.openai.com/v1/", "https://api.openai.com"),
        ("https://api.openai.com/v1", "https://api.openai.com"),
        ("https://api.openai.com", "https://api.openai.com"),
        ("http://localhost:8080/v1", "http://localhost:8080"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("https://generativelanguage.googleapis.com/v1beta", "https://generativelanguage.googleapis.com/v1beta"),
    ])
    def test_normalize(self, url, expected):
        assert TaskPage._normalize_base_url(url) == expected
