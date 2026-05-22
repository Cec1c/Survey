"""测试Skills扫描和解析功能"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from PyQt5.QtWidgets import QApplication
from app.gui.state.llm_config import LLMConfig
from app.gui.pages.skills_page import SkillsPage

def test_skills_scanning():
    app = QApplication(sys.argv)

    # 测试配置
    config_path = "app/config/llm_config.json"
    config = LLMConfig.load(config_path)

    print("=== Skills扫描测试 ===")
    print("完成 skills 扫描测试")

    # 创建Skills页面
    skills_page = SkillsPage(config, config_path)

    # 测试目录扫描功能
    print("测试skills目录扫描...")
    directory_skills = skills_page._scan_skills_directory()

    print(f"扫描到 {len(directory_skills)} 个skills:")
    for skill in directory_skills:
        print(f"- {skill['name']} ({skill['id']}) - {skill['category']}")
        print(f"  描述: {skill['description'][:50]}...")
        if 'file_path' in skill:
            print(f"  文件: {skill['file_path']}")
        print()

    # 测试skill文件解析
    print("=== 测试idapython skill解析 ===")
    idapython_path = "skills/idapython/SKILL.md"
    if os.path.exists(idapython_path):
        parsed_skill = skills_page._parse_skill_file(idapython_path, "idapython")
        if parsed_skill:
            print(f"成功解析idapython skill:")
            print(f"  名称: {parsed_skill['name']}")
            print(f"  描述: {parsed_skill['description'][:100]}...")
            print(f"  分类: {parsed_skill['category']}")
            print(f"  内容长度: {len(parsed_skill.get('content', ''))}")
        else:
            print("解析idapython skill失败")
    else:
        print(f"idapython skill文件不存在: {idapython_path}")

    # 测试普通markdown文件解析
    print("\n=== 测试普通markdown skill解析 ===")
    code_review_path = "skills/code_review.md"
    if os.path.exists(code_review_path):
        parsed_skill = skills_page._parse_skill_file(code_review_path, "code_review")
        if parsed_skill:
            print(f"成功解析code_review skill:")
            print(f"  名称: {parsed_skill['name']}")
            print(f"  描述: {parsed_skill['description'][:100]}...")
            print(f"  分类: {parsed_skill['category']}")
        else:
            print("解析code_review skill失败")
    else:
        print(f"code_review skill文件不存在: {code_review_path}")

    # 测试YAML解析
    print("\n=== 测试YAML解析 ===")
    test_yaml = """
name: test_skill
description: 这是一个测试skill
category: Test
custom_field: custom_value
"""
    parsed = skills_page._parse_yaml_like(test_yaml)
    print(f"解析结果: {parsed}")

    print("\n=== 所有测试完成 ===")

if __name__ == "__main__":
    test_skills_scanning()