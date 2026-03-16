from __future__ import annotations

import json
import re
from typing import Any, Dict


def parse_json_response(text: str) -> Dict[str, Any]:
    try:
        clean = text.strip()
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
            tool_block = _extract_tool_invocation(text)
            if tool_block:
                payload_tool = str(payload.get("tool", "") or "").strip()
                if not payload_tool or (
                    payload_tool.startswith("[")
                    and payload_tool.count("[/") < tool_block.count("[/")
                ):
                    payload["tool"] = tool_block
            if "tool" not in payload and isinstance(payload.get("action"), str):
                payload["tool"] = payload.get("action")
            return payload
        return {}
    except json.JSONDecodeError:
        try:
            tool = _extract_tool_invocation(text)
            if not tool:
                tool = _extract_string_field(text, "tool")
            if not tool:
                tool = _extract_string_field(text, "action")
            decision = _extract_string_field(text, "decision").upper()
            card = _extract_object_field(text, "card")
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
