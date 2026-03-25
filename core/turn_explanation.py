from __future__ import annotations

import json
import re
import time
from typing import Any, Sequence


LAST_TURN_EXPLANATION_PREFIX = "[LAST_TURN_EXPLANATION_CONTEXT]"

_EXPLAIN_REQUEST_RE = re.compile(
    r"(?is)^\s*(?:please\s+)?(?:"
    r"why\s+did\s+you(?:\s+\w+){0,6}\??|"
    r"how\s+did\s+you\s+decide(?:\s+\w+){0,6}\??|"
    r"explain(?:\s+(?:that|the\s+last\s+turn|why|your\s+decision))?"
    r")\s*[.!?]*\s*$"
)
_EXPLAIN_FOLLOWUP_RE = re.compile(
    r"(?is)^\s*(?:please\s+)?(?:"
    r"more\s+detail(?:s)?|"
    r"why\s+specifically|"
    r"be\s+more\s+specific|"
    r"elaborate|"
    r"go\s+deeper"
    r")\s*[.!?]*\s*$"
)


def looks_like_turn_explanation_request(text: str) -> bool:
    return bool(_EXPLAIN_REQUEST_RE.match(str(text or "").strip()))


def looks_like_turn_explanation_followup(text: str) -> bool:
    return bool(_EXPLAIN_FOLLOWUP_RE.match(str(text or "").strip()))


def build_last_turn_explanation_message(snapshot: dict[str, Any]) -> str:
    payload = dict(snapshot or {})
    return LAST_TURN_EXPLANATION_PREFIX + "\n" + json.dumps(payload, ensure_ascii=False)


def parse_last_turn_explanation_message(content: str) -> dict[str, Any] | None:
    raw = str(content or "").strip()
    if not raw.startswith(LAST_TURN_EXPLANATION_PREFIX):
        return None
    payload_text = raw[len(LAST_TURN_EXPLANATION_PREFIX):].strip()
    if not payload_text:
        return None
    try:
        payload = json.loads(payload_text)
    except Exception:
        return None
    return dict(payload) if isinstance(payload, dict) else None


