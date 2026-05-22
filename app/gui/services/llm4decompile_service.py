"""LLM4Decompile 推理服务封装 (可选模块)

通过 OpenAI-compatible API 调用本地 vLLM 部署的 LLM4Decompile 模型，
优化 IDA/Hex-Rays 反编译伪代码的可读性。

⚠️ 这是一个可选模块，默认关闭。启用需要:
  1. GPU 服务器 (>=16GB VRAM) 运行 vLLM
  2. 下载模型: huggingface-cli download LLM4Binary/llm4decompile-9b-v2
  3. 在 llm_config.json 中设置 "llm4decompile_enabled": true
  4. 配置 llm4decompile_base_url 指向 vLLM 服务地址

支持三种模型:
- V2 Ref: 优化 Ghidra/IDA 伪代码 (llm4decompile-9b-v2, ~18GB)
- SK2Decompile Struct: 结构恢复 Phase 1 (sk2decompile-struct-6.7b)
- SK2Decompile Ident: 标识符命名 Phase 2 (sk2decompile-ident-6.7b)
"""

import json
import urllib.request
import urllib.error
from typing import Any, Dict, Optional


class LLM4DecompileService:
    """封装 LLM4Decompile 推理服务 (OpenAI-compatible API)"""

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        model: str = "llm4decompile-9b-v2",
        timeout: float = 60.0,
        enabled: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.enabled = enabled
        self._available: Optional[bool] = None

    def check_available(self) -> bool:
        """检测 LLM4Decompile 服务是否可用"""
        if not self.enabled:
            self._available = False
            return False
        try:
            url = f"{self.base_url}/models"
            req = urllib.request.Request(url, method="GET")
            resp = urllib.request.urlopen(req, timeout=5)
            self._available = resp.status == 200
        except Exception:
            self._available = False
        return self._available

    @property
    def available(self) -> bool:
        if self._available is None:
            self.check_available()
        return self._available

    def refine_pseudocode(
        self, pseudocode: str, function_name: str = "", original_addr: str = ""
    ) -> Dict[str, Any]:
        """使用 LLM4Decompile V2 Ref 模型优化伪代码

        Args:
            pseudocode: IDA Hex-Rays 原始伪代码
            function_name: 函数名 (用于 prompt)
            original_addr: 函数地址

        Returns:
            {"ok": True, "refined_code": "...", "model": "..."} or {"ok": False, "error": "..."}
        """
        if not self.enabled:
            return {"ok": False, "error": "LLM4Decompile 服务未启用"}

        if not self.available:
            return {"ok": False, "error": "LLM4Decompile 服务不可用，请检查 vLLM 是否运行"}

        if not pseudocode or not pseudocode.strip():
            return {"ok": False, "error": "伪代码为空"}

        # 截断过长的伪代码 (模型上下文限制 8192)
        max_input = 6000
        if len(pseudocode) > max_input:
            pseudocode = pseudocode[:max_input] + "\n... [truncated]"

        prompt = self._build_prompt(pseudocode, function_name)
        result = self._call_llm(prompt)

        if result.get("ok"):
            return {
                "ok": True,
                "refined_code": result["text"],
                "model": self.model,
                "function_name": function_name,
                "address": original_addr,
            }
        return result

    def recover_structure(self, pseudocode: str, function_name: str = "") -> Dict[str, Any]:
        """使用 SK2Decompile Struct 模型恢复代码结构 (Phase 1)

        Args:
            pseudocode: IDA 伪代码
            function_name: 函数名

        Returns:
            {"ok": True, "normalized_ir": "...", "model": "sk2decompile-struct-6.7b"}
        """
        if not self.enabled:
            return {"ok": False, "error": "LLM4Decompile 服务未启用"}
        if not self.available:
            return {"ok": False, "error": "LLM4Decompile 服务不可用"}

        # 预处理伪代码
        normalized = self._normalize_pseudo(pseudocode)
        prompt = self._build_prompt(normalized, function_name)

        saved_model = self.model
        self.model = "sk2decompile-struct-6.7b"
        result = self._call_llm(prompt)
        self.model = saved_model

        if result.get("ok"):
            return {
                "ok": True,
                "normalized_ir": result["text"],
                "model": "sk2decompile-struct-6.7b",
                "function_name": function_name,
            }
        return result

    def recover_identifiers(
        self, normalized_ir: str, struct_model_result: str = ""
    ) -> Dict[str, Any]:
        """使用 SK2Decompile Ident 模型恢复标识符 (Phase 2)

        Args:
            normalized_ir: Phase 1 输出的归一化 IR
            struct_model_result: struct 模型的原始输出 (用于结合 context)

        Returns:
            {"ok": True, "source_code": "...", "model": "sk2decompile-ident-6.7b"}
        """
        if not self.enabled:
            return {"ok": False, "error": "LLM4Decompile 服务未启用"}
        if not self.available:
            return {"ok": False, "error": "LLM4Decompile 服务不可用"}

        prompt = self._build_prompt(normalized_ir, "")

        saved_model = self.model
        self.model = "sk2decompile-ident-6.7b"
        result = self._call_llm(prompt)
        self.model = saved_model

        if result.get("ok"):
            return {
                "ok": True,
                "source_code": result["text"],
                "model": "sk2decompile-ident-6.7b",
            }
        return result

    def refine_two_phase(self, pseudocode: str, function_name: str = "") -> Dict[str, Any]:
        """两阶段优化: 先结构恢复再标识符命名

        Returns:
            {"ok": True, "source_code": "...", "struct_ir": "...", "phase1_model": "...", "phase2_model": "..."}
        """
        phase1 = self.recover_structure(pseudocode, function_name)
        if not phase1.get("ok"):
            return {"ok": False, "error": f"Phase 1 failed: {phase1.get('error')}", "phase1": phase1}

        phase2 = self.recover_identifiers(phase1["normalized_ir"])
        if not phase2.get("ok"):
            return {
                "ok": False,
                "error": f"Phase 2 failed: {phase2.get('error')}",
                "phase1": phase1,
                "struct_ir": phase1["normalized_ir"],
            }

        return {
            "ok": True,
            "source_code": phase2["source_code"],
            "struct_ir": phase1["normalized_ir"],
            "phase1_model": phase1["model"],
            "phase2_model": phase2["model"],
            "function_name": function_name,
        }

    # ── 内部方法 ──────────────────────────────────────────────────────

    def _build_prompt(self, code: str, function_name: str) -> str:
        fn = function_name or "func"
        return f"# This is the assembly code:\n{fn}:\n{code}\n# What is the source code?\n"

    def _call_llm(self, prompt: str) -> Dict[str, Any]:
        """调用 LLM4Decompile API"""
        endpoint = self._chat_endpoint()
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": 4096,
            "temperature": 0.1,
        }

        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        try:
            req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return {"ok": False, "error": f"LLM4Decompile HTTP {exc.code}: {detail[:500]}"}
        except urllib.error.URLError as exc:
            return {"ok": False, "error": f"LLM4Decompile 连接失败: {exc.reason}"}
        except Exception as exc:
            return {"ok": False, "error": f"LLM4Decompile 调用异常: {exc}"}

        try:
            choices = data.get("choices") or []
            if not choices:
                return {"ok": False, "error": "LLM4Decompile 返回空 choices"}
            text = choices[0].get("message", {}).get("content", "")
            if not text:
                text = choices[0].get("text", "")
            return {"ok": True, "text": text.strip()}
        except Exception as exc:
            return {"ok": False, "error": f"LLM4Decompile 解析响应失败: {exc}"}

    def _chat_endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _normalize_pseudo(self, pseudocode: str) -> str:
        """预处理伪代码 (参考 sk2decompile/evaluation/normalize_pseudo.py)"""
        import re

        text = pseudocode

        # 移除 __fastcall, __cdecl, __stdcall 等调用约定
        text = re.sub(r"__(fastcall|cdecl|stdcall|thiscall|usercall|userpurge)\b", "", text)

        # 移除 __ptr32, __ptr64
        text = re.sub(r"__(ptr32|ptr64)\b", "", text)

        # 常见类型映射 (IDA 特有类型 → 标准 C 类型)
        type_map = {
            r"\b_QWORD\b": "uint64_t",
            r"\b_DWORD\b": "uint32_t",
            r"\b_WORD\b": "uint16_t",
            r"\b_BYTE\b": "uint8_t",
            r"\b_BOOL\b": "bool",
            r"\b_QWORD_PTR\b": "uint64_t *",
            r"\b_DWORD_PTR\b": "uint32_t *",
            r"\b_LONG\b": "int32_t",
            r"\b_ULONG\b": "uint32_t",
            r"\b_LONGLONG\b": "int64_t",
            r"\b_ULARGE_INTEGER\b": "uint64_t",
            r"\b_LARGE_INTEGER\b": "int64_t",
        }
        for pattern, replacement in type_map.items():
            text = re.sub(pattern, replacement, text)

        # 移除注释
        text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

        return text.strip()
