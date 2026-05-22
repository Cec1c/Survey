# Skills 目录

这个目录用于存放自定义的Skill文件，每个Skill定义特定的功能或工作流程。

## Skill 文件格式

每个Skill文件使用Markdown格式，包含：

### Frontmatter (必需)

```yaml
---
name: skill_name          # Skill的标识符
description: 简短描述     # Skill的用途说明
type: category            # Skill的分类
---
```

### 内容

Skill的主要内容应该包含：
- 具体的执行步骤
- 预期的输入和输出
- 使用说明
- 示例场景

## 内置 Skills

系统预置了以下Skills：

### 开发类
- **Claude API**: 构建和优化Claude API应用
- **代码审查**: 审查Pull Request
- **项目初始化**: 初始化CLAUDE.md文件
- **代码简化**: 优化代码质量和效率

### 安全类
- **安全审查**: 执行安全审计

### 自动化类
- **循环任务**: 设置循环执行的任务

## 添加自定义 Skill

1. 在此目录创建新的Markdown文件
2. 添加必要的frontmatter
3. 描述Skill的功能和使用方法
4. 在应用界面的Skills页面中启用

## Skill 分类

- **Development**: 开发相关工具
- **Security**: 安全相关工具
- **Automation**: 自动化工具
- **General**: 通用工具