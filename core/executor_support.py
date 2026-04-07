from __future__ import annotations

import json
import re
import sys
from typing import Any

from config import CFG
from core.contracts import PlannerDecision, StageCard
from core.json_utils import parse_json_response
from core.prompting import PromptBuilder
from core.runtime_control import CancellationToken, OperationCancelled


MODULE_PACKAGE_ALIASES = {
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
    "pil": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "dateutil": "python-dateutil",
    "googleapiclient": "google-api-python-client",
    "duckduckgo_search": "duckduckgo-search",
    "sentence_transformers": "sentence-transformers",
}


def normalize_completion_handoff(decision: PlannerDecision) -> str:
    for key in ("proposal", "handoff", "message", "response"):
        value = str(decision.get(key, "") or "").strip()
        if value:
            return value
    return ""


def normalize_install_package_name(module_name: str) -> str:
    raw = str(module_name or "").strip()
    if not raw:
        return ""
    top_level = raw.split(".", 1)[0]
    alias = MODULE_PACKAGE_ALIASES.get(top_level.lower())
    if alias:
        return alias
    return top_level.replace("_", "-")


def extract_installable_packages(tool_result: Any) -> list[str]:
    if isinstance(tool_result, dict):
        text_parts = [
            str(tool_result.get("summary", "") or ""),
            str(tool_result.get("stdout", "") or ""),
            str(tool_result.get("stderr", "") or ""),
        ]
        text = "\n".join(part for part in text_parts if part)
    else:
        text = str(tool_result or "")
    if not text:
        return []

    candidates: list[str] = []

    for module_name in re.findall(r"No module named ['\"]([^'\"]+)['\"]", text, re.IGNORECASE):
        normalized = normalize_install_package_name(module_name)
        if normalized:
            candidates.append(normalized)

    for command in re.findall(r"pip install ([A-Za-z0-9_.\- ]+)", text, re.IGNORECASE):
        for token in command.split():
            cleaned = token.strip()
            if cleaned:
                candidates.append(cleaned)

    stdlib_names = getattr(sys, "stdlib_module_names", set())
    filtered: list[str] = []
    seen: set[str] = set()
    for package in candidates:
        top_level = package.split(".", 1)[0].replace("-", "_").lower()
        if top_level in stdlib_names:
            continue
        if package.lower() in {"pip", "setuptools", "wheel"}:
            continue
        if package not in seen:
            filtered.append(package)
            seen.add(package)
    return filtered


def format_tool_result_for_log(tool_name: str, tool_result: Any, *, limit: int = 240) -> str:
    if isinstance(tool_result, dict):
        if tool_name in {"RUN_CODE", "FILE_OP"}:
            created = tool_result.get("created_files") or []
            updated = tool_result.get("updated_files") or []
            deleted = tool_result.get("deleted_files") or []
            created_dirs = tool_result.get("created_dirs") or []
            deleted_dirs = tool_result.get("deleted_dirs") or []
            action = str(tool_result.get("action", "")).strip()
            summary = str(tool_result.get("summary", "")).strip()
            parts = [
                f"action={action}" if action else "",
                f"status={tool_result.get('status', 'UNKNOWN')}",
                summary,
            ]
            if created:
                parts.append(f"created={created}")
            if updated:
                parts.append(f"updated={updated}")
            if deleted:
                parts.append(f"deleted={deleted}")
            if created_dirs:
                parts.append(f"created_dirs={created_dirs}")
            if deleted_dirs:
                parts.append(f"deleted_dirs={deleted_dirs}")
            text = " | ".join(part for part in parts if part)
        else:
            text = json.dumps(tool_result, ensure_ascii=False)
    else:
        text = " ".join(str(tool_result).split())
    if len(text) > limit:
        text = text[:limit] + "..."
    return f"   -> Result [{tool_name}]: {text}"


def decision_signature(decision: PlannerDecision) -> str:
    thought = " ".join(str(decision.get("thought", "") or "").split())
    tool = " ".join(str(decision.get("tool", "") or "").split())
    proposal = " ".join(str(decision.get("proposal", "") or "").split())
    is_complete = bool(decision.get("is_complete", False))
    return json.dumps(
        {
            "thought": thought,
            "tool": tool,
            "is_complete": is_complete,
            "proposal": proposal,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def looks_like_completion_thought(text: str) -> bool:
    clean = " ".join(str(text or "").lower().split())
    if not clean:
        return False
    return bool(
        re.search(
            r"\b(no further action is needed|no further action needed|success condition is satisfied|stage goal is satisfied|inspection is complete|stage is complete|no more action is needed|nothing more is needed)\b",
            clean,
        )
    )


def tool_result_text(tool_result: Any) -> str:
    if isinstance(tool_result, str):
        return tool_result
    try:
        return json.dumps(tool_result, ensure_ascii=False)
    except Exception:
        return str(tool_result)


def tool_signature(tag: str, tool_result: Any) -> str:
    tag_upper = str(tag or "").upper()
    if not isinstance(tool_result, dict):
        return f"{tag_upper}:{str(tool_result)[:160]}"
    action = str(tool_result.get("action", "")).lower()
    status = str(tool_result.get("status", "")).upper()
    summary = str(tool_result.get("summary", ""))[:160]
    changed = int(bool(tool_result.get("workspace_changed")))
    requested_path = str(tool_result.get("requested_path") or tool_result.get("path") or "")[:120]
    requested_paths = ",".join(sorted(str(item) for item in (tool_result.get("requested_paths") or [])[:4]))
    requested_root = str(tool_result.get("requested_root") or "")[:120]
    requested_query = str(tool_result.get("requested_query") or "")[:120]
    files = tool_result.get("files") or {}
    file_keys = ",".join(sorted(str(path) for path in list(files.keys())[:4])) if isinstance(files, dict) else ""
    created = ",".join(sorted(str(item) for item in (tool_result.get("created_files") or [])[:4]))
    updated = ",".join(sorted(str(item) for item in (tool_result.get("updated_files") or [])[:4]))
    deleted = ",".join(sorted(str(item) for item in (tool_result.get("deleted_files") or [])[:4]))
    evidence = ",".join(sorted(str(item) for item in (tool_result.get("evidence_files") or [])[:4]))
    return "|".join(
        [
            tag_upper,
            action,
            status,
            str(changed),
            requested_path,
            requested_paths,
            requested_root,
            requested_query,
            file_keys,
            created,
            updated,
            deleted,
            evidence,
            summary,
        ]
    )


def run_inspector(*, llm, ui, scratchpad: list[str], stage: StageCard, cancel_token: CancellationToken | None = None) -> bool:
    prompt_path = CFG.DATA_DIR / "prompts" / "inspector.txt"
    if not prompt_path.exists():
        return False

    sys_base = prompt_path.read_text(encoding="utf-8")
    scratch_text = "\n".join(scratchpad)
    sys_prompt = PromptBuilder.build_inspector_prompt(sys_base, stage, scratch_text)

    try:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": "Return the inspector decision JSON for the stage above."},
        ]
        raw = llm.generate(
            messages,
            temperature=0.0,
            max_tokens=int(getattr(CFG, "INSPECTOR_MAX_TOKENS", 120)),
            cancel_token=cancel_token,
        )
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        ui.put(("agent_log", f"[INSPECTOR] {raw.strip()}"))
        dec = str(parse_json_response(raw).get("decision", "CONTINUE")).upper()
        return dec == "FINISH"
    except OperationCancelled:
        raise
    except Exception as e:
        ui.put(("agent_log", f"   -> Inspector Error: {e}"))
        return False
