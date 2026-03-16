from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable, List

from .stores import IntentStateStore, SituationalStateStore

if TYPE_CHECKING:
    from .world_model import WorldModelManager


_EXPLICIT_ASSISTANT_REQUEST_RE = re.compile(
    r"(?i)\b(?:can|could|would|will)\s+you\b|\bplease\b|\bhelp me\b|\bfor me\b|\bgo ahead and\b"
)
_TENTATIVE_INTENT_PATTERNS = (
    (re.compile(r"(?is)^\s*(?:maybe|perhaps)\s+(?:i|we)\s+should\s+(?P<action>.+?)\s*[.?!]*$"), "tentative"),
    (re.compile(r"(?is)^\s*(?:(?:i|we)\s+might\s+(?:need|want|have)\s+to)\s+(?P<action>.+?)\s*[.?!]*$"), "tentative"),
    (re.compile(r"(?is)^\s*(?:should|could)\s+(?:i|we)\s+(?P<action>.+?)\s*[?!.]*$"), "tentative"),
    (re.compile(r"(?is)^\s*(?:i|we)\s+(?:am|are|m|'m|re)\s+thinking\s+about\s+(?P<action>.+?)\s*[.?!]*$"), "tentative"),
    (re.compile(r"(?is)^\s*(?:i|we)\s+(?:am|are|m|'m|re)\s+considering\s+(?P<action>.+?)\s*[.?!]*$"), "tentative"),
    (re.compile(r"(?is)^\s*(?:i|we)\s+want\s+to\s+(?P<action>.+?)\s*[.?!]*$"), "strong"),
    (re.compile(r"(?is)^\s*(?:i|we)(?:'d| would)\s+like\s+to\s+(?P<action>.+?)\s*[.?!]*$"), "strong"),
    (re.compile(r"(?is)^\s*(?:i|we)\s+need\s+to\s+(?P<action>.+?)\s*[.?!]*$"), "strong"),
    (re.compile(r"(?is)^\s*(?:i|we)\s+plan\s+to\s+(?P<action>.+?)\s*[.?!]*$"), "strong"),
)
_STATE_WORDS = (
    "hungry",
    "tired",
    "sleepy",
    "sad",
    "stressed",
    "anxious",
    "sick",
    "ill",
    "bored",
    "frustrated",
    "annoyed",
    "overwhelmed",
    "busy",
)
_INTENT_MATCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "i",
    "im",
    "i'm",
    "me",
    "my",
    "need",
    "plan",
    "please",
    "should",
    "that",
    "the",
    "to",
    "today",
    "tomorrow",
    "want",
    "we",
}
_ACTIVITY_RE = re.compile(
    r"(?is)\b(?:i am|i'm|im|we are|we're|were)\s+"
    r"(?P<activity>(?:watching|playing|debugging|working on|testing|using|reading)\s+.+?)(?:[.?!]|$)"
)
_TRYING_RE = re.compile(
    r"(?is)\b(?:i am|i'm|im)\s+trying\s+to\s+(?P<activity>.+?)(?:\bbut\b|[.?!]|$)"
)
_FOCUS_RE = re.compile(
    r"(?is)\bmy\s+(?:biggest|main|primary|current)\s+"
    r"(?:project|focus|priority)\s+(?:is(?:\s+currently)?\s+)?"
    r"(?P<activity>.+?)(?:[.?!]|$)"
)
_FRICTION_RE = re.compile(
    r"(?i)\b(not picking up|isn't picking up|isnt picking up|not working|doesn't work|doesnt work|"
    r"not going through|mishearing|misheard|not getting picked up|not being picked up)\b"
)


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "item"


def _clean_fragment(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^(?:no,\s*|well,\s*|so,\s*)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!?")
    return cleaned


def _query_tokens(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(query or "").lower())
        if len(token) > 2
    }


def _intent_match_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(token) > 2 and token not in _INTENT_MATCH_STOPWORDS
    }


@dataclass(frozen=True)
class TransientEntry:
    key: str
    kind: str
    label: str
    value: str
    confidence: str
    source_turn: str
    updated_at: int
    expires_at: int | None

    def as_payload(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "value": self.value,
            "confidence": self.confidence,
            "source_turn": self.source_turn,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }


