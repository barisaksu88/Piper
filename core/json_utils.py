from __future__ import annotations

import json
import re
from typing import Any, Dict


_PLANNER_METADATA_KEYS = {
    "thought",
    "tool",
    "is_complete",
    "proposal",
    "constraints",
    "clarification_requested",
    "stop_recommended",
    "handoff",
    "message",
    "response",
}


def parse_json_response(text: Any) -> Dict[str, Any]:
    try:
        if isinstance(text, dict):
            payload = dict(text)
            _normalize_payload_tool(payload, "")
            return payload

        raw_text = str(text or "")
        clean = raw_text.strip()
        if "```json" in clean:
            clean = clean.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in clean:
            clean = clean.split("```", 1)[1].split("```", 1)[0]

        clean = clean.strip()
        start = clean.find("{")
        if start != -1:
            end = clean.rfind("}") + 1
            candidate = clean[start:end] if end > start else clean[start:]
            candidate = _append_missing_json_closers(candidate)
            payload = json.loads(candidate)
            if not isinstance(payload, dict):
                return {}
            tool_block = _extract_tool_invocation(raw_text)
            if tool_block:
                payload_tool = normalize_tool_invocation(payload.get("tool", ""))
                if not payload_tool or (
                    payload_tool.startswith("[")
                    and payload_tool.count("[/") < tool_block.count("[/")
                ):
                    payload["tool"] = tool_block
                else:
                    payload["tool"] = payload_tool
            else:
                _normalize_payload_tool(payload, raw_text)
            if "tool" not in payload and isinstance(payload.get("action"), str):
                payload["tool"] = payload.get("action")
            return payload
        return {}
    except json.JSONDecodeError:
        try:
            fallback_text = str(text or "")
            tool = _extract_tool_invocation(fallback_text)
            if not tool:
                tool = _extract_string_field(fallback_text, "tool")
            if not tool:
                tool = _extract_string_field(fallback_text, "action")
            decision = _extract_string_field(fallback_text, "decision").upper()
            card = _extract_object_field(fallback_text, "card")
            if decision in {"CHAT", "TASK", "SEARCH"}:
                payload = {"decision": decision}
                if isinstance(card, dict) and card:
                    payload["card"] = card
                return payload
            return {
                "thought": _extract_string_field(text, "thought"),
                "tool": tool,
                "is_complete": _extract_bool_field(text, "is_complete"),
                "proposal": _extract_string_field(text, "proposal"),
            }
        except Exception:
            return {}


def normalize_tool_invocation(tool: Any) -> str:
    """Return Piper's bracket-tag tool syntax from common planner variants.

    The planner contract asks for a string such as
    ``[FILE_OP] {"action":"list_tree"} [/FILE_OP]``.  Local models sometimes
    emit structured JSON instead, for example
    ``{"name":"FILE_OP","arguments":{"action":"list_tree"}}``.  Normalize that
    shape at the boundary so the executor never calls string methods on dicts.
    """
    if tool is None:
        return ""
    if isinstance(tool, str):
        return tool.strip()
    if not isinstance(tool, dict):
        return str(tool or "").strip()

    data = dict(tool)
    function = data.get("function")
    function_data = function if isinstance(function, dict) else {}
    name = (
        data.get("name")
        or data.get("tool")
        or data.get("tag")
        or data.get("type")
        or function_data.get("name")
        or ""
    )
    args = (
        data.get("arguments")
        if "arguments" in data
        else data.get("args")
        if "args" in data
        else data.get("payload")
        if "payload" in data
        else data.get("input")
        if "input" in data
        else function_data.get("arguments")
        if "arguments" in function_data
        else None
    )

    if not str(name or "").strip() and str(data.get("action") or "").strip():
        name = "FILE_OP"
    tag = _normalize_tool_tag_name(name)
    if not tag:
        return str(tool or "").strip()
    if tag.startswith("["):
        return tag

    if args is None:
        args = {
            key: value
            for key, value in data.items()
            if key not in {"name", "tool", "tag", "type", "function"}
        }

    if tag == "FILE_OP":
        payload_text = _tool_args_to_text(args)
        return f"[FILE_OP] {payload_text or '{}'} [/FILE_OP]"
    if tag == "RUN_CODE":
        code = _tool_code_from_args(args)
        return f"[RUN_CODE]\n{code}\n[/RUN_CODE]" if code else "[RUN_CODE]\n\n[/RUN_CODE]"
    if tag == "INSTALL_PACKAGE":
        package = _tool_package_from_args(args)
        return f"[INSTALL_PACKAGE: {package}]" if package else "[INSTALL_PACKAGE]"

    payload_text = _tool_args_to_text(args)
    if payload_text:
        return f"[{tag}] {payload_text} [/{tag}]"
    return f"[{tag}]"


