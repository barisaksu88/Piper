from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from config import data_state_path
from memory.stores import JsonDictStore


@dataclass(frozen=True)
class CodexRepairStateStore:
    data_dir: Path
    request_store: JsonDictStore
    status_store: JsonDictStore
    recovery_store: JsonDictStore

    @classmethod
    def for_data_dir(cls, data_dir: Path) -> "CodexRepairStateStore":
        base = Path(data_dir)
        return cls(
            data_dir=base,
            request_store=JsonDictStore(data_state_path(base, "codex_repair_request.json")),
            status_store=JsonDictStore(data_state_path(base, "codex_repair_status.json")),
            recovery_store=JsonDictStore(data_state_path(base, "codex_recovery.json")),
        )

    def load_request(self) -> Dict[str, Any]:
        return self.request_store.load()

    def save_request(self, payload: Dict[str, Any]) -> None:
        self.request_store.save(dict(payload or {}))

    def clear_request(self) -> None:
        self.request_store.save({})

    def load_status(self) -> Dict[str, Any]:
        return self.status_store.load()

    def save_status(self, payload: Dict[str, Any]) -> None:
        self.status_store.save(dict(payload or {}))

    def clear_status(self) -> None:
        self.status_store.save({})

    def load_recovery(self) -> Dict[str, Any]:
        return self.recovery_store.load()

    def save_recovery(self, payload: Dict[str, Any]) -> None:
        self.recovery_store.save(dict(payload or {}))

    def clear_recovery(self) -> None:
        self.recovery_store.save({})