class TransientStateManager:
    def __init__(
        self,
        *,
        situational_store: SituationalStateStore,
        intent_store: IntentStateStore,
        knowledge_mgr: "WorldModelManager | None" = None,
    ) -> None:
        self.situational_store = situational_store
        self.intent_store = intent_store
        self.knowledge_mgr = knowledge_mgr
        self._migrate_legacy_world_model_state()

    def ingest_user_turn(self, text: str) -> None:
        cleaned = _clean_fragment(text)
        if not cleaned:
            return

        self._migrate_legacy_world_model_state()
        self.situational_store.prune_expired()
        self.intent_store.prune_expired()

        for entry in self._extract_situational_entries(cleaned):
            self.situational_store.upsert_entry(entry.key, entry.as_payload())

        for entry in self._extract_intent_entries(cleaned):
            self.intent_store.upsert_entry(entry.key, entry.as_payload())

    def render_situational_state(self, query: str = "", *, max_items: int = 4) -> str:
        self._migrate_legacy_world_model_state()
        self.situational_store.prune_expired()
        entries = self._rank_entries(self.situational_store.load_active_entries().values(), query=query)
        if not entries:
            return ""

        lines = [
            "[SITUATIONAL STATE]",
            "These are temporary or recent user states that may matter for tone, empathy, or planning.",
        ]
        for entry in entries[: max(int(max_items), 1)]:
            lines.append(f"- {entry.get('label')}: {entry.get('value')}")
        return "\n".join(lines)

    def render_intent_state(self, query: str = "", *, max_items: int = 4) -> str:
        self.intent_store.prune_expired()
        entries = self._rank_entries(self.intent_store.load_active_entries().values(), query=query)
        if not entries:
            return ""

        lines = [
            "[INTENT STATE]",
            "These are soft user intentions or leanings. They are not confirmed tasks, events, or durable facts.",
        ]
        for entry in entries[: max(int(max_items), 1)]:
            confidence = str(entry.get("confidence") or "").strip().title() or "Active"
            lines.append(f"- {confidence}: {entry.get('value')}")
        return "\n".join(lines)

    def list_situational_entries(self) -> Dict[str, Dict[str, Any]]:
        self._migrate_legacy_world_model_state()
        self.situational_store.prune_expired()
        return self.situational_store.load_active_entries()

    def list_intent_entries(self) -> Dict[str, Dict[str, Any]]:
        self.intent_store.prune_expired()
        return self.intent_store.load_active_entries()

    def reconcile_operational_change(
        self,
        *,
        kind: str,
        action: str,
        name: str,
        source_text: str = "",
        scheduled_date: str = "",
    ) -> int:
        if str(kind or "").strip().lower() not in {"task", "event"}:
            return 0
        self.intent_store.prune_expired()
        active_entries = self.intent_store.load_active_entries()
        removed = 0
        for key, entry in active_entries.items():
            if not self._intent_matches_operational_target(
                entry,
                kind=kind,
                action=action,
                name=name,
                source_text=source_text,
                scheduled_date=scheduled_date,
            ):
                continue
            if self.intent_store.remove_entry(key):
                removed += 1
        return removed

    def _migrate_legacy_world_model_state(self) -> None:
        if self.knowledge_mgr is None or not hasattr(self.knowledge_mgr, "drain_legacy_situational_entries"):
            return
        migrated = list(self.knowledge_mgr.drain_legacy_situational_entries() or [])
        for item in migrated:
            value = _clean_fragment(str(item.get("value") or ""))
            if not value:
                continue
            key_hint = str(item.get("key") or item.get("label") or "legacy").strip()
            label = str(item.get("label") or "Legacy State").strip() or "Legacy State"
            updated_at = int(item.get("updated_at") or time.time())
            expires_at = item.get("expires_at")
            entry = TransientEntry(
                key=f"legacy:{_slugify(key_hint)}:{_slugify(value)}",
                kind="legacy",
                label=label,
                value=value,
                confidence="legacy",
                source_turn="legacy world model migration",
                updated_at=updated_at,
                expires_at=int(expires_at) if expires_at is not None else None,
            )
            self.situational_store.upsert_entry(entry.key, entry.as_payload())

    def _extract_situational_entries(self, text: str) -> List[TransientEntry]:
        now_ts = int(time.time())
        entries: List[TransientEntry] = []
        lowered = text.lower()

        for word in _STATE_WORDS:
            if re.search(rf"(?i)\b(?:i am|i'm|im|feeling)\b[^.?!]*\b{re.escape(word)}\b", lowered):
                entries.append(
                    TransientEntry(
                        key=f"state:{_slugify(word)}",
                        kind="condition",
                        label="Current State",
                        value=word,
                        confidence="explicit",
                        source_turn=text,
                        updated_at=now_ts,
                        expires_at=now_ts + 3 * 86400,
                    )
                )

        activity_match = _ACTIVITY_RE.search(text)
        if activity_match:
            activity = _clean_fragment(activity_match.group("activity"))
            if activity:
                entries.append(
                    TransientEntry(
                        key="activity:current",
                        kind="activity",
                        label="Current Activity",
                        value=activity,
                        confidence="explicit",
                        source_turn=text,
                        updated_at=now_ts,
                        expires_at=now_ts + 3 * 86400,
                    )
                )

        trying_match = _TRYING_RE.search(text)
        if trying_match:
            activity = "trying to " + _clean_fragment(trying_match.group("activity"))
            if activity:
                entries.append(
                    TransientEntry(
                        key="activity:trying",
                        kind="activity",
                        label="Current Activity",
                        value=activity,
                        confidence="explicit",
                        source_turn=text,
                        updated_at=now_ts,
                        expires_at=now_ts + 2 * 86400,
                    )
                )

        focus_match = _FOCUS_RE.search(text)
        if focus_match:
            activity = _clean_fragment(focus_match.group("activity"))
            if activity:
                entries.append(
                    TransientEntry(
                        key="focus:current",
                        kind="focus",
                        label="Current Focus",
                        value=activity,
                        confidence="explicit",
                        source_turn=text,
                        updated_at=now_ts,
                        expires_at=now_ts + 3 * 86400,
                    )
                )

        if _FRICTION_RE.search(text):
            entries.append(
                TransientEntry(
                    key=f"friction:{_slugify(text)}",
                    kind="friction",
                    label="Current Friction",
                    value=_clean_fragment(text),
                    confidence="explicit",
                    source_turn=text,
                    updated_at=now_ts,
                    expires_at=now_ts + 2 * 86400,
                )
            )

        return self._dedupe(entries)

    def _extract_intent_entries(self, text: str) -> List[TransientEntry]:
        if _EXPLICIT_ASSISTANT_REQUEST_RE.search(text):
            return []

        now_ts = int(time.time())
        entries: List[TransientEntry] = []
        for pattern, confidence in _TENTATIVE_INTENT_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            action = _clean_fragment(match.group("action"))
            if not action:
                continue
            entries.append(
                TransientEntry(
                    key=f"intent:{_slugify(action)}",
                    kind="intent",
                    label="Intent",
                    value=action,
                    confidence=confidence,
                    source_turn=text,
                    updated_at=now_ts,
                    expires_at=now_ts + (7 if confidence == "tentative" else 14) * 86400,
                )
            )
            break
        return self._dedupe(entries)

    def _intent_matches_operational_target(
        self,
        entry: Dict[str, Any],
        *,
        kind: str,
        action: str,
        name: str,
        source_text: str,
        scheduled_date: str,
    ) -> bool:
        if not isinstance(entry, dict):
            return False
        value = _clean_fragment(str(entry.get("value") or ""))
        if not value:
            return False

        candidate_texts = [
            _clean_fragment(name),
            _clean_fragment(source_text),
            _clean_fragment(scheduled_date),
        ]
        candidate_texts = [text for text in candidate_texts if text]
        if not candidate_texts:
            return False

        normalized_value = _slugify(value)
        for candidate in candidate_texts:
            normalized_candidate = _slugify(candidate)
            if normalized_candidate and (
                normalized_candidate in normalized_value or normalized_value in normalized_candidate
            ):
                return True

        value_tokens = _intent_match_tokens(value)
        if not value_tokens:
            return False
        target_tokens = set()
        for candidate in candidate_texts:
            target_tokens.update(_intent_match_tokens(candidate))
        if not target_tokens:
            return False

        overlap = value_tokens & target_tokens
        if not overlap:
            return False
        if value_tokens.issubset(target_tokens):
            return True
        if len(overlap) >= 2:
            return True
        if len(value_tokens) == 1 and len(target_tokens) == 1 and overlap:
            return True
        return False

    def _rank_entries(self, entries: Iterable[Dict[str, Any]], *, query: str) -> List[Dict[str, Any]]:
        ranked: List[tuple[int, int, str, Dict[str, Any]]] = []
        tokens = _query_tokens(query)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or "").strip()
            value = str(entry.get("value") or "").strip()
            if not label or not value:
                continue
            blob = f"{label} {value}".lower()
            relevance = 1 if tokens and any(token in blob for token in tokens) else 0
            updated_at = int(entry.get("updated_at") or 0)
            ranked.append((-relevance, -updated_at, label.lower(), entry))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        return [item[-1] for item in ranked]

    @staticmethod
    def _dedupe(entries: List[TransientEntry]) -> List[TransientEntry]:
        deduped: Dict[str, TransientEntry] = {}
        for entry in entries:
            deduped[entry.key] = entry
        return list(deduped.values())
