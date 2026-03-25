from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Iterable


PENDING_FILE_TARGET_CONFIRMATION_PREFIX = "[PENDING_FILE_TARGET_CONFIRMATION]"

_AFFIRMATIVE_CONFIRM_RE = re.compile(
    r"(?is)^\s*(?:yes(?:\s*[, ]\s*please)?|yeah|yep|yup|sure|okay|ok|alright|all right|go ahead|please do|do it|sounds good|that's right|that is right|correct)\s*[.!?]*\s*$"
)
_DECLINE_CONFIRM_RE = re.compile(
    r"(?is)^\s*(?:no|nope|nah|not that|wrong one|wrong file|never mind|nevermind|cancel|stop|leave it|leave them|forget it|don't|dont)\s*[.!?]*\s*$"
)
_PATH_TOKEN_RE = re.compile(r"[\w./\\-]+(?:\.[A-Za-z0-9]{1,8})?")


def build_pending_file_target_confirmation_message(payload: dict[str, Any]) -> str:
    body = json.dumps(dict(payload or {}), ensure_ascii=False, separators=(",", ":"))
    return f"{PENDING_FILE_TARGET_CONFIRMATION_PREFIX}\n{body}"


def extract_pending_file_target_confirmation(
    recent_history: Iterable[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    for item in reversed(list(recent_history or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "system":
            continue
        content = str(item.get("content") or "").strip()
        if not content.startswith(PENDING_FILE_TARGET_CONFIRMATION_PREFIX):
            continue
        _, _, body = content.partition("\n")
        if not body.strip():
            return None
        try:
            payload = json.loads(body)
        except Exception:
            return None
        return dict(payload) if isinstance(payload, dict) else None
    return None


def build_confirmed_route_decision(
    base_route_decision: dict[str, Any],
    *,
    exact_target: str,
    chosen_target: str,
) -> dict[str, Any]:
    route = json.loads(json.dumps(dict(base_route_decision or {}), ensure_ascii=False))
    return _replace_in_jsonish(route, exact_target=exact_target, chosen_target=chosen_target)


def classify_pending_file_target_confirmation_reply(
    user_msg: str,
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    text = str(user_msg or "").strip()
    confirmation = dict(payload or {})
    candidates = [str(item).strip() for item in (confirmation.get("candidates") or []) if str(item).strip()]
    if not text or not candidates:
        return None

    chosen = _resolve_candidate_reply(text, candidates)
    if chosen:
        return {"decision": "choose", "chosen_target": chosen}

    if len(candidates) == 1 and _AFFIRMATIVE_CONFIRM_RE.match(text):
        return {"decision": "confirm", "chosen_target": candidates[0]}

    if _DECLINE_CONFIRM_RE.match(text):
        return {"decision": "decline"}

    return None


def _replace_in_jsonish(value: Any, *, exact_target: str, chosen_target: str) -> Any:
    if isinstance(value, dict):
        replaced = {}
        for key, item in value.items():
            if key == "active_targets" and isinstance(item, list):
                replaced[key] = [
                    chosen_target if _targets_match(entry, exact_target) else _replace_in_jsonish(entry, exact_target=exact_target, chosen_target=chosen_target)
                    for entry in item
                ]
                continue
            replaced[key] = _replace_in_jsonish(item, exact_target=exact_target, chosen_target=chosen_target)
        return replaced
    if isinstance(value, list):
        return [_replace_in_jsonish(item, exact_target=exact_target, chosen_target=chosen_target) for item in value]
    if isinstance(value, str):
        return _replace_target_text(value, exact_target=exact_target, chosen_target=chosen_target)
    return value


def _replace_target_text(text: str, *, exact_target: str, chosen_target: str) -> str:
    raw = str(text or "")
    exact = str(exact_target or "").strip()
    chosen = str(chosen_target or "").strip()
    if not raw or not exact or not chosen:
        return raw
    if re.search(r"[./\\]", exact):
        return re.sub(re.escape(exact), chosen, raw, flags=re.IGNORECASE)
    pattern = re.compile(rf"(?<![\w./\\-]){re.escape(exact)}(?![\w./\\-])", re.IGNORECASE)
    return pattern.sub(chosen, raw)


def _resolve_candidate_reply(text: str, candidates: list[str]) -> str:
    clean_text = str(text or "").strip()
    if not clean_text:
        return ""
    for raw in _PATH_TOKEN_RE.findall(clean_text):
        candidate = _match_candidate(raw, candidates)
        if candidate:
            return candidate
    normalized_text = _normalize_fragment(clean_text)
    for candidate in candidates:
        if _normalize_fragment(candidate) and _normalize_fragment(candidate) in normalized_text:
            return candidate
        if _normalize_fragment(PurePosixPath(candidate).stem) and _normalize_fragment(PurePosixPath(candidate).stem) in normalized_text:
            return candidate
    return ""


def _match_candidate(raw: str, candidates: list[str]) -> str:
    probe = str(raw or "").strip()
    if not probe:
        return ""
    for candidate in candidates:
        if _targets_match(probe, candidate):
            return candidate
    return ""


def _targets_match(left: str, right: str) -> bool:
    left_clean = str(left or "").replace("\\", "/").strip().lower()
    right_clean = str(right or "").replace("\\", "/").strip().lower()
    if not left_clean or not right_clean:
        return False
    if left_clean == right_clean:
        return True
    left_name = PurePosixPath(left_clean).name
    right_name = PurePosixPath(right_clean).name
    if left_name == right_name:
        return True
    left_stem = PurePosixPath(left_name).stem
    right_stem = PurePosixPath(right_name).stem
    return bool(left_stem and right_stem and left_stem == right_stem)


def _normalize_fragment(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    return " ".join(cleaned.split())
