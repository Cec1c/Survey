"""工具钩子系统 — 参考 Claude Code PreToolUse/PostToolUse (第 9 章)。

提供可插拔的前/后拦截器:
- PreToolUse: 修改参数、阻止执行、附加上下文
- PostToolUse: 修改输出、过滤敏感信息
"""

from typing import Any, Callable, Dict, List, Optional, Protocol
from dataclasses import dataclass, field


class PreToolHook(Protocol):
    """前钩子: 拦截 (tool_name, arguments, context) → 允许修改参数或阻止执行"""

    def __call__(
        self, tool_name: str, arguments: Dict[str, Any], context: "HookContext"
    ) -> Optional[Dict[str, Any]]:
        """
        返回:
        - None: 使用原始参数继续
        - 带有 'blocked': True 的 dict: 阻止执行, 返回错误消息
        - 其他 dict: 修改后的参数 (替换原始参数)
        """
        ...


class PostToolHook(Protocol):
    """后钩子: 拦截 (tool_name, arguments, result, context) → 允许修改结果"""

    def __call__(
        self, tool_name: str, arguments: Dict[str, Any],
        result: Any, context: "HookContext",
    ) -> Any:
        """返回修改后的 result。"""
        ...


@dataclass
class HookContext:
    """传递给所有钩子的共享上下文"""
    hypothesis_ids: List[str] = field(default_factory=list)
    phase: str = ""
    tool_call_count: int = 0


class ToolHookChain:
    """按顺序运行的前/后钩子链"""

    def __init__(self):
        self._pre: List[PreToolHook] = []
        self._post: List[PostToolHook] = []

    def add_pre(self, hook: PreToolHook) -> None:
        self._pre.append(hook)

    def add_post(self, hook: PostToolHook) -> None:
        self._post.append(hook)

    def run_pre(
        self, tool_name: str, arguments: Dict[str, Any], context: HookContext,
    ) -> Optional[Dict[str, Any]]:
        """运行所有前钩子。第一个返回 block 的钩子获胜。"""
        for hook in self._pre:
            try:
                r = hook(tool_name, dict(arguments), context)
            except Exception:
                continue  # 钩子异常不阻塞执行

            if isinstance(r, dict) and r.get("blocked"):
                return r  # 停止 — 执行被阻止
            if r is not None:
                arguments = r  # 参数被修改
        return arguments if arguments else None

    def run_post(
        self, tool_name: str, arguments: Dict[str, Any],
        result: Any, context: HookContext,
    ) -> Any:
        """运行所有后钩子, 每个钩子可以修改结果。"""
        for hook in self._post:
            try:
                result = hook(tool_name, arguments, result, context)
            except Exception:
                pass  # 钩子异常不改变结果
        return result


# ── 内置钩子 ────────────────────────────────────────────────────────────

class RenameConflictChecker:
    """前钩子: 阻止会导致命名冲突的重命名操作。

    在调查工具缓存中检查是否存在重复的函数名/变量名。
    """

    def __call__(
        self, tool_name: str, arguments: Dict[str, Any], context: HookContext,
    ) -> Optional[Dict[str, Any]]:
        if tool_name not in (
            "rename_function", "rename_global_variable",
            "rename_local_variable", "rename_stack_frame_variable",
        ):
            return None

        new_name = arguments.get("new_name", "").strip()
        if not new_name:
            return {"blocked": True, "error": "new_name cannot be empty"}

        # 基本验证: 名称不能包含特殊字符
        if not self._is_valid_identifier(new_name):
            return {
                "blocked": True,
                "error": f"'{new_name}' is not a valid C identifier",
            }
        return None

    @staticmethod
    def _is_valid_identifier(name: str) -> bool:
        if not name:
            return False
        if not (name[0].isalpha() or name[0] == "_"):
            return False
        return all(c.isalnum() or c == "_" for c in name)


class AddressValidator:
    """前钩子: 验证地址参数的格式, 拒绝变量名格式的"地址"。"""

    def __call__(
        self, tool_name: str, arguments: Dict[str, Any], context: HookContext,
    ) -> Optional[Dict[str, Any]]:
        for key in ("address", "start_address", "function_address", "memory_address"):
            val = arguments.get(key)
            if val is not None and isinstance(val, str) and val.strip():
                stripped = val.strip()
                # 拒绝变量名格式: dword_xxx, byte_xxx, unk_xxx, loc_xxx
                if "_" in stripped and not stripped.startswith("0x"):
                    return {
                        "blocked": True,
                        "error": (
                            f"'{stripped}' looks like a variable name, not an address. "
                            f"Use list_functions or get_global_value to find "
                            f"the actual hex address, then use that instead."
                        ),
                    }
                try:
                    int(stripped, 0)
                except ValueError:
                    return {
                        "blocked": True,
                        "error": f"Invalid address '{stripped}': must be hex (e.g. 0x401000) or decimal",
                    }
        return None
