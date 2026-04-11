from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)


class JsonStoreError(RuntimeError):
    pass


class JsonDictStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.backup_path = self.path.with_suffix(f"{self.path.suffix}.bak")

    def _load_dict_file(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise JsonStoreError(f"Expected JSON object in {path}")
        return data

    def _atomic_write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.stem}.",
            suffix=f"{path.suffix}.tmp",
            text=True,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def _archive_corrupt_primary(self) -> Path | None:
        if not self.path.exists():
            return None
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        archived = self.path.with_name(f"{self.path.stem}.corrupt_{stamp}{self.path.suffix}")
        try:
            self.path.replace(archived)
        except Exception:
            return None
        return archived

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return self._load_dict_file(self.path)
        except Exception as exc:
            if self.backup_path.exists():
                try:
                    data = self._load_dict_file(self.backup_path)
                    archived = self._archive_corrupt_primary()
                    self._atomic_write_text(
                        self.path,
                        json.dumps(data, indent=2, ensure_ascii=False),
                    )
                    if archived is not None:
                        _LOG.warning(
                            "[JsonStore] Recovered %s from backup. Archived corrupt copy to %s.",
                            self.path.name,
                            archived.name,
                        )
                    return data
                except Exception as backup_exc:
                    raise JsonStoreError(
                        f"Failed to load {self.path} and backup {self.backup_path}: "
                        f"{exc} | backup: {backup_exc}"
                    ) from backup_exc
            raise JsonStoreError(f"Failed to load JSON store {self.path}: {exc}") from exc

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        with self._lock:
            self._atomic_write_text(self.path, payload)
            self._atomic_write_text(self.backup_path, payload)


class TaskStore(JsonDictStore):
    def add(self, task_name: str, status: str = "pending") -> None:
        data = self.load()
        data[task_name] = status
        self.save(data)

    def pop(self, task_name: str) -> Optional[str]:
        data = self.load()
        if task_name not in data:
            return None
        value = str(data.pop(task_name))
        self.save(data)
        return value

    def remove(self, task_name: str) -> bool:
        data = self.load()
        if task_name not in data:
            return False
        del data[task_name]
        self.save(data)
        return True

    def pending_names(self) -> List[str]:
        data = self.load()
        return [name for name, status in data.items() if status == "pending"]

    def as_structured(self) -> List[Dict[str, str]]:
        data = self.load()
        return [{"name": name, "status": str(status)} for name, status in data.items()]


class EventStore(JsonDictStore):
    @staticmethod
    def _parse_entry(value: Any) -> tuple[str, Optional[str]]:
        """Return (date_str, time_str|None) from a stored event value."""
        if isinstance(value, dict):
            return str(value.get("date") or ""), value.get("time") or None
        return str(value or ""), None

    def add(self, name: str, date_str: str, time_str: Optional[str] = None) -> None:
        data = self.load()
        if time_str:
            data[name] = {"date": date_str, "time": time_str}
        else:
            # Preserve existing time if entry already has one
            existing = data.get(name)
            if isinstance(existing, dict) and existing.get("time"):
                data[name] = {"date": date_str, "time": existing["time"]}
            else:
                data[name] = date_str
        self.save(data)

    def pop(self, name: str) -> Optional[str]:
        data = self.load()
        if name not in data:
            return None
        value = str(data.pop(name))
        self.save(data)
        return value

    def remove(self, name: str) -> bool:
        data = self.load()
        if name not in data:
            return False
        del data[name]
        self.save(data)
        return True

    def upcoming(self, *, now: Optional[_dt.datetime] = None) -> List[Dict[str, str]]:
        data = self.load()
        current = now or _dt.datetime.now()
        items: List[Dict[str, str]] = []
        for name, raw in data.items():
            date_str, time_str = self._parse_entry(raw)
            try:
                event_date = _dt.datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                continue
            if event_date.date() >= current.date():
                item: Dict[str, str] = {"name": name, "date": date_str}
                if time_str:
                    item["time"] = time_str
                items.append(item)
        items.sort(key=lambda item: (item["date"], item.get("time") or ""))
        return items

    def cleanup_old_events(self, *, now: Optional[_dt.datetime] = None) -> int:
        current = now or _dt.datetime.now()
        data = self.load()
        valid: Dict[str, Any] = {}
        removed = 0
        for name, raw in data.items():
            date_str, _ = self._parse_entry(raw)
            try:
                event_date = _dt.datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                valid[name] = raw
                continue
            if event_date.date() >= current.date():
                valid[name] = raw
            else:
                removed += 1
        if removed:
            self.save(valid)
        return removed


