from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import CFG

_TOKEN_RE = re.compile(r"\S+")
_SUMMARY_HEADERS = (
    "[CONVERSATION SUMMARY]",
    "[EARLIER CONVERSATION SUMMARY - MAY OMIT DETAIL]",
)


@dataclass(frozen=True)
class ConversationCompressionResult:
    history: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    compressed: bool = False
    summarization_used: bool = False


class ConversationCompressor:
    DEFAULT_TOKEN_BUDGET = 400
    SUMMARY_HEADER = _SUMMARY_HEADERS[-1]

    def __init__(self, *, token_budget: int = DEFAULT_TOKEN_BUDGET) -> None:
        self.token_budget = max(int(token_budget or self.DEFAULT_TOKEN_BUDGET), 1)

    @staticmethod
    def load_summary(path: Path) -> str:
        summary_path = Path(path)
        if not summary_path.exists():
            return ""
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str(payload.get("summary") or "").strip()

    @staticmethod
    def save_summary(path: Path, summary: str) -> None:
        summary_path = Path(path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"summary": str(summary or "").strip()}
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def compress_history(
        self,
        *,
        history: list[dict[str, Any]],
        existing_summary: str = "",
        max_turns: int,
        llm: Any | None = None,
        cancel_token: Any | None = None,
    ) -> ConversationCompressionResult:
        messages = [dict(item) for item in (history or []) if isinstance(item, dict)]
        summary_text = self._sanitize_summary_text(existing_summary)
        max_turns = max(int(max_turns or 0), 1)

        if len(messages) <= max_turns:
            history_out = list(messages)
            if summary_text:
                history_out = [self.build_summary_message(summary_text), *history_out]
            return ConversationCompressionResult(
                history=history_out,
                summary=summary_text,
                compressed=bool(summary_text),
                summarization_used=False,
            )

        kept = list(messages[-max_turns:])
        dropped = self._clean_messages(messages[:-max_turns])
        candidate = self._build_candidate_summary(existing_summary=summary_text, dropped=dropped)
        summarization_used = False

        if candidate and self._token_count(candidate) > self.token_budget:
            summary_text = self._summarize_candidate(candidate, llm=llm, cancel_token=cancel_token)
            summarization_used = True
        else:
            summary_text = candidate

        history_out = list(kept)
        if summary_text:
            history_out = [self.build_summary_message(summary_text), *history_out]

        return ConversationCompressionResult(
            history=history_out,
            summary=summary_text,
            compressed=True,
            summarization_used=summarization_used,
        )

    @staticmethod
    def build_summary_message(summary: str) -> dict[str, Any]:
        return {
            "role": "system",
            "content": ConversationCompressor.SUMMARY_HEADER + "\n" + str(summary or "").strip(),
            "hidden": True,
        }

    def _summarize_candidate(
        self,
        candidate: str,
        *,
        llm: Any | None,
        cancel_token: Any | None = None,
    ) -> str:
        if llm is None:
            return self._truncate_to_budget(candidate)

        messages = [
            {
                "role": "system",
                "content": (
                    "Summarize the older Piper conversation context for future turns.\n"
                    "Preserve durable facts, explicit user preferences, active project context, task decisions, "
                    "document conclusions, and unresolved commitments.\n"
                    "Drop filler, pleasantries, UI chatter, and repeated confirmations.\n"
                    f"Return plain text only, under {self.token_budget} tokens."
                ),
            },
            {
                "role": "user",
                "content": candidate,
            },
        ]
        try:
            raw = llm.generate(
                messages,
                temperature=0.1,
                max_tokens=int(getattr(CFG, "CONVERSATION_SUMMARY_MAX_TOKENS", 500)),
                cancel_token=cancel_token,
            )
        except Exception:
            return self._truncate_to_budget(candidate)
        summary = self._normalize_summary(raw)
        if not summary:
            return self._truncate_to_budget(candidate)
        if self._token_count(summary) > self.token_budget:
            return self._truncate_to_budget(summary)
        return summary

    def _truncate_to_budget(self, text: str) -> str:
        tokens = _TOKEN_RE.findall(str(text or ""))
        if len(tokens) <= self.token_budget:
            return str(text or "").strip()
        return " ".join(tokens[-self.token_budget :]).strip()

    @staticmethod
    def _normalize_summary(text: str) -> str:
        clean = str(text or "").strip()
        if "```" in clean:
            parts = clean.split("```")
            clean = next((part for part in parts if part and not part.strip().lower().startswith("text")), clean)
        for header in _SUMMARY_HEADERS:
            clean = clean.replace(header, "").strip()
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        return clean.strip()

    def _sanitize_summary_text(self, text: str) -> str:
        clean = self._normalize_summary(text)
        if not clean:
            return ""
        kept_lines: list[str] = []
        pending_blank = False
        for raw_line in clean.splitlines():
            line = str(raw_line or "").strip()
            if not line:
                pending_blank = bool(kept_lines)
                continue
            if self._summary_line_is_low_value(line):
                continue
            if pending_blank and kept_lines:
                kept_lines.append("")
            kept_lines.append(line)
            pending_blank = False
        return "\n".join(kept_lines).strip()

    @staticmethod
    def _token_count(text: str) -> int:
        return len(_TOKEN_RE.findall(str(text or "")))

    @staticmethod
    def _summary_line_is_low_value(line: str) -> bool:
        normalized = " ".join(str(line or "").split()).strip()
        if not normalized:
            return True
        lower = normalized.lower()
        if lower.startswith("system:"):
            return True
        return lower.startswith(
            (
                "=== new session",
                "[latest_runtime_context]",
                "[search summary for ",
                "[search report consumed for ",
                "[search report rule]",
                "background search complete for '",
                "the web search is complete.",
                "[ui]",
            )
        )

    @staticmethod
    def _clean_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for message in list(messages or []):
            role = str(message.get("role") or "user").strip().lower() or "user"
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                continue
            if role == "assistant" and content in {"Thinking...", "Thinking…"}:
                continue
            if content.startswith("[UI]"):
                continue
            if content.startswith("[LATEST_RUNTIME_CONTEXT]"):
                continue
            if content.startswith(_SUMMARY_HEADERS):
                continue
            if content.startswith("[copied"):
                continue
            if content.startswith("[ERROR]"):
                continue
            cleaned.append({"role": role, "content": content})
        return cleaned

    def _build_candidate_summary(
        self,
        *,
        existing_summary: str,
        dropped: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        existing = self._sanitize_summary_text(existing_summary)
        if existing:
            parts.append(existing)
        transcript = self._render_transcript(dropped)
        if transcript:
            parts.append(transcript)
        return "\n\n".join(part for part in parts if part).strip()

    @staticmethod
    def _render_transcript(messages: list[dict[str, Any]]) -> str:
        if not messages:
            return ""
        lines: list[str] = []
        for message in messages:
            role = str(message.get("role") or "user").strip().lower() or "user"
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"{role.title()}: {content}")
        return "\n".join(lines).strip()
