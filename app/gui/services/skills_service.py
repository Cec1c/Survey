import os
import re
import json
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class Skill:
    """单个Skill的数据结构"""
    id: str
    name: str
    description: str
    category: str
    content: str
    enabled: bool = True
    file_path: Optional[str] = None


class SkillsService:
    """Skills管理服务，负责加载、管理和应用Skills"""

    def __init__(self, skills_directory: str = "skills"):
        self.skills_directory = skills_directory
        self._skills: Dict[str, Skill] = {}
        self._enabled_skills: List[str] = []

    def load_skills(self) -> Dict[str, Skill]:
        """加载所有Skills"""
        self._skills = {}
        self._enabled_skills = []

        if not os.path.exists(self.skills_directory):
            return self._skills

        try:
            # 扫描Skills目录
            for item in os.listdir(self.skills_directory):
                item_path = os.path.join(self.skills_directory, item)

                # 检查目录形式的Skill（如idapython）
                if os.path.isdir(item_path):
                    skill_file = os.path.join(item_path, "SKILL.md")
                    if os.path.exists(skill_file):
                        skill = self._parse_skill_file(skill_file, item)
                        if skill:
                            self._skills[skill.id] = skill
                            if skill.enabled:
                                self._enabled_skills.append(skill.id)

                # 检查.md文件形式的Skill
                elif item.endswith('.md') and item != 'README.md':
                    skill = self._parse_skill_file(item_path, item.replace('.md', ''))
                    if skill:
                        self._skills[skill.id] = skill
                        if skill.enabled:
                            self._enabled_skills.append(skill.id)

        except Exception as e:
            print(f"加载Skills时出错: {e}")

        return self._skills

    def _parse_skill_file(self, file_path: str, skill_id: str) -> Optional[Skill]:
        """解析Skill文件"""
        try:
            # 检查文件大小
            if os.path.getsize(file_path) > 1024 * 1024:  # 限制为1MB
                return None

            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # 限制内容长度
            if len(content) > 100 * 1024:  # 限制为100KB
                content = content[:100 * 1024]

            # 解析frontmatter
            frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
            if frontmatter_match:
                frontmatter_text = frontmatter_match.group(1)
                frontmatter_data = self._parse_yaml_like(frontmatter_text)

                name = frontmatter_data.get('name', skill_id)
                description = frontmatter_data.get('description', '无描述')
                category = frontmatter_data.get('category', 'General')

                # 只保存Skill内容部分（不包括frontmatter）
                content_start = frontmatter_match.end()
                skill_content = content[content_start:]

                return Skill(
                    id=skill_id,
                    name=name,
                    description=description,
                    category=category,
                    content=skill_content.strip(),
                    file_path=file_path,
                    enabled=True
                )
            else:
                return Skill(
                    id=skill_id,
                    name=skill_id.replace('_', ' ').title(),
                    description="自定义Skill",
                    category="Custom",
                    content=content,
                    file_path=file_path,
                    enabled=True
                )
        except Exception as e:
            print(f"解析Skill文件 {file_path} 时出错: {e}")
            return None

    def _parse_yaml_like(self, text: str) -> dict:
        """简单的YAML解析器"""
        result = {}
        for line in text.split('\n'):
            line = line.strip()
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip().strip('"\'')
                result[key] = value
        return result

    def get_enabled_skills_content(self) -> str:
        """获取所有启用Skills的内容，用于集成到system prompt"""
        if not self._enabled_skills:
            return ""

        skills_content = []
        skills_content.append("\n## 可用的Skills\n")

        for skill_id in self._enabled_skills:
            if skill_id in self._skills:
                skill = self._skills[skill_id]
                skills_content.append(f"### {skill.name} ({skill.category})")
                skills_content.append(f"{skill.description}")
                skills_content.append("")

                # 添加Skill内容（限制长度）
                skill_text = skill.content
                if len(skill_text) > 2000:  # 限制为2000字符
                    skill_text = skill_text[:2000] + "\n... (内容已截断)"

                skills_content.append(skill_text)
                skills_content.append("\n---\n")

        return "\n".join(skills_content)

    def get_skills_by_category(self, category: str) -> List[Skill]:
        """按分类获取Skills"""
        return [skill for skill in self._skills.values() if skill.category == category]

    def get_skill_by_id(self, skill_id: str) -> Optional[Skill]:
        """根据ID获取Skill"""
        return self._skills.get(skill_id)

    def search_skills(self, query: str) -> List[Skill]:
        """搜索Skills"""
        query_lower = query.lower()
        return [
            skill for skill in self._skills.values()
            if (query_lower in skill.name.lower() or
                query_lower in skill.description.lower() or
                query_lower in skill.content.lower())
        ]

    def get_relevant_skills(self, user_query: str) -> str:
        """根据用户查询获取相关的Skills内容"""
        relevant_skills = self.search_skills(user_query)

        if not relevant_skills:
            return ""

        skills_content = []
        skills_content.append(f"\n## 相关的Skills\n")

        for skill in relevant_skills[:3]:  # 最多返回3个相关Skills
            skills_content.append(f"### {skill.name}")
            skills_content.append(f"{skill.description}")
            skills_content.append("")

            # 添加关键内容（更短的版本）
            skill_text = skill.content
            if len(skill_text) > 1000:  # 限制为1000字符
                skill_text = skill_text[:1000] + "\n... (内容已截断)"

            skills_content.append(skill_text)
            skills_content.append("\n---\n")

        return "\n".join(skills_content)

    def enable_skill(self, skill_id: str) -> bool:
        """启用Skill"""
        if skill_id in self._skills:
            if skill_id not in self._enabled_skills:
                self._enabled_skills.append(skill_id)
            return True
        return False

    def disable_skill(self, skill_id: str) -> bool:
        """禁用Skill"""
        if skill_id in self._enabled_skills:
            self._enabled_skills.remove(skill_id)
            return True
        return False

    def is_skill_enabled(self, skill_id: str) -> bool:
        """检查Skill是否启用"""
        return skill_id in self._enabled_skills

    def get_all_skills(self) -> Dict[str, Skill]:
        """获取所有Skills"""
        return self._skills

    def get_enabled_skills(self) -> List[Skill]:
        """获取所有启用的Skills"""
        return [self._skills[skill_id] for skill_id in self._enabled_skills if skill_id in self._skills]