class KnowledgeStore(JsonDictStore):
    _TRANSIENT_KEY_PREFIXES = ("pending_", "temporary_", "temp_", "current_", "recent_", "latest_")
    _DEFAULT_TRANSIENT_TTL_S = 14 * 86400

    @staticmethod
    def _is_entry_active(entry: Any, *, now_ts: Optional[float] = None) -> bool:
        if isinstance(entry, str):
            return True
        if not isinstance(entry, dict):
            return False
        current = time.time() if now_ts is None else now_ts
        expires_at = entry.get("expires_at")
        return expires_at is None or expires_at > current

    def load_active(self) -> Dict[str, Any]:
        data = self.load()
        now_ts = time.time()
        active: Dict[str, Any] = {}
        for key, entry in data.items():
            if isinstance(entry, str):
                active[key] = {"value": entry, "expires_at": None}
                continue
            if self._is_entry_active(entry, now_ts=now_ts):
                active[key] = entry
        return active

    def upsert_value(self, key: str, value: str, *, expires_at: Optional[int] = None) -> None:
        data = self.load()
        if expires_at is None:
            existing = data.get(key)
            if isinstance(existing, dict) and self._is_entry_active(existing):
                expires_at = existing.get("expires_at")
            elif any(str(key).strip().lower().startswith(prefix) for prefix in self._TRANSIENT_KEY_PREFIXES):
                expires_at = int(time.time()) + self._DEFAULT_TRANSIENT_TTL_S
        data[key] = {"value": value, "expires_at": expires_at}
        self.save(data)


class StructuredEntryStore(JsonDictStore):
    SCHEMA_VERSION = 1

    @classmethod
    def default_payload(cls) -> Dict[str, Any]:
        now_ts = int(time.time())
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "entries": {},
            "metadata": {
                "created_at": now_ts,
                "updated_at": now_ts,
            },
        }

    def load_payload(self) -> Dict[str, Any]:
        data = self.load()
        if not data:
            return self.default_payload()

        payload = self.default_payload()
        payload["schema_version"] = int(data.get("schema_version") or self.SCHEMA_VERSION)

        entries = data.get("entries")
        if isinstance(entries, dict):
            payload["entries"] = {
                str(key): value
                for key, value in entries.items()
                if isinstance(value, dict)
            }

        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            payload["metadata"].update(metadata)
        return payload

    def save_payload(self, payload: Dict[str, Any]) -> None:
        data = self.default_payload()
        if isinstance(payload, dict):
            data.update(payload)
        data["schema_version"] = int(data.get("schema_version") or self.SCHEMA_VERSION)
        entries = data.get("entries")
        if not isinstance(entries, dict):
            data["entries"] = {}
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            data["metadata"] = {}
        data["metadata"]["updated_at"] = int(time.time())
        self.save(data)

    def load_active_entries(self) -> Dict[str, Dict[str, Any]]:
        payload = self.load_payload()
        now_ts = int(time.time())
        active: Dict[str, Dict[str, Any]] = {}
        for key, entry in (payload.get("entries") or {}).items():
            expires_at = entry.get("expires_at")
            if expires_at is not None:
                try:
                    if int(expires_at) <= now_ts:
                        continue
                except Exception:
                    continue
            active[str(key)] = dict(entry)
        return active

    def upsert_entry(self, key: str, entry: Dict[str, Any]) -> None:
        payload = self.load_payload()
        entries = dict(payload.get("entries") or {})
        entries[str(key)] = dict(entry)
        payload["entries"] = entries
        self.save_payload(payload)

    def remove_entry(self, key: str) -> bool:
        payload = self.load_payload()
        entries = dict(payload.get("entries") or {})
        if str(key) not in entries:
            return False
        del entries[str(key)]
        payload["entries"] = entries
        self.save_payload(payload)
        return True

    def prune_expired(self) -> int:
        payload = self.load_payload()
        entries = dict(payload.get("entries") or {})
        now_ts = int(time.time())
        kept: Dict[str, Dict[str, Any]] = {}
        removed = 0
        for key, entry in entries.items():
            expires_at = entry.get("expires_at")
            if expires_at is not None:
                try:
                    if int(expires_at) <= now_ts:
                        removed += 1
                        continue
                except Exception:
                    removed += 1
                    continue
            kept[key] = entry
        if removed:
            payload["entries"] = kept
            self.save_payload(payload)
        return removed


class SituationalStateStore(StructuredEntryStore):
    pass