def _normalize_payload_tool(payload: Dict[str, Any], source_text: str) -> None:
    tool_block = _extract_tool_invocation(source_text)
    if tool_block:
        payload["tool"] = tool_block
        return

    if "tool" in payload:
        raw_tool = payload.get("tool")
        if isinstance(raw_tool, str) and _normalize_tool_tag_name(raw_tool) in {"FILE_OP", "RUN_CODE"}:
            sibling_args = {
                key: value
                for key, value in payload.items()
                if key not in _PLANNER_METADATA_KEYS
            }
            if sibling_args:
                payload["tool"] = normalize_tool_invocation(
                    {"name": raw_tool, "arguments": sibling_args}
                )
                return
        payload["tool"] = normalize_tool_invocation(raw_tool)
        return

    if isinstance(payload.get("action"), str):
        payload["tool"] = payload.get("action")


def _normalize_tool_tag_name(name: Any) -> str:
    clean = str(name or "").strip()
    if not clean:
        return ""
    if clean.startswith("["):
        return clean
    clean = clean.strip("[]").replace("-", "_").replace(" ", "_").upper()
    aliases = {
        "FILEOP": "FILE_OP",
        "FILE_OPERATION": "FILE_OP",
        "RUN": "RUN_CODE",
        "CODE": "RUN_CODE",
        "PYTHON": "RUN_CODE",
    }
    return aliases.get(clean, clean)


def _tool_args_to_text(args: Any) -> str:
    if args is None:
        return ""
    if isinstance(args, str):
        return args.strip()
    try:
        return json.dumps(args, ensure_ascii=False)
    except Exception:
        return str(args or "").strip()


def _tool_code_from_args(args: Any) -> str:
    if isinstance(args, str):
        return args.strip()
    if isinstance(args, dict):
        for key in ("code", "script", "python", "body", "content"):
            value = str(args.get(key) or "").strip()
            if value:
                return value
    return _tool_args_to_text(args)


def _tool_package_from_args(args: Any) -> str:
    if isinstance(args, str):
        return args.strip()
    if isinstance(args, dict):
        for key in ("package", "name", "module", "dependency"):
            value = str(args.get(key) or "").strip()
            if value:
                return value
    return ""


def _extract_string_field(text: str, name: str) -> str:
    match = re.search(rf'"{name}"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if match:
        raw = match.group(1)
        try:
            return json.loads(f'"{raw}"')
        except Exception:
            return raw

    single_quote_match = re.search(rf"'{name}'\s*:\s*'((?:[^'\\\\]|\\\\.)*)'", text, re.DOTALL)
    if single_quote_match:
        raw = single_quote_match.group(1)
        return raw.replace("\\'", "'").replace('\\"', '"')

    loose_match = re.search(rf'["\']?{name}["\']?\s*:\s*"([^"]*)"', text, re.DOTALL)
    if loose_match:
        return loose_match.group(1)
    return ""


def _extract_bool_field(text: str, name: str) -> bool:
    match = re.search(rf'["\']?{name}["\']?\s*:\s*(true|false)', text, re.IGNORECASE)
    return bool(match and match.group(1).lower() == "true")


def _extract_tool_invocation(text: str) -> str:
    block_match = re.search(r"(\[(?:FILE_OP|RUN_CODE)\].*?\[/(?:FILE_OP|RUN_CODE)\])", text, re.DOTALL)
    if block_match:
        return block_match.group(1).strip()
    inline_match = re.search(r"(\[(?:[A-Z_]+)(?::[^\]]*)?\])", text)
    if inline_match:
        return inline_match.group(1).strip()
    return ""


def _append_missing_json_closers(text: str) -> str:
    stack: list[str] = []
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()

    if in_string:
        text += '"'
    while stack:
        text += stack.pop()
    return text


def _extract_object_field(text: str, name: str) -> Dict[str, Any]:
    match = re.search(rf'"{name}"\s*:\s*([{{\[])', text)
    if not match:
        return {}

    opener = match.group(1)
    closer = "}" if opener == "{" else "]"
    start = match.start(1)
    depth = 0
    in_string = False
    escape = False

    for idx in range(start, len(text)):
        ch = text[idx]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                candidate = text[start : idx + 1]
                try:
                    payload = json.loads(_append_missing_json_closers(candidate))
                except json.JSONDecodeError:
                    return {}
                return payload if isinstance(payload, dict) else {}

    candidate = _append_missing_json_closers(text[start:])
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