def extract_last_turn_explanation_snapshot(
    messages: Sequence[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    for message in reversed(list(messages or [])):
        if str(message.get("role") or "").strip().lower() != "system":
            continue
        parsed = parse_last_turn_explanation_message(str(message.get("content") or ""))
        if parsed is not None:
            return parsed
    return None


def activate_last_turn_explanation_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict) or not snapshot:
        return None
    updated = dict(snapshot)
    updated["explain_active"] = True
    return updated


def _route_source_label(orc, *, reporter_just_ran: bool = False) -> str:
    interceptor = str(getattr(orc, "route_interceptor", "") or "").strip().upper()
    if interceptor:
        return f"interceptor:{interceptor.lower()}"
    turn_stats = getattr(orc, "turn_stats", None)
    bypass = str(getattr(turn_stats, "pre_llm_bypass", "") or "").strip().lower()
    if bypass:
        return f"bypass:{bypass}"
    if reporter_just_ran:
        return "search_report_handoff"
    return "router_llm"


def _verification_snapshot(orc) -> dict[str, Any]:
    verification = getattr(orc, "last_verification", None)
    if verification is None:
        return {}
    verdict = str(getattr(verification, "verdict", "") or "").strip().upper()
    evidence = str(getattr(verification, "evidence_summary", "") or "").strip()
    recommendation = str(getattr(verification, "recommendation", "") or "").strip()
    checker_path = str(getattr(verification, "checker_path", "") or "").strip()
    if not any((verdict, evidence, recommendation, checker_path)):
        return {}
    return {
        "verdict": verdict,
        "evidence": evidence,
        "recommendation": recommendation,
        "checker_path": checker_path,
    }


def _infer_outcome(orc) -> tuple[str, str]:
    turn_stats = getattr(orc, "turn_stats", None)
    if bool(getattr(turn_stats, "persona_error", False)):
        detail = str(getattr(turn_stats, "outcome_detail", "") or "").strip()
        return "FAILED", detail

    verification = getattr(orc, "last_verification", None)
    if verification is not None:
        verdict = str(getattr(verification, "verdict", "") or "").strip().upper() or "FAILED"
        detail = str(getattr(verification, "evidence_summary", "") or "").strip()
        return verdict, detail

    outcome_pack = getattr(orc, "last_stage_outcome", None)
    if outcome_pack is not None:
        status = str(getattr(outcome_pack, "status", "") or "").strip().upper()
        detail = str(getattr(outcome_pack, "detail", "") or "").strip()
        if "PAUSED" in status:
            return "PAUSED", detail
        if "FAILED" in status or "INCOMPLETE" in status:
            return "FAILED", detail
        if bool(getattr(outcome_pack, "effective_success", False)):
            return "VERIFIED", detail
        if status:
            return "FAILED", detail

    detail = str(getattr(turn_stats, "outcome_detail", "") or "").strip()
    latest_route_error = str(getattr(orc, "latest_route_error", "") or "").strip()
    return "VERIFIED", detail or latest_route_error


def _phase_timings_snapshot(orc) -> dict[str, float]:
    turn_stats = getattr(orc, "turn_stats", None)
    phase_ms = dict(getattr(turn_stats, "phase_ms", {}) or {})
    started_at = getattr(turn_stats, "started_at_monotonic", None)
    if started_at is not None and not phase_ms.get("total"):
        phase_ms["total"] = round(max(0.0, time.perf_counter() - float(started_at)) * 1000.0, 3)
    ordered: dict[str, float] = {}
    for key in ("route", "manager", "reporter", "persona", "tts", "total"):
        value = phase_ms.get(key)
        try:
            ordered[key] = round(float(value or 0.0), 3)
        except Exception:
            ordered[key] = 0.0
    return ordered


def build_last_turn_explanation_snapshot(orc, *, reporter_just_ran: bool = False) -> dict[str, Any]:
    route_decision = dict(getattr(orc, "route_decision", {}) or {})
    decision = str(route_decision.get("decision") or "CHAT").strip().upper() or "CHAT"
    # For SEARCH routes context_card is stale (set by the previous TASK turn's
    # phase_manager).  Always read the card from route_decision for SEARCH so
    # the search_query field reflects the actual query, not orc.user_msg.
    if decision == "SEARCH":
        card = dict(route_decision.get("card") or {})
    else:
        card = dict(getattr(orc, "context_card", {}) or route_decision.get("card") or {})
    stages = [
        {
            "stage_goal": str(stage.get("stage_goal") or "").strip(),
            "stage_type": str(stage.get("stage_type") or "").strip().upper(),
            "file_stage_kind": str(stage.get("file_stage_kind") or "").strip().upper(),
        }
        for stage in (card.get("stages") or [])
        if isinstance(stage, dict)
    ]
    outcome, outcome_detail = _infer_outcome(orc)
    turn_stats = getattr(orc, "turn_stats", None)
    search_query = str(card.get("query") or getattr(orc, "user_msg", "") or "").strip() if decision == "SEARCH" else ""
    snapshot = {
        "turn_id": str(getattr(turn_stats, "turn_id", "") or "").strip(),
        "user_request": str(getattr(orc, "user_msg", "") or "").strip(),
        "route_decision": decision,
        "route_source": _route_source_label(orc, reporter_just_ran=reporter_just_ran),
        "reporter_just_ran": bool(reporter_just_ran),
        "search_query": search_query,
        "task_goal": str(card.get("goal") or "").strip(),
        "stages": stages,
        "verification": _verification_snapshot(orc),
        "router_reroute_fired": bool(getattr(turn_stats, "router_reroute_fired", False)),
        "outcome": outcome,
        "outcome_detail": outcome_detail,
        "phase_ms": _phase_timings_snapshot(orc),
        "explain_active": False,
    }
    return snapshot


def render_explain_last_turn_block(
    snapshot: dict[str, Any] | None,
    *,
    detail_level: str = "default",
) -> str:
    lines = ["[EXPLAIN_LAST_TURN]"]
    if not isinstance(snapshot, dict) or not snapshot:
        lines.extend(
            (
                "No completed previous-turn snapshot is available.",
                "Tell the user you can explain only the most recent completed turn, and none is available right now.",
                "Keep the answer to one or two sentences.",
                "Do not emit [ROUTER].",
            )
        )
        return "\n".join(lines)

    normalized_detail_level = str(detail_level or "default").strip().lower() or "default"
    lines.extend(
        (
            "Explain the immediately previous completed turn only.",
            "Default answer: 2 to 4 short plain-English sentences.",
            "Cover what path was chosen, why that path was chosen, and what outcome happened.",
            "Stay on the explanation request only. Do not pivot to unrelated suggestions or offers.",
            "Do not use raw field names, JSON, or internal jargon unless the user explicitly asked for more detail.",
            "Do not emit [ROUTER].",
        )
    )
    if normalized_detail_level == "detailed":
        lines.append("The user asked for more detail. You may mention verification, reroutes, and timings if they help.")

    lines.append(f"Previous user request: {str(snapshot.get('user_request') or '').strip()}")
    lines.append(f"Route decision: {str(snapshot.get('route_decision') or '').strip()}")
    lines.append(f"Route source: {str(snapshot.get('route_source') or '').strip()}")
    if snapshot.get("reporter_just_ran"):
        lines.append("Reporter handoff: This was the final user-facing summary after a background search completed.")
    if str(snapshot.get("search_query") or "").strip():
        lines.append(f"Search query: {str(snapshot.get('search_query') or '').strip()}")
    if str(snapshot.get("task_goal") or "").strip():
        lines.append(f"Task goal: {str(snapshot.get('task_goal') or '').strip()}")
    stages = [dict(item) for item in (snapshot.get("stages") or []) if isinstance(item, dict)]
    if stages:
        lines.append("Stages:")
        for stage in stages[:4]:
            goal = str(stage.get("stage_goal") or "").strip()
            stage_type = str(stage.get("stage_type") or "").strip()
            file_stage_kind = str(stage.get("file_stage_kind") or "").strip()
            summary = goal or stage_type or "Unnamed stage"
            extras = [part for part in (stage_type, file_stage_kind) if part]
            if extras:
                summary += " [" + ", ".join(extras) + "]"
            lines.append(f"- {summary}")
    verification = dict(snapshot.get("verification") or {})
    if verification:
        lines.append(f"Verification verdict: {str(verification.get('verdict') or '').strip()}")
        if str(verification.get("checker_path") or "").strip():
            lines.append(f"Checker path: {str(verification.get('checker_path') or '').strip()}")
        if str(verification.get("recommendation") or "").strip():
            lines.append(f"Recommendation: {str(verification.get('recommendation') or '').strip()}")
        if str(verification.get("evidence") or "").strip():
            lines.append(f"Verification evidence: {str(verification.get('evidence') or '').strip()}")
    lines.append(
        "Router reroute fired: "
        + ("yes" if bool(snapshot.get("router_reroute_fired")) else "no")
    )
    lines.append(f"Outcome: {str(snapshot.get('outcome') or '').strip()}")
    if str(snapshot.get("outcome_detail") or "").strip():
        lines.append(f"Outcome detail: {str(snapshot.get('outcome_detail') or '').strip()}")
    phase_ms = dict(snapshot.get("phase_ms") or {})
    if phase_ms:
        timing_parts: list[str] = []
        for key in ("route", "manager", "reporter", "persona", "tts", "total"):
            try:
                value = round(float(phase_ms.get(key) or 0.0), 3)
            except Exception:
                continue
            timing_parts.append(f"{key}={value} ms")
        if timing_parts:
            lines.append("Phase timings: " + ", ".join(timing_parts))
    return "\n".join(lines)