class IntentStateStore(StructuredEntryStore):
    # Maximum lifetime for any intent entry.  Entries without an explicit
    # expires_at (e.g. migrated from a previous session or written by an older
    # code path) are capped to this TTL measured from their updated_at timestamp.
    DEFAULT_TTL_SECONDS: int = 2 * 86400  # 2 days

    def _resolve_expires_at(self, entry: Dict[str, Any]) -> "int | None":
        """Return the effective expiry timestamp for an entry.

        If the entry carries an explicit ``expires_at`` it is used as-is.
        Otherwise the expiry is derived from ``updated_at + DEFAULT_TTL_SECONDS``
        so entries without an explicit TTL do not accumulate indefinitely.
        """
        expires_at = entry.get("expires_at")
        if expires_at is None:
            updated_at = entry.get("updated_at")
            if updated_at is not None:
                try:
                    return int(updated_at) + self.DEFAULT_TTL_SECONDS
                except Exception:
                    return None
        return expires_at

    def load_active_entries(self) -> Dict[str, Dict[str, Any]]:
        payload = self.load_payload()
        now_ts = int(time.time())
        active: Dict[str, Dict[str, Any]] = {}
        for key, entry in (payload.get("entries") or {}).items():
            expires_at = self._resolve_expires_at(entry)
            if expires_at is not None:
                try:
                    if int(expires_at) <= now_ts:
                        continue
                except Exception:
                    continue
            active[str(key)] = dict(entry)
        return active

    def prune_expired(self) -> int:
        """Override to apply DEFAULT_TTL_SECONDS to entries without explicit expires_at."""
        payload = self.load_payload()
        entries = dict(payload.get("entries") or {})
        now_ts = int(time.time())
        kept: Dict[str, Dict[str, Any]] = {}
        removed = 0
        for key, entry in entries.items():
            expires_at = self._resolve_expires_at(entry)
            if expires_at is not None:
                try:
                    if int(expires_at) <= now_ts:
                        removed += 1
                        continue
                except Exception:
                    removed += 1
                    continue
            kept[key] = entry
        if removed:
            payload["entries"] = kept
            self.save_payload(payload)
        return removed


class WorldModelStore(JsonDictStore):
    SCHEMA_VERSION = 1
    ROOT_ENTITY_ID = "person:user"

    @classmethod
    def default_graph(cls) -> Dict[str, Any]:
        now_ts = int(time.time())
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "root_entity_id": cls.ROOT_ENTITY_ID,
            "nodes": {
                cls.ROOT_ENTITY_ID: {
                    "id": cls.ROOT_ENTITY_ID,
                    "type": "person",
                    "label": "User",
                    "aliases": ["user", "me"],
                    "attributes": {},
                    "updated_at": now_ts,
                }
            },
            "edges": [],
            "metadata": {
                "created_at": now_ts,
                "updated_at": now_ts,
                "migrated_from_legacy_knowledge": False,
            },
        }

    def load_graph(self) -> Dict[str, Any]:
        data = self.load()
        if not data:
            return self.default_graph()

        graph = self.default_graph()
        graph["schema_version"] = int(data.get("schema_version") or self.SCHEMA_VERSION)
        graph["root_entity_id"] = str(data.get("root_entity_id") or self.ROOT_ENTITY_ID)

        nodes = data.get("nodes")
        if isinstance(nodes, dict):
            graph["nodes"] = nodes
        if graph["root_entity_id"] not in graph["nodes"]:
            graph["nodes"][graph["root_entity_id"]] = self.default_graph()["nodes"][self.ROOT_ENTITY_ID]

        edges = data.get("edges")
        if isinstance(edges, list):
            graph["edges"] = [item for item in edges if isinstance(item, dict)]

        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            graph["metadata"].update(metadata)
        graph["metadata"]["updated_at"] = int(graph["metadata"].get("updated_at") or int(time.time()))
        return graph

    def save_graph(self, graph: Dict[str, Any]) -> None:
        payload = self.default_graph()
        if isinstance(graph, dict):
            payload.update(graph)
        payload["schema_version"] = int(payload.get("schema_version") or self.SCHEMA_VERSION)
        payload["root_entity_id"] = str(payload.get("root_entity_id") or self.ROOT_ENTITY_ID)
        if not isinstance(payload.get("nodes"), dict):
            payload["nodes"] = {}
        if payload["root_entity_id"] not in payload["nodes"]:
            payload["nodes"][payload["root_entity_id"]] = self.default_graph()["nodes"][self.ROOT_ENTITY_ID]
        if not isinstance(payload.get("edges"), list):
            payload["edges"] = []
        if not isinstance(payload.get("metadata"), dict):
            payload["metadata"] = {}
        payload["metadata"]["updated_at"] = int(time.time())
        self.save(payload)
