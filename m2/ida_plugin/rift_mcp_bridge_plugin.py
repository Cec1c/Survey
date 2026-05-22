import hashlib
import json
import socket
import threading
import traceback
from typing import Any, Callable, Dict, List

import idaapi
import idautils
import idc

try:
    import ida_hexrays
except Exception:
    ida_hexrays = None

def _optional_import(name: str):
    try:
        return __import__(name)
    except Exception:
        return None


ida_bytes = _optional_import("ida_bytes")
ida_funcs = _optional_import("ida_funcs")
ida_ida = _optional_import("ida_ida")
ida_name = _optional_import("ida_name")
ida_nalt = _optional_import("ida_nalt")
ida_struct = _optional_import("ida_struct")
ida_typeinf = _optional_import("ida_typeinf")
ida_ua = _optional_import("ida_ua")
ida_frame = _optional_import("ida_frame")


def _hex(ea: int) -> str:
    return hex(ea) if ea != idc.BADADDR else "0x0"


class RpcError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class SurveyIdaBridgeServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 31337):
        self.host = host
        self.port = port
        self._stop = threading.Event()
        self._thread = None
        self._server_socket = None
        self.methods: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "check_connection": self.check_connection,
            "get_metadata": self.get_metadata,
            "get_current_address": self.get_current_address,
            "get_current_function": self.get_current_function,
            "list_functions": self.list_functions,
            "list_functions_filter": self.list_functions_filter,
            "get_function_by_address": self.get_function_by_address,
            "get_function_by_name": self.get_function_by_name,
            "decompile_function": self.decompile_function,
            "disassemble_function": self.disassemble_function,
            "get_entry_points": self.get_entry_points,
            "get_callers": self.get_callers,
            "get_callees": self.get_callees,
            "get_xrefs_to": self.get_xrefs_to,
            "get_xrefs_to_field": self.get_xrefs_to_field,
            "list_globals": self.list_globals,
            "list_globals_filter": self.list_globals_filter,
            "get_global_variable_value_at_address": self.get_global_variable_value_at_address,
            "get_global_variable_value_by_name": self.get_global_variable_value_by_name,
            "list_imports": self.list_imports,
            "list_strings": self.list_strings,
            "list_strings_filter": self.list_strings_filter,
            "get_defined_structures": self.get_defined_structures,
            "search_structures": self.search_structures,
            "get_struct_info_simple": self.get_struct_info_simple,
            "analyze_struct_detailed": self.analyze_struct_detailed,
            "get_struct_at_address": self.get_struct_at_address,
            "list_local_types": self.list_local_types,
            "declare_c_type": self.declare_c_type,
            "get_stack_frame_variables": self.get_stack_frame_variables,
            "create_stack_frame_variable": self.create_stack_frame_variable,
            "rename_stack_frame_variable": self.rename_stack_frame_variable,
            "set_stack_frame_variable_type": self.set_stack_frame_variable_type,
            "delete_stack_frame_variable": self.delete_stack_frame_variable,
            "rename_function": self.rename_function,
            "rename_global_variable": self.rename_global_variable,
            "rename_local_variable": self.rename_local_variable,
            "set_comment": self.set_comment,
            "set_function_prototype": self.set_function_prototype,
            "set_global_variable_type": self.set_global_variable_type,
            "set_local_variable_type": self.set_local_variable_type,
            "data_read_byte": self.data_read_byte,
            "data_read_word": self.data_read_word,
            "data_read_dword": self.data_read_dword,
            "data_read_qword": self.data_read_qword,
            "data_read_string": self.data_read_string,
            "read_memory_bytes": self.read_memory_bytes,
            "patch_address_assembles": self.patch_address_assembles,
            # ── Python 工具名别名 (ToolPipeline 直接用工具名调用，绕过 _call() 翻译) ──
            "read_string": self.data_read_string,
            "read_bytes": self.read_memory_bytes,
            "read_integer": self.data_read_dword,
            "patch_asm": self.patch_address_assembles,
            "get_struct_info": self.get_struct_info_simple,
            "get_global_value": self.get_global_variable_value_at_address,
            "convert_number": self.convert_number,
            # ── 合并工具 (新增) ──
            "get_function": self.get_function,
            "get_global_variable_value": self.get_global_variable_value,
            # ── Python 执行 (新增) ──
            "execute_python": self.execute_python,
            # ── 调试工具 (新增, 来自 api_debug.py 移植) ──
            "debug_start": self.debug_start,
            "debug_exit": self.debug_exit,
            "debug_continue": self.debug_continue,
            "debug_step_into": self.debug_step_into,
            "debug_step_over": self.debug_step_over,
            "debug_run_to": self.debug_run_to,
            "debug_list_breakpoints": self.debug_list_breakpoints,
            "debug_add_breakpoint": self.debug_add_breakpoint,
            "debug_delete_breakpoint": self.debug_delete_breakpoint,
            "debug_get_registers": self.debug_get_registers,
            "debug_get_stacktrace": self.debug_get_stacktrace,
            "debug_read_memory": self.debug_read_memory,
            "debug_write_memory": self.debug_write_memory,
        }

    @staticmethod
    def _log(text: str) -> None:
        try:
            idaapi.msg(f"[survey-ida-mcp-bridge] {text}\n")
        except Exception:
            pass

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rift-ida-mcp-bridge", daemon=True)
        self._thread.start()
        self._log(f"thread started on {self.host}:{self.port}")

    def stop(self):
        self._stop.set()
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
        self._log("stop requested")

    def _run(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket = server
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(8)
            server.settimeout(0.5)
            self._log("socket listening")
        except Exception as exc:
            self._log(f"fatal startup error: {exc}")
            return

        while not self._stop.is_set():
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                self._log(f"accept loop stopping: {exc}")
                break
            except Exception as exc:
                self._log(f"accept error: {exc}")
                continue

            try:
                with conn:
                    data = b""
                    while True:
                        try:
                            part = conn.recv(8192)
                        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError) as exc:
                            self._log(f"recv aborted from {addr}: {exc}")
                            data = b""
                            break
                        if not part:
                            break
                        data += part

                    if not data:
                        continue

                    try:
                        response = self._handle_request(data.decode("utf-8"))
                    except Exception as exc:
                        self._log(f"handle_request error: {exc}")
                        self._log(traceback.format_exc())
                        continue

                    try:
                        conn.sendall(response.encode("utf-8"))
                    except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError, OSError) as exc:
                        self._log(f"send aborted to {addr}: {exc}")
                        continue
            except Exception as exc:
                self._log(f"connection loop error: {exc}")
                self._log(traceback.format_exc())
                continue

        try:
            server.close()
        except Exception:
            pass
        self._log("thread exited")

    def _handle_request(self, raw: str) -> str:
        try:
            req = json.loads(raw.strip())
            method = req.get("method")
            params = req.get("params", {})
            req_id = req.get("id")
            if method not in self.methods:
                raise RpcError("METHOD_NOT_FOUND", str(method))
            result = self._execute_on_main_thread(lambda: self.methods[method](params))
            return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}, ensure_ascii=False)
        except RpcError as exc:
            return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}}, ensure_ascii=False)

    def _execute_on_main_thread(self, fn: Callable[[], Any]) -> Any:
        """Run IDA API work on UI/main thread to avoid thread violations."""
        result_box = {"value": None, "error": None}

        def _runner():
            try:
                result_box["value"] = fn()
            except Exception as exc:
                result_box["error"] = exc
            return 1

        # Prefer idaapi.execute_sync for broad compatibility across IDA versions.
        if hasattr(idaapi, "execute_sync") and hasattr(idaapi, "MFF_READ"):
            idaapi.execute_sync(_runner, idaapi.MFF_READ)
        else:
            # Last fallback for unusual environments.
            _runner()
        if result_box["error"] is not None:
            raise result_box["error"]
        return result_box["value"]

    @staticmethod
    def _ea(text) -> int:
        """Parse address parameter: handles str (hex/decimal), int, float."""
        try:
            if isinstance(text, (int, float)):
                return int(text)
            text = str(text).strip()
            if text.lower().startswith("0x"):
                return int(text, 16)
            # Assume hex for unprefixed numeric strings (IDA convention)
            return int(text, 16)
        except Exception:
            raise RpcError("INVALID_ADDRESS", f"bad address: {text}")

    @staticmethod
    def _paginate(items: List[Any], offset: int, count: int) -> Dict[str, Any]:
        offset = max(0, int(offset))
        count = max(1, int(count))
        return {"total": len(items), "items": items[offset : offset + count]}

    def _function_row(self, ea: int) -> Dict[str, Any]:
        start = idc.get_func_attr(ea, idc.FUNCATTR_START)
        end = idc.get_func_attr(ea, idc.FUNCATTR_END)
        return {"address": _hex(start), "end_address": _hex(end), "name": idc.get_func_name(start), "size": max(0, end - start)}

    def _read_scalar(self, address: str, size: int) -> Dict[str, Any]:
        ea = self._ea(address)
        buf = idaapi.get_bytes(ea, size)
        if not buf:
            raise RpcError("READ_FAILED", f"cannot read {size} at {address}")
        value = int.from_bytes(buf, "little", signed=False)
        return {"address": _hex(ea), "size": size, "value": value, "hex": buf.hex()}

    def _find_stack_lvar(self, fn, name: str):
        if not ida_hexrays or not ida_hexrays.init_hexrays_plugin():
            raise RpcError("HEX_RAYS_UNAVAILABLE", "Hex-Rays is required")
        cfunc = ida_hexrays.decompile(fn.start_ea)
        if not cfunc:
            raise RpcError("DECOMPILE_FAILED", "failed decompile for stack var ops")
        lvars = cfunc.get_lvars()
        for v in lvars:
            if v.name == name:
                return cfunc, v
        raise RpcError("NOT_FOUND", f"stack var not found: {name}")

    def _get_function_frame(self, fn):
        """Resolve stack frame struct in a version-compatible way."""
        frame = None
        if ida_frame and hasattr(ida_frame, "get_frame"):
            frame = ida_frame.get_frame(fn)
        if frame:
            return frame
        frame_id = idc.get_frame_id(fn.start_ea)
        if frame_id == idc.BADADDR or frame_id is None:
            return None
        if ida_struct and hasattr(ida_struct, "get_struc"):
            return ida_struct.get_struc(frame_id)
        return None

    def _iter_structs(self):
        """Yield (sid, name) for known structures with best-effort compatibility."""
        if ida_struct and hasattr(ida_struct, "get_struc_qty"):
            qty = ida_struct.get_struc_qty()
            for i in range(qty):
                sid = ida_struct.get_struc_by_idx(i)
                name = ida_struct.get_struc_name(sid)
                if name:
                    yield sid, name
            return
        try:
            for row in idautils.Structs():
                # IDA usually returns (idx, sid, name)
                if len(row) >= 3:
                    yield row[1], row[2]
        except Exception:
            return

    def _get_struct_id(self, struct_name: str):
        if ida_struct and hasattr(ida_struct, "get_struc_id"):
            return ida_struct.get_struc_id(struct_name)
        if hasattr(idc, "get_struc_id"):
            return idc.get_struc_id(struct_name)
        return idc.BADADDR

    def _resolve_struct_name(self, struct_name: str) -> str:
        """Resolve canonical struct name with fallback forms."""
        target = struct_name.strip()
        if not target:
            return target
        candidates = {target, target.lstrip("_"), "_" + target.lstrip("_")}
        # Direct resolution first
        for c in candidates:
            sid = self._get_struct_id(c)
            if sid not in (idc.BADADDR, idaapi.BADADDR):
                return c
        # Fuzzy case-insensitive fallback
        all_names = [name for _, name in self._iter_structs()]
        low_map = {name.lower(): name for name in all_names}
        for c in candidates:
            if c.lower() in low_map:
                return low_map[c.lower()]
        return target

    def _resolve_tinfo(self, type_name: str):
        """Resolve primitive or named type into tinfo_t with compatibility aliases."""
        if not ida_typeinf:
            raise RpcError("UNAVAILABLE", "ida_typeinf unavailable")

        alias = {
            "int8": ida_typeinf.BTF_INT8,
            "__int8": ida_typeinf.BTF_INT8,
            "int8_t": ida_typeinf.BTF_INT8,
            "char": ida_typeinf.BTF_INT8,
            "signed char": ida_typeinf.BTF_INT8,
            "uint8": ida_typeinf.BTF_UINT8,
            "__uint8": ida_typeinf.BTF_UINT8,
            "uint8_t": ida_typeinf.BTF_UINT8,
            "unsigned char": ida_typeinf.BTF_UINT8,
            "byte": ida_typeinf.BTF_UINT8,
            "int16": ida_typeinf.BTF_INT16,
            "__int16": ida_typeinf.BTF_INT16,
            "int16_t": ida_typeinf.BTF_INT16,
            "short": ida_typeinf.BTF_INT16,
            "uint16": ida_typeinf.BTF_UINT16,
            "__uint16": ida_typeinf.BTF_UINT16,
            "uint16_t": ida_typeinf.BTF_UINT16,
            "word": ida_typeinf.BTF_UINT16,
            "int32": ida_typeinf.BTF_INT32,
            "__int32": ida_typeinf.BTF_INT32,
            "int32_t": ida_typeinf.BTF_INT32,
            "int": ida_typeinf.BTF_INT32,
            "long": ida_typeinf.BTF_INT32,
            "uint32": ida_typeinf.BTF_UINT32,
            "__uint32": ida_typeinf.BTF_UINT32,
            "uint32_t": ida_typeinf.BTF_UINT32,
            "unsigned int": ida_typeinf.BTF_UINT32,
            "dword": ida_typeinf.BTF_UINT32,
            "int64": ida_typeinf.BTF_INT64,
            "__int64": ida_typeinf.BTF_INT64,
            "int64_t": ida_typeinf.BTF_INT64,
            "long long": ida_typeinf.BTF_INT64,
            "uint64": ida_typeinf.BTF_UINT64,
            "__uint64": ida_typeinf.BTF_UINT64,
            "uint64_t": ida_typeinf.BTF_UINT64,
            "unsigned long long": ida_typeinf.BTF_UINT64,
            "qword": ida_typeinf.BTF_UINT64,
            "bool": ida_typeinf.BTF_BOOL,
            "void": ida_typeinf.BTF_VOID,
            "float": ida_typeinf.BTF_FLOAT,
            "double": ida_typeinf.BTF_DOUBLE,
        }
        key = type_name.strip().lower()
        if key in alias:
            return ida_typeinf.tinfo_t(alias[key])

        # Named/local types
        tif = ida_typeinf.tinfo_t()
        for kind in (
            ida_typeinf.BTF_STRUCT,
            ida_typeinf.BTF_TYPEDEF,
            ida_typeinf.BTF_ENUM,
            ida_typeinf.BTF_UNION,
        ):
            if tif.get_named_type(None, type_name, kind):
                return tif

        # Generic parser fallback
        parsed = ida_typeinf.tinfo_t()
        if hasattr(ida_typeinf, "parse_decl") and ida_typeinf.parse_decl(
            parsed, ida_typeinf.get_idati(), type_name + ";", 0
        ):
            return parsed
        if ida_typeinf.tinfo_t(type_name):
            return ida_typeinf.tinfo_t(type_name)
        raise RpcError("BAD_TYPE", f"cannot parse type: {type_name}")

    def _get_struct_size_by_id(self, sid: int) -> int:
        if ida_struct and hasattr(ida_struct, "get_struc"):
            st = ida_struct.get_struc(sid)
            if st:
                return int(ida_struct.get_struc_size(st))
        if hasattr(idc, "get_struc_size"):
            try:
                return int(idc.get_struc_size(sid))
            except Exception:
                return 0
        return 0

    def check_connection(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {"connected": True, "ida_version": idaapi.get_kernel_version()}

    def get_metadata(self, _: Dict[str, Any]) -> Dict[str, Any]:
        path = idc.get_input_file_path() or ""
        file_hash = ""
        if path:
            try:
                with open(path, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()
            except Exception:
                pass
        is_64 = False
        if ida_ida and hasattr(ida_ida, "inf_is_64bit"):
            is_64 = bool(ida_ida.inf_is_64bit())
        elif hasattr(idaapi, "get_inf_structure"):
            try:
                is_64 = bool(idaapi.get_inf_structure().is_64bit())
            except Exception:
                is_64 = False
        return {
            "input_path": path,
            "input_name": idc.get_root_filename(),
            "image_base": _hex(idaapi.get_imagebase()),
            "sha256": file_hash,
            "is_64bit": is_64,
        }

    def get_current_address(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {"address": _hex(idc.here())}

    def get_current_function(self, _: Dict[str, Any]) -> Dict[str, Any]:
        fn = idaapi.get_func(idc.here())
        if not fn:
            raise RpcError("NOT_FOUND", "cursor not in function")
        return self._function_row(fn.start_ea)

    def list_functions(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filt = str(params.get("filter", "")).strip().lower()
        if filt:
            rows = [self._function_row(ea) for ea in idautils.Functions() if filt in idc.get_func_name(ea).lower()]
        else:
            rows = [self._function_row(ea) for ea in idautils.Functions()]
        return self._paginate(rows, params.get("offset", 0), params.get("count", 100))

    def list_functions_filter(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filt = str(params.get("filter", "")).lower()
        rows = [self._function_row(ea) for ea in idautils.Functions() if filt in idc.get_func_name(ea).lower()]
        return self._paginate(rows, params.get("offset", 0), params.get("count", 100))

    def get_function_by_address(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["address"])
        fn = idaapi.get_func(ea)
        if not fn:
            raise RpcError("NOT_FOUND", f"no function at {params['address']}")
        return self._function_row(fn.start_ea)

    def get_function_by_name(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = str(params["name"]).strip()
        ea = idc.get_name_ea_simple(name)
        if ea != idc.BADADDR and idaapi.get_func(ea):
            return self.get_function_by_address({"address": _hex(ea)})

        # Fuzzy fallback: exact lower/name contains, for cases like "CheckSerial".
        target = name.lower()
        candidates = []
        for fn_ea in idautils.Functions():
            fn_name = (idc.get_func_name(fn_ea) or "").strip()
            if not fn_name:
                continue
            fn_l = fn_name.lower()
            score = 0
            if fn_l == target:
                score = 100
            elif target in fn_l:
                score = 50
            if score:
                candidates.append((score, fn_ea, fn_name))
        if not candidates:
            raise RpcError("NOT_FOUND", f"name not found: {name}")
        candidates.sort(key=lambda x: (-x[0], x[1]))
        best_ea = candidates[0][1]
        return self.get_function_by_address({"address": _hex(best_ea)})

    def decompile_function(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["address"])
        if ida_hexrays is None or not ida_hexrays.init_hexrays_plugin():
            raise RpcError("HEX_RAYS_UNAVAILABLE", "Hex-Rays decompiler unavailable")
        cfunc = ida_hexrays.decompile(ea)
        if not cfunc:
            raise RpcError("DECOMPILE_FAILED", f"failed at {params['address']}")
        return {"address": params["address"], "pseudocode": str(cfunc)}

    def disassemble_function(self, params: Dict[str, Any]) -> Dict[str, Any]:
        fn = idaapi.get_func(self._ea(params["start_address"]))
        if not fn:
            raise RpcError("NOT_FOUND", "function not found")
        lines = [{"address": _hex(ea), "line": idc.generate_disasm_line(ea, 0) or ""} for ea in idautils.FuncItems(fn.start_ea)]
        out: Dict[str, Any] = {"start_address": _hex(fn.start_ea), "lines": lines}
        # `lines` are IDA disassembly text; pseudocode (when available) matches common "反汇编/伪代码" expectations.
        if ida_hexrays is not None and ida_hexrays.init_hexrays_plugin():
            cfunc = ida_hexrays.decompile(fn.start_ea)
            if cfunc:
                out["pseudocode"] = str(cfunc)
        return out

    def get_entry_points(self, _: Dict[str, Any]) -> Dict[str, Any]:
        items = []
        qty = idaapi.get_entry_qty()
        for i in range(qty):
            ord_i = idaapi.get_entry_ordinal(i)
            ea = idaapi.get_entry(ord_i)
            items.append({"ordinal": ord_i, "address": _hex(ea), "name": idaapi.get_entry_name(ord_i)})
        return {"items": items}

    def get_callers(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["function_address"])
        callers = [{"from": _hex(x.frm), "to": _hex(x.to), "type": int(x.type)} for x in idautils.XrefsTo(ea, 0)]
        return {"function_address": _hex(ea), "callers": callers}

    def get_callees(self, params: Dict[str, Any]) -> Dict[str, Any]:
        fn = idaapi.get_func(self._ea(params["function_address"]))
        if not fn:
            raise RpcError("NOT_FOUND", "function not found")
        callees = set()
        for ea in idautils.FuncItems(fn.start_ea):
            for x in idautils.XrefsFrom(ea, 0):
                tgt = x.to
                tgt_fn = idaapi.get_func(tgt)
                if tgt_fn:
                    callees.add(tgt_fn.start_ea)
        return {"function_address": _hex(fn.start_ea), "callees": [self._function_row(ea) for ea in sorted(callees)]}

    def get_xrefs_to(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["address"])
        refs = [{"from": _hex(x.frm), "to": _hex(x.to), "type": int(x.type)} for x in idautils.XrefsTo(ea, 0)]
        return {"address": _hex(ea), "xrefs": refs}

    def get_xrefs_to_field(self, params: Dict[str, Any]) -> Dict[str, Any]:
        struct_name = params["struct_name"]
        field_name = params["field_name"]
        return {"struct_name": struct_name, "field_name": field_name, "xrefs": [], "note": "field xref requires advanced type mapping; currently empty"}

    def list_globals(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filt = str(params.get("filter", "")).strip().lower()
        names = list(idautils.Names())
        if filt:
            rows = [{"address": _hex(ea), "name": n} for ea, n in names if filt in n.lower() and idc.get_segm_name(ea) != ".text"]
        else:
            rows = [{"address": _hex(ea), "name": n} for ea, n in names if idc.get_segm_name(ea) != ".text"]
        return self._paginate(rows, params.get("offset", 0), params.get("count", 100))

    def list_globals_filter(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filt = str(params.get("filter", "")).lower()
        names = list(idautils.Names())
        rows = [{"address": _hex(ea), "name": n} for ea, n in names if filt in n.lower() and idc.get_segm_name(ea) != ".text"]
        return self._paginate(rows, params.get("offset", 0), params.get("count", 100))

    def get_global_variable_value_at_address(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["address"])
        size = ida_bytes.get_item_size(ea) if ida_bytes else 1
        size = max(1, min(int(size), 16))
        buf = idaapi.get_bytes(ea, size) or b""
        return {"address": _hex(ea), "size": size, "hex": buf.hex(), "value_u64": int.from_bytes(buf[:8].ljust(8, b"\x00"), "little")}

    def get_global_variable_value_by_name(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = idc.get_name_ea_simple(params["variable_name"])
        if ea == idc.BADADDR:
            raise RpcError("NOT_FOUND", "global not found")
        return self.get_global_variable_value_at_address({"address": _hex(ea)})

    def list_imports(self, params: Dict[str, Any]) -> Dict[str, Any]:
        rows = []

        def cb(ea, name, ordinal):
            rows.append({"address": _hex(ea), "name": name or "", "ordinal": ordinal})
            return True

        nimps = ida_nalt.get_import_module_qty() if ida_nalt else 0
        for i in range(nimps):
            ida_nalt.enum_import_names(i, cb)
        return self._paginate(rows, params.get("offset", 0), params.get("count", 100))

    def list_strings(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filt = str(params.get("filter", "")).strip().lower()
        s = list(idautils.Strings())
        if filt:
            rows = [{"address": _hex(int(x.ea)), "value": str(x)} for x in s if filt in str(x).lower()]
        else:
            rows = [{"address": _hex(int(x.ea)), "value": str(x)} for x in s]
        return self._paginate(rows, params.get("offset", 0), params.get("count", 100))

    def list_strings_filter(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filt = str(params.get("filter", "")).lower()
        s = list(idautils.Strings())
        rows = [{"address": _hex(int(x.ea)), "value": str(x)} for x in s if filt in str(x).lower()]
        return self._paginate(rows, params.get("offset", 0), params.get("count", 100))

    def get_defined_structures(self, _: Dict[str, Any]) -> Dict[str, Any]:
        out = []
        for sid, name in self._iter_structs():
            out.append({"name": name, "size": self._get_struct_size_by_id(sid)})
        if not out:
            raise RpcError("UNAVAILABLE", "structure enumeration API unavailable")
        return {"items": out}

    def search_structures(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filt = str(params["filter"]).lower()
        items = self.get_defined_structures({})["items"]
        return {"items": [x for x in items if filt in x["name"].lower()]}

    def _struct_fields(self, struct_name: str) -> List[Dict[str, Any]]:
        resolved_name = self._resolve_struct_name(struct_name)
        sid = self._get_struct_id(resolved_name)
        if sid in (idc.BADADDR, idaapi.BADADDR):
            raise RpcError("NOT_FOUND", f"struct not found: {struct_name}")
        members = []
        # Preferred path: idautils.StructMembers (works on many IDA versions)
        try:
            rows = idautils.StructMembers(sid)
            for row in rows:
                # Common shape: (offset, name, size)
                if len(row) >= 3:
                    members.append({"name": row[1], "offset": int(row[0]), "size": int(row[2])})
                elif len(row) >= 2:
                    members.append({"name": row[1], "offset": int(row[0]), "size": 0})
        except Exception:
            rows = []
        if members:
            return members
        # Fallback path with ida_struct object if present
        if ida_struct and hasattr(ida_struct, "get_struc"):
            st = ida_struct.get_struc(sid)
            if st:
                for m in st.members:
                    members.append({"name": ida_struct.get_member_name(m.id), "offset": m.soff, "size": m.eoff - m.soff})
        return members

    def get_struct_info_simple(self, params: Dict[str, Any]) -> Dict[str, Any]:
        resolved_name = self._resolve_struct_name(params["name"])
        return {"name": resolved_name, "fields": self._struct_fields(resolved_name)}

    def analyze_struct_detailed(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self.get_struct_info_simple(params)

    def get_struct_at_address(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["address"])
        resolved_name = self._resolve_struct_name(params["struct_name"])
        fields = self._struct_fields(resolved_name)
        values = []
        for f in fields:
            addr = ea + int(f["offset"])
            buf = idaapi.get_bytes(addr, int(f["size"])) or b""
            values.append({"field": f["name"], "offset": f["offset"], "address": _hex(addr), "hex": buf.hex()})
        return {"address": _hex(ea), "struct_name": resolved_name, "values": values}

    def list_local_types(self, _: Dict[str, Any]) -> Dict[str, Any]:
        if not ida_typeinf:
            return {"items": []}
        idati = ida_typeinf.get_idati()
        items = []
        qty = ida_typeinf.get_ordinal_qty(idati)
        for ordinal in range(1, qty):
            name = ida_typeinf.get_numbered_type_name(idati, ordinal)
            if name:
                items.append({"ordinal": ordinal, "name": name})
        return {"items": items}

    def declare_c_type(self, params: Dict[str, Any]) -> Dict[str, Any]:
        decl = params["c_declaration"]
        ok = idc.parse_decls(decl + ";", idc.PT_SILENT)
        return {"accepted": ok == 0, "result_code": ok}

    def get_stack_frame_variables(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not ida_typeinf:
            raise RpcError("UNAVAILABLE", "ida_typeinf unavailable")
        fn = idaapi.get_func(self._ea(params["function_address"]))
        if not fn:
            raise RpcError("NOT_FOUND", "function not found")
        tif = ida_typeinf.tinfo_t()
        if not tif.get_type_by_tid(fn.frame) or not tif.is_udt():
            return {"items": []}
        udt = ida_typeinf.udt_type_data_t()
        tif.get_udt_details(udt)
        items = []
        for udm in udt:
            if hasattr(udm, "is_gap") and udm.is_gap():
                continue
            items.append(
                {
                    "name": udm.name,
                    "offset": hex(udm.offset // 8),
                    "size": hex(udm.size // 8),
                    "type": str(udm.type),
                }
            )
        return {"items": items}

    def create_stack_frame_variable(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not ida_frame or not ida_typeinf:
            raise RpcError("UNAVAILABLE", "ida_frame/ida_typeinf unavailable")
        fn = idaapi.get_func(self._ea(params["function_address"]))
        if not fn:
            raise RpcError("NOT_FOUND", "function not found")
        off = int(params["offset"], 0)
        tif = self._resolve_tinfo(params["type_name"])
        ok = ida_frame.define_stkvar(fn, params["variable_name"], off, tif)
        if not ok:
            raise RpcError("CREATE_FAILED", "define_stkvar failed")
        return {"created": True, "variable_name": params["variable_name"], "offset": off, "type_name": params["type_name"]}

    def rename_stack_frame_variable(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not ida_typeinf or not ida_frame:
            raise RpcError("UNAVAILABLE", "rename stack var requires ida_typeinf/ida_frame")
        fn = idaapi.get_func(self._ea(params["function_address"]))
        if not fn:
            raise RpcError("NOT_FOUND", "function not found")
        frame_tif = ida_typeinf.tinfo_t()
        if not ida_frame.get_func_frame(frame_tif, fn):
            raise RpcError("FRAME_NOT_FOUND", "no frame")
        idx, udm = frame_tif.get_udm(params["old_name"])
        if not udm:
            raise RpcError("NOT_FOUND", "old stack var not found")
        tid = frame_tif.get_udm_tid(idx)
        if ida_frame.is_special_frame_member(tid):
            raise RpcError("RENAME_FAILED", "special frame member cannot be renamed")
        udm_full = ida_typeinf.udm_t()
        frame_tif.get_udm_by_tid(udm_full, tid)
        offset = udm_full.offset // 8
        if ida_frame.is_funcarg_off(fn, offset):
            raise RpcError("RENAME_FAILED", "argument member cannot be renamed")
        success = ida_frame.define_stkvar(fn, params["new_name"], ida_frame.soff_to_fpoff(fn, offset), udm_full.type)
        if not success:
            raise RpcError("RENAME_FAILED", "define_stkvar for new name failed")
        return {"renamed": True, "old_name": params["old_name"], "new_name": params["new_name"]}

    def set_stack_frame_variable_type(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not ida_typeinf or not ida_frame:
            raise RpcError("UNAVAILABLE", "set stack var type requires ida_typeinf/ida_frame")
        fn = idaapi.get_func(self._ea(params["function_address"]))
        if not fn:
            raise RpcError("NOT_FOUND", "function not found")
        frame_tif = ida_typeinf.tinfo_t()
        if not ida_frame.get_func_frame(frame_tif, fn):
            raise RpcError("FRAME_NOT_FOUND", "no frame")
        idx, udm = frame_tif.get_udm(params["variable_name"])
        if not udm:
            raise RpcError("NOT_FOUND", "stack var not found")
        tid = frame_tif.get_udm_tid(idx)
        udm_full = ida_typeinf.udm_t()
        frame_tif.get_udm_by_tid(udm_full, tid)
        offset = udm_full.offset // 8
        tif = self._resolve_tinfo(params["type_name"])
        if not ida_frame.set_frame_member_type(fn, offset, tif):
            raise RpcError("TYPE_SET_FAILED", "cannot set frame member type")
        return {"updated": True}

    def delete_stack_frame_variable(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not ida_typeinf or not ida_frame:
            raise RpcError("UNAVAILABLE", "delete stack var requires ida_typeinf/ida_frame")
        fn = idaapi.get_func(self._ea(params["function_address"]))
        if not fn:
            raise RpcError("NOT_FOUND", "function not found")
        frame_tif = ida_typeinf.tinfo_t()
        if not ida_frame.get_func_frame(frame_tif, fn):
            raise RpcError("FRAME_NOT_FOUND", "no frame")
        idx, udm = frame_tif.get_udm(params["variable_name"])
        if not udm:
            raise RpcError("NOT_FOUND", "stack var not found")
        tid = frame_tif.get_udm_tid(idx)
        if ida_frame.is_special_frame_member(tid):
            raise RpcError("DELETE_FAILED", "special frame member cannot be deleted")
        udm_full = ida_typeinf.udm_t()
        frame_tif.get_udm_by_tid(udm_full, tid)
        offset = udm_full.offset // 8
        size = max(1, udm_full.size // 8)
        if ida_frame.is_funcarg_off(fn, offset):
            raise RpcError("DELETE_FAILED", "argument member cannot be deleted")
        if not ida_frame.delete_frame_members(fn, offset, offset + size):
            raise RpcError("DELETE_FAILED", "cannot delete stack var")
        return {"deleted": True}

    def rename_function(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["function_address"])
        if not idc.set_name(ea, params["new_name"], idc.SN_NOWARN):
            raise RpcError("RENAME_FAILED", "rename function failed")
        return {"address": _hex(ea), "new_name": params["new_name"]}

    def rename_global_variable(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = idc.get_name_ea_simple(params["old_name"])
        if ea == idc.BADADDR:
            raise RpcError("NOT_FOUND", "global not found")
        if not idc.set_name(ea, params["new_name"], idc.SN_NOWARN):
            raise RpcError("RENAME_FAILED", "rename global failed")
        return {"address": _hex(ea), "new_name": params["new_name"]}

    def rename_local_variable(self, params: Dict[str, Any]) -> Dict[str, Any]:
        fn = idaapi.get_func(self._ea(params["function_address"]))
        cfunc, lvar = self._find_stack_lvar(fn, params["old_name"])
        if not ida_hexrays.rename_lvar(fn.start_ea, params["old_name"], params["new_name"]):
            raise RpcError("RENAME_FAILED", "rename local variable failed")
        return {"function_address": _hex(fn.start_ea), "old_name": lvar.name, "new_name": params["new_name"]}

    def set_comment(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["address"])
        if not idc.set_cmt(ea, params["comment"], 0):
            raise RpcError("COMMENT_FAILED", "set comment failed")
        return {"address": _hex(ea), "comment": params["comment"]}

    def set_function_prototype(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["function_address"])
        if not idc.SetType(ea, params["prototype"]):
            raise RpcError("TYPE_SET_FAILED", "set prototype failed")
        return {"function_address": _hex(ea), "prototype": params["prototype"]}

    def set_global_variable_type(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = idc.get_name_ea_simple(params["variable_name"])
        if ea == idc.BADADDR:
            raise RpcError("NOT_FOUND", "global not found")
        if not idc.SetType(ea, params["new_type"]):
            raise RpcError("TYPE_SET_FAILED", "set global type failed")
        return {"variable_name": params["variable_name"], "new_type": params["new_type"]}

    def set_local_variable_type(self, params: Dict[str, Any]) -> Dict[str, Any]:
        fn = idaapi.get_func(self._ea(params["function_address"]))
        if not fn:
            raise RpcError("NOT_FOUND", "function not found")
        cfunc, _ = self._find_stack_lvar(fn, params["variable_name"])
        for idx, v in enumerate(cfunc.get_lvars()):
            if v.name == params["variable_name"]:
                if not ida_hexrays.modify_user_lvar_info(
                    fn.start_ea,
                    ida_hexrays.MLI_TYPE,
                    ida_hexrays.lvar_saved_info_t(v, tif=ida_typeinf.tinfo_t(params["new_type"])),
                ):
                    raise RpcError("TYPE_SET_FAILED", "set local type failed")
        return {"updated": True}

    def data_read_byte(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._read_scalar(params["address"], 1)

    def data_read_word(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._read_scalar(params["address"], 2)

    def data_read_dword(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._read_scalar(params["address"], 4)

    def data_read_qword(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._read_scalar(params["address"], 8)

    def data_read_string(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["address"])
        value = idc.get_strlit_contents(ea, -1, idc.STRTYPE_C)
        if value is None:
            raise RpcError("READ_FAILED", "string not found")
        return {"address": _hex(ea), "value": value.decode("utf-8", errors="replace")}

    def read_memory_bytes(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["memory_address"])
        size = int(params["size"])
        buf = idaapi.get_bytes(ea, size)
        if buf is None:
            raise RpcError("READ_FAILED", "cannot read memory")
        return {"address": _hex(ea), "size": size, "hex": buf.hex()}

    def patch_address_assembles(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params["address"])
        ins = [x.strip() for x in params["instructions"].split(";") if x.strip()]
        if not hasattr(idautils, "Assemble"):
            raise RpcError("NOT_IMPLEMENTED", "idautils.Assemble is unavailable in this IDA build")
        if not ida_bytes:
            raise RpcError("UNAVAILABLE", "ida_bytes unavailable")
        cur = ea
        patched = []
        for asm in ins:
            ok_asm, bytes_to_patch = idautils.Assemble(cur, asm)
            if not ok_asm:
                raise RpcError("ASSEMBLE_FAILED", f"assemble failed at {_hex(cur)}: {asm}")
            ida_bytes.patch_bytes(cur, bytes_to_patch)
            size = len(bytes_to_patch)
            patched.append({"address": _hex(cur), "instruction": asm, "size": size})
            cur += size
        return {"address": _hex(ea), "patched": patched}

    def convert_number(self, params: Dict[str, Any]) -> Dict[str, Any]:
        text = str(params["text"]).strip()
        size = params.get("size")
        try:
            value = int(text, 0)
        except Exception:
            raise RpcError("INVALID_NUMBER", f"bad number: {text}")
        if size is None:
            if value <= 0xFF:
                size = 1
            elif value <= 0xFFFF:
                size = 2
            elif value <= 0xFFFFFFFF:
                size = 4
            else:
                size = 8
        size = int(size)
        raw = value.to_bytes(size, "little", signed=False)
        return {"int": value, "hex": hex(value), "size": size, "little_endian_hex": raw.hex(), "ascii": raw.decode("latin-1", errors="replace"), "binary": format(value, "b")}


    # ── 合并工具 ────────────────────────────────────────────────────────

    def get_function(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """合并 get_function_by_address + get_function_by_name。智能解析 query。"""
        query = str(params.get("query", "")).strip()
        if not query:
            raise RpcError("INVALID_PARAM", "query is required")
        # 尝试作为地址解析
        try:
            if query.lower().startswith("0x"):
                ea = int(query, 16)
            elif query.lower().startswith("sub_"):
                ea = int(query[4:], 16)
            else:
                ea = int(query, 16)  # 假定 hex
        except ValueError:
            ea = idc.BADADDR
        if ea != idc.BADADDR:
            fn = idaapi.get_func(ea)
            if fn:
                return self._function_row(fn.start_ea)
        # 按名称查找
        return self.get_function_by_name({"name": query})

    def get_global_variable_value(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """合并 get_global_variable_value_at_address + get_global_variable_value_by_name。"""
        query = str(params.get("query", "")).strip()
        stripped = query.strip()
        # 尝试作为 hex 地址解析
        try:
            if stripped.lower().startswith("0x"):
                ea = int(stripped, 16)
            else:
                ea = int(stripped, 16)
        except ValueError:
            ea = idc.BADADDR
        if ea != idc.BADADDR and ea > 0:
            return self.get_global_variable_value_at_address({"address": hex(ea)})
        # 按名称查找
        return self.get_global_variable_value_by_name({"variable_name": stripped})

    # ── Python 执行 ──────────────────────────────────────────────────────

    def execute_python(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """执行任意 IDAPython 代码片段。"""
        code = str(params.get("code", "")).strip()
        if not code:
            raise RpcError("INVALID_PARAM", "code is required")
        local_ns: Dict[str, Any] = {}
        try:
            exec(code, {
                "idaapi": idaapi, "idc": idc, "idautils": idautils,
                "ida_bytes": ida_bytes, "ida_funcs": ida_funcs,
                "ida_name": ida_name, "ida_struct": ida_struct,
                "ida_typeinf": ida_typeinf, "ida_frame": ida_frame,
                "ida_hexrays": ida_hexrays, "ida_nalt": ida_nalt,
                "ida_ua": ida_ua, "ida_ida": ida_ida,
            }, local_ns)
            # 收集非私有、非模块的输出
            output = {k: v for k, v in local_ns.items() if not k.startswith("_")}
            return {"output": str(output) if output else "(no output)", "success": True}
        except Exception as exc:
            raise RpcError("PYTHON_ERROR", str(exc))

    # ── 调试工具 (移植自 ida-pro-mcp api_debug.py) ─────────────────────

    def debug_start(self, _: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import ida_dbg
            if ida_dbg.get_process_state() != ida_dbg.DSTATE_NOTASK:
                return {"status": "already_started"}
            path = idc.get_input_file_path()
            if not path:
                raise RpcError("NO_FILE", "no input file")
            ida_dbg.load_debugger("win32", True)
            ida_dbg.start_process(path, "", "")
            return {"status": "started", "path": path}
        except Exception:
            try:
                ida_dbg = __import__("ida_dbg")
                ida_dbg.load_debugger("win32", True)
                ida_dbg.start_process(idc.get_input_file_path(), "", "")
                return {"status": "started"}
            except Exception as exc:
                raise RpcError("DEBUG_START_FAILED", str(exc))

    def debug_exit(self, _: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import ida_dbg
            ida_dbg.exit_process()
            return {"status": "exited"}
        except Exception as exc:
            raise RpcError("DEBUG_EXIT_FAILED", str(exc))

    def debug_continue(self, _: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import ida_dbg
            ida_dbg.continue_process()
            return {"status": "running"}
        except Exception as exc:
            raise RpcError("DEBUG_CONTINUE_FAILED", str(exc))

    def debug_step_into(self, _: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import ida_dbg
            ida_dbg.step_into()
            return {"status": "stepped_into"}
        except Exception as exc:
            raise RpcError("DEBUG_STEP_FAILED", str(exc))

    def debug_step_over(self, _: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import ida_dbg
            ida_dbg.step_over()
            return {"status": "stepped_over"}
        except Exception as exc:
            raise RpcError("DEBUG_STEP_FAILED", str(exc))

    def debug_run_to(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params.get("address", "0"))
        try:
            import ida_dbg
            ida_dbg.run_to(ea)
            return {"status": "running_to", "address": _hex(ea)}
        except Exception as exc:
            raise RpcError("DEBUG_RUN_TO_FAILED", str(exc))

    def debug_list_breakpoints(self, _: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import ida_dbg
            bps = []
            qty = ida_dbg.get_bpt_qty()
            for i in range(qty):
                ea = ida_dbg.get_bpt_ea(i)
                bps.append({
                    "index": i,
                    "address": _hex(ea),
                    "enabled": ida_dbg.is_bpt_enabled(i) if hasattr(ida_dbg, "is_bpt_enabled") else True,
                })
            return {"breakpoints": bps}
        except Exception as exc:
            raise RpcError("BPT_LIST_FAILED", str(exc))

    def debug_add_breakpoint(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params.get("address", "0"))
        try:
            import ida_dbg
            ok = ida_dbg.add_bpt(ea)
            if not ok:
                raise RpcError("BPT_ADD_FAILED", f"failed at {_hex(ea)}")
            return {"address": _hex(ea), "added": True}
        except RpcError:
            raise
        except Exception as exc:
            raise RpcError("BPT_ADD_FAILED", str(exc))

    def debug_delete_breakpoint(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params.get("address", "0"))
        try:
            import ida_dbg
            ok = ida_dbg.del_bpt(ea)
            if not ok:
                raise RpcError("BPT_DEL_FAILED", f"failed at {_hex(ea)}")
            return {"address": _hex(ea), "deleted": True}
        except RpcError:
            raise
        except Exception as exc:
            raise RpcError("BPT_DEL_FAILED", str(exc))

    def debug_get_registers(self, _: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import ida_dbg
            import ida_idd as _idd
            dbg = _idd.get_dbg()
            if not dbg:
                raise RpcError("NO_DEBUGGER", "debugger not running")
            tid = ida_dbg.get_current_thread()
            regs = {}
            regvals = ida_dbg.get_reg_vals(tid)
            for idx, rv in enumerate(regvals):
                reg_info = dbg.regs(idx) if hasattr(dbg, "regs") else None
                name = reg_info.name if reg_info else f"reg_{idx}"
                try:
                    val = rv.pyval(reg_info.dtype) if reg_info else rv.ival
                    regs[name] = hex(val) if isinstance(val, int) else str(val)
                except Exception:
                    regs[name] = str(rv.ival)
            return {"tid": tid, "registers": regs}
        except RpcError:
            raise
        except Exception as exc:
            raise RpcError("REGS_FAILED", str(exc))

    def debug_get_stacktrace(self, _: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import ida_dbg
            tid = ida_dbg.get_current_thread()
            frames = []
            # Walk stack using EBP/RBP chain
            regs_raw = ida_dbg.get_reg_vals(tid) if hasattr(ida_dbg, "get_reg_vals") else None
            if regs_raw is None:
                return {"tid": tid, "stacktrace": [], "note": "registers unavailable"}
            # Simple approach: enumerate frames via IDA API
            try:
                qty = ida_dbg.get_call_stack(tid, []) if hasattr(ida_dbg, "get_call_stack") else 0
            except Exception:
                qty = 0
            return {"tid": tid, "stacktrace": [], "frame_count": qty,
                    "note": "use debug_get_registers for rip/rbp/rsp"}
        except Exception as exc:
            raise RpcError("STACKTRACE_FAILED", str(exc))

    def debug_read_memory(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params.get("address", "0"))
        size = int(params.get("size", 256))
        try:
            import ida_dbg
            buf = ida_dbg.read_dbg_memory(ea, min(size, 4096))
            if buf is None:
                raise RpcError("DEBUG_READ_FAILED", f"cannot read at {_hex(ea)}")
            return {"address": _hex(ea), "size": len(buf), "hex": buf.hex()}
        except RpcError:
            raise
        except Exception as exc:
            raise RpcError("DEBUG_READ_FAILED", str(exc))

    def debug_write_memory(self, params: Dict[str, Any]) -> Dict[str, Any]:
        ea = self._ea(params.get("address", "0"))
        data_str = str(params.get("data", "")).replace(" ", "")
        try:
            data = bytes.fromhex(data_str)
        except Exception:
            raise RpcError("INVALID_DATA", "data must be hex string, e.g. '90 90'")
        try:
            import ida_dbg
            ok = ida_dbg.write_dbg_memory(ea, data)
            if not ok:
                raise RpcError("DEBUG_WRITE_FAILED", f"cannot write at {_hex(ea)}")
            return {"address": _hex(ea), "size": len(data), "written": True}
        except RpcError:
            raise
        except Exception as exc:
            raise RpcError("DEBUG_WRITE_FAILED", str(exc))



_server_instance = SurveyIdaBridgeServer()


class SurveyMcpBridgePlugin(idaapi.plugin_t):
    flags = idaapi.PLUGIN_KEEP
    comment = "Survey IDA MCP bridge plugin"
    help = "Expose IDA APIs to local FastMCP bridge."
    wanted_name = "Survey MCP Bridge"
    wanted_hotkey = "Ctrl-Shift-M"

    def init(self):
        _server_instance.start()
        return idaapi.PLUGIN_KEEP

    def term(self):
        _server_instance.stop()

    def run(self, arg):
        idaapi.info("Survey MCP Bridge is running on 127.0.0.1:31337\n")


def PLUGIN_ENTRY():
    return SurveyMcpBridgePlugin()
