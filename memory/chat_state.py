"""core/chat_state.py

Chat state + memory glue.

Purpose:
- Keep app.py lean by owning:
  - in-memory message list
  - session marker + bootstrap gating
  - persistence to memory.jsonl
  - loading recent memory
  - providing the message list to feed the prompt builder

This module is intentionally UI-agnostic.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .storage import append_jsonl, load_recent_turns, now_ts

_LOG = logging.getLogger(__name__)


@dataclass
class ChatState:
    """In-memory chat + persistence helpers.

    Notes:
    - `bootstrap_pending` is used by the New button to inject the bootstrap transcript once.
    - `style_bootstrap_pending` is used when switching style mid-conversation:
        it should inject the style bootstrap transcript once, without clearing memory.
    """

    memory_path: Path
    session_marker_prefix: str = "=== New session"
    history_limit: int = 500

    messages: List[Dict[str, str]] = field(default_factory=list)
    
    # Thread safety lock
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _streaming_assistant_index: int | None = None

    # When True, prompt builder should include the bootstrap transcript once.
    bootstrap_pending: bool = False

    # When True, prompt builder should inject the *style* bootstrap transcript once
    # (mid-convo), without touching existing messages.
    style_bootstrap_pending: bool = False

    def load_recent_memory(self, *, limit: int = 50) -> None:
        loaded: List[Dict[str, str]] = []
        for t in load_recent_turns(self.memory_path, limit=limit):
            role = t.get("role")
            content = t.get("content")
            if role and content:
                loaded.append({"role": role, "content": content})
        if not loaded:
            return
        with self._lock:
            self.messages.extend(loaded)

    def persist_turn(self, role: str, content: str) -> None:
        append_jsonl(
            self.memory_path,
            {"ts": now_ts(), "role": role, "content": content},
            max_lines=self.history_limit,
        )

    def append(self, role: str, content: str) -> None:
        with self._lock:
            if str(role or "").lower() == "user":
                self._drop_orphaned_streaming_assistant_locked()
            self.messages.append({"role": role, "content": content})

    def append_message(self, message: Dict[str, Any]) -> None:
        with self._lock:
            self.messages.append(dict(message))

    def upsert_hidden_system_message(self, prefix: str, content: str) -> None:
        text = str(content or "").strip()
        marker = str(prefix or "").strip()
        if not text or not marker:
            return
        payload = {"role": "system", "content": text, "hidden": True}
        with self._lock:
            for i in range(len(self.messages) - 1, -1, -1):
                message = self.messages[i]
                if str(message.get("role") or "").lower() != "system":
                    continue
                if str(message.get("content") or "").startswith(marker):
                    self.messages[i] = payload
                    return
            self.messages.append(payload)

    def remove_hidden_system_message(self, prefix: str) -> None:
        marker = str(prefix or "").strip()
        if not marker:
            return
        with self._lock:
            self.messages = [
                dict(message)
                for message in self.messages
                if not (
                    str(message.get("role") or "").lower() == "system"
                    and bool(message.get("hidden"))
                    and str(message.get("content") or "").startswith(marker)
                )
            ]

    def upsert_streaming_assistant(self, text: str) -> None:
        with self._lock:
            if not text:
                return
            active_index = self._streaming_assistant_index
            if (
                active_index is None
                or active_index < 0
                or active_index >= len(self.messages)
                or self.messages[active_index].get("role") != "assistant"
            ):
                self.messages.append({"role": "assistant", "content": ""})
                self._streaming_assistant_index = len(self.messages) - 1
                active_index = self._streaming_assistant_index
            self.messages[active_index]["content"] = text

    def finalize_streaming_assistant(self) -> None:
        with self._lock:
            self._streaming_assistant_index = None
        
    def clear(self) -> None:
        with self._lock:
            self.messages.clear()
            self.bootstrap_pending = False
            self.style_bootstrap_pending = False
            self._streaming_assistant_index = None

    def bind_memory_path(self, memory_path: Path) -> None:
        self.memory_path = Path(memory_path)

    def begin_fresh_session(self, *, wipe_persistent: bool) -> None:
        with self._lock:
            self.messages.clear()
            self.messages.append({"role": "system", "content": self.session_marker_prefix})
            self.bootstrap_pending = True
            self.style_bootstrap_pending = False
            self._streaming_assistant_index = None

        if not wipe_persistent:
            return

        try:
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            with self.memory_path.open("w", encoding="utf-8") as f:
                f.write("")
        except Exception as e:
            _LOG.warning("[ChatState] Error wiping memory file: %s", e)

    def new_session(self) -> None:
        """Start a fresh session.

        Adds a session marker message and arms bootstrap_pending.
        IMPORTANT: This now WIPES the memory.jsonl file to prevent 'Zombie Memory'.
        """
        self.begin_fresh_session(wipe_persistent=True)

    def arm_style_bootstrap(self) -> None:
        """Arm a one-time style bootstrap injection for the next prompt build.

        This is used by style switching mid-conversation.
        It does NOT mutate messages, and it does NOT clear memory.
        """
        with self._lock:
            self.style_bootstrap_pending = True

    def get_messages_snapshot(self) -> List[Dict[str, str]]:
        """Returns a thread-safe copy of messages for UI rendering."""
        with self._lock:
            return list(self.messages)

    def recent_messages(self, limit: int) -> List[Dict[str, str]]:
        with self._lock:
            return list(self.messages[-limit:])

    def for_model(self) -> List[Dict[str, str]]:
        """Return messages to feed the prompt builder.

        - If bootstrap is pending, include marker once.
        - Otherwise, filter session markers so they don't re-trigger bootstrap.

        Note:
        - style bootstrap injection is handled by the prompt builder using
          `style_bootstrap_pending` and does not require markers.
        """
        if self.bootstrap_pending:
            return list(self.messages)

        out: List[Dict[str, str]] = []
        for m in self.messages:
            role = (m.get("role") or "").lower()
            content = (m.get("content") or "")
            if role == "system" and content.strip().startswith(self.session_marker_prefix):
                continue
            out.append(m)
        return out
    
    def replace_last_system_message(self, search_content: str, replacement_message: Dict[str, str]) -> bool:
        """Thread-safe search and replace for the Orchestrator."""
        with self._lock:
            for i in range(len(self.messages) - 1, -1, -1):
                message = self.messages[i]
                if message.get("role") == "system" and message.get("content") == search_content:
                    self.messages[i] = replacement_message
                    return True
            return False

    def replace_last_assistant_content(self, content: str) -> bool:
        if not content or not content.strip():
            return False
        with self._lock:
            for i in range(len(self.messages) - 1, -1, -1):
                if self.messages[i].get("role") == "assistant":
                    self.messages[i]["content"] = content
                    return True
        return False

    def remove_last_assistant_if_exact(self, content: str) -> bool:
        target = str(content or "")
        with self._lock:
            for i in range(len(self.messages) - 1, -1, -1):
                if self.messages[i].get("role") != "assistant":
                    continue
                if str(self.messages[i].get("content") or "") == target:
                    self.messages.pop(i)
                    if self._streaming_assistant_index == i:
                        self._streaming_assistant_index = None
                    elif (
                        self._streaming_assistant_index is not None
                        and i < self._streaming_assistant_index
                    ):
                        self._streaming_assistant_index -= 1
                    return True
                return False
        return False

    def _drop_orphaned_streaming_assistant_locked(self) -> None:
        active_index = self._streaming_assistant_index
        if active_index is None:
            return
        if (
            active_index < 0
            or active_index >= len(self.messages)
            or self.messages[active_index].get("role") != "assistant"
        ):
            self._streaming_assistant_index = None
            return
        self.messages.pop(active_index)
        self._streaming_assistant_index = None
