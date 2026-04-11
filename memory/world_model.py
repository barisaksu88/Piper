"""Graph-backed world model memory with a legacy knowledge mirror."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from core.feature_hooks import register_hook
from .knowledge_policy import (
    PROFILE_REFRESH_EVERY_CALLS,
    default_expiry_for_transient_fact,
    fact_is_grounded,
    history_contains_world_model_candidate,
    history_contains_explicit_profile_disclosure,
    history_digest,
    history_user_text,
    profile_fact_shape_is_allowed,
    profile_update_is_grounded,
    resolve_fact_expiry,
)
from .knowledge_prompts import build_memory_archivist_prompt
from .stores import KnowledgeStore, WorldModelStore
from .world_model_prompts import build_world_model_extraction_prompt

if TYPE_CHECKING:
    from llm.llm_server_client import LlamaServerClient


_SINGULAR_ATTRIBUTES = {"name", "birth_date", "date_of_birth", "gender", "location"}
_ROOT_ATTRIBUTE_LABELS = {
    "name": "Name",
    "location": "Location",
    "occupation": "Occupation",
    "job": "Occupation",
    "likes/interests": "Likes/Interests",
    "likes_interests": "Likes/Interests",
    "future plans": "Future Plans",
    "future_plans": "Future Plans",
    "date of birth": "Birthday",
    "date_of_birth": "Birthday",
    # canonical storage key — label is "Birthday" not "Date of Birth" to avoid the model
    # confusing it with today's date when the user asks "what's the date?"
    "birth_date": "Birthday",
    "vehicle": "Vehicle",
    "gender": "Gender",
}
_ROOT_CANONICAL_MAP = {
    "likes/interests": "likes_interests",
    "likes_interests": "likes_interests",
    "future plans": "future_plans",
    "future_plans": "future_plans",
    "date of birth": "birth_date",
    "date_of_birth": "birth_date",
    "job": "occupation",
}
_RELATION_DISPLAY = {
    "child": "child",
    "partner": "partner",
    "friend": "friend",
    "parent": "parent",
    "works_on": "works on",
    "owns": "owns",
    "lives_in": "lives in",
}
_TRANSIENT_ATTRIBUTE_PREFIXES = ("pending_", "temporary_", "temp_", "current_", "recent_", "latest_")
_TEMP_WORKSPACE_RELATIONS = {"works_on"}
_TEMP_WORKSPACE_ATTRIBUTE_NAMES = {"file_name", "path", "script_path", "workspace_path"}
_FILELIKE_VALUE_RE = re.compile(r"(?:^|[/\\])[^/\\]+\.[a-z0-9]{1,8}$|^[^/\\]+\.[a-z0-9]{1,8}$", re.IGNORECASE)
_DISTINCT_SAME_NAME_RE = re.compile(
    r"\b(different people|different person|not the same person|same name|two .* with the same name)\b",
    re.IGNORECASE,
)


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "item"


def _canonical_attribute_name(name: str) -> str:
    raw = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    raw = raw.replace("/", "_")
    return _ROOT_CANONICAL_MAP.get(raw, raw)


def _display_attribute_name(name: str) -> str:
    key = _canonical_attribute_name(name)
    return _ROOT_ATTRIBUTE_LABELS.get(key, key.replace("_", " ").title())


def _canonical_relation(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_") or "related_to"


def _relation_name_from_display(name: str) -> str:
    target = str(name or "").strip().lower()
    if not target:
        return ""
    for relation_key, display in _RELATION_DISPLAY.items():
        if target == str(display or "").strip().lower():
            return relation_key
    return _canonical_relation(target)


def _normalize_aliases(values: Iterable[str]) -> List[str]:
    aliases: List[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        aliases.append(value)
    return aliases


def _node_type_from_key(key: str) -> str:
    lowered = str(key or "").strip().lower()
    if lowered in {"vehicle", "car"}:
        return "device"
    if lowered in {"location", "place"}:
        return "place"
    return "person"


def _entity_id(entity_type: str, label: str) -> str:
    return f"{_slugify(entity_type)}:{_slugify(label)}"


def _active_entry(entry: Dict[str, Any], *, now_ts: Optional[float] = None) -> bool:
    current = time.time() if now_ts is None else now_ts
    expires_at = entry.get("expires_at")
    return expires_at is None or float(expires_at) > current


def _active_values(entries: Any) -> List[Dict[str, Any]]:
    if not isinstance(entries, list):
        return []
    current = time.time()
    return [entry for entry in entries if isinstance(entry, dict) and _active_entry(entry, now_ts=current)]


def _entry_values(entries: Any) -> List[str]:
    values: List[str] = []
    for entry in _active_values(entries):
        value = str(entry.get("value") or "").strip()
        if value:
            values.append(value)
    return values


def _query_tokens(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(query or "").lower())
        if len(token) > 2
    }


def _attribute_is_prompt_relevant(name: str, entries: Any, *, query: str) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return False
    haystacks = [str(name or "").lower()]
    for entry in _active_values(entries):
        value = str(entry.get("value") or "").lower()
        if value:
            haystacks.append(value)
    blob = " ".join(haystacks)
    return any(token in blob for token in tokens)


def _attribute_should_render_in_prompt(name: str, entries: Any, *, query: str) -> bool:
    active_entries = _active_values(entries)
    if not active_entries:
        return False
    canonical = _canonical_attribute_name(name)
    if any(canonical.startswith(prefix) for prefix in _TRANSIENT_ATTRIBUTE_PREFIXES):
        return _attribute_is_prompt_relevant(canonical, active_entries, query=query)
    if any(entry.get("expires_at") is not None for entry in active_entries):
        return _attribute_is_prompt_relevant(canonical, active_entries, query=query)
    return True


def _attribute_is_situational(name: str, entries: Any) -> bool:
    active_entries = _active_values(entries)
    if not active_entries:
        return False
    canonical = _canonical_attribute_name(name)
    if any(canonical.startswith(prefix) for prefix in _TRANSIENT_ATTRIBUTE_PREFIXES):
        return True
    return any(entry.get("expires_at") is not None for entry in active_entries)


def _infer_relation(description: str) -> str | None:
    lowered = str(description or "").lower()
    if "daughter" in lowered or "son" in lowered:
        return "child"
    if any(token in lowered for token in ("girlfriend", "boyfriend", "wife", "husband", "partner")):
        return "partner"
    if "friend" in lowered:
        return "friend"
    return None


def _relation_description(relation: str, label: str) -> str:
    relation_key = _canonical_relation(relation)
    if relation_key == "child":
        return f"Daughter named {label}"
    if relation_key == "partner":
        return f"Partner named {label}"
    if relation_key == "friend":
        return f"Friend named {label}"
    display = _RELATION_DISPLAY.get(relation_key, relation_key.replace("_", " "))
    return f"{display.title()} {label}"


def _relation_scoped_entity_id(entity_type: str, label: str, relation_name: str) -> str:
    return _entity_id(entity_type, f"{label} {relation_name}")


def _label_matches_node(node: Dict[str, Any], label: str, entity_type: str = "") -> bool:
    candidate = str(label or "").strip().lower()
    if not candidate or not isinstance(node, dict):
        return False
    if entity_type and str(node.get("type") or "").strip().lower() != str(entity_type or "").strip().lower():
        return False
    node_label = str(node.get("label") or "").strip().lower()
    if candidate == node_label:
        return True
    aliases = {str(item).strip().lower() for item in (node.get("aliases") or [])}
    return candidate in aliases


def _history_indicates_distinct_same_name_entities(history_text: str, label: str) -> bool:
    text = str(history_text or "").lower()
    candidate = str(label or "").strip().lower()
    if not text or not candidate:
        return False
    if candidate not in text:
        return False
    if _DISTINCT_SAME_NAME_RE.search(text):
        return True
    return bool(
        re.search(rf"\b(guy friend|male friend|friend)\s+{re.escape(candidate)}\b", text)
        and re.search(rf"\b(girlfriend|boyfriend|partner)\s+{re.escape(candidate)}\b", text)
    )


def _looks_like_workspace_artifact_value(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(_FILELIKE_VALUE_RE.search(text))


class WorldModelManager:
    def __init__(
        self,
        data_dir: Path,
        llm_client: "LlamaServerClient",
        *,
        world_model_store: WorldModelStore,
        knowledge_store: Optional[KnowledgeStore] = None,
        memory_brain: Any | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.llm = llm_client
        self.store = world_model_store
        self.world_model_path = self.store.path
        self.knowledge_store = knowledge_store
        self.knowledge_path = self.knowledge_store.path if self.knowledge_store is not None else None
        self.memory_brain = memory_brain
        self._lock = threading.Lock()
        self.log_callback = None
        self._graph_saved_callback = None
        self._profile_refresh_counter = 0
        self._last_profile_digest = ""

        self.store.save_graph(self.store.load_graph())
        self._migrate_legacy_knowledge_if_needed()
        self._scrub_graph_memory()
        self._normalize_graph_memory()
        self._sync_legacy_knowledge_mirror()

    def set_logger(self, callback) -> None:
        self.log_callback = callback

    def set_graph_saved_callback(self, callback) -> None:
        self._graph_saved_callback = callback

    def _log(self, text: str) -> None:
        if self.log_callback:
            self.log_callback(text)

    def load_graph(self) -> Dict[str, Any]:
        return self.store.load_graph()

    def load(self) -> Dict[str, Any]:
        return self._flatten_legacy_knowledge(self.load_graph())

    def render_prompt_state(self, query: str, *, max_entities: int = 6) -> str:
        graph = self.load_graph()
        root_id = str(graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        nodes = graph.get("nodes") or {}
        root = nodes.get(root_id) or {}
        if not isinstance(root, dict):
            return ""

        selected_ids = self._select_relevant_entities(graph, query, max_entities=max_entities)
        lines: List[str] = ["[WORLD STATE]"]

        root_block = self._render_node_block(graph, root_id, include_relations=True, query=query)
        if root_block:
            lines.extend(root_block)

        for entity_id in selected_ids:
            if entity_id == root_id:
                continue
            block = self._render_node_block(graph, entity_id, include_relations=False, query=query)
            if block:
                lines.append("")
                lines.extend(block)

        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    def render_situational_state(self, query: str = "", *, max_items: int = 4) -> str:
        graph = self.load_graph()
        root_id = str(graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        nodes = graph.get("nodes") or {}
        root = nodes.get(root_id) or {}
        if not isinstance(root, dict):
            return ""

        ranked: list[tuple[int, int, str, str]] = []
        for name, entries in (root.get("attributes") or {}).items():
            if not _attribute_is_situational(name, entries):
                continue
            values = _entry_values(entries)
            if not values:
                continue
            active_entries = _active_values(entries)
            expires = [
                int(entry.get("expires_at"))
                for entry in active_entries
                if entry.get("expires_at") is not None
            ]
            earliest_expiry = min(expires) if expires else (2**31 - 1)
            relevance = 1 if _attribute_is_prompt_relevant(name, entries, query=query) else 0
            ranked.append(
                (
                    -relevance,
                    earliest_expiry,
                    _display_attribute_name(name),
                    "; ".join(values),
                )
            )

        if not ranked:
            return ""

        ranked.sort(key=lambda item: (item[0], item[1], item[2].lower()))
        lines = [
            "[SITUATIONAL STATE]",
            "These are temporary or recent user states that may matter for tone, empathy, or planning.",
        ]
        for _, _, label, value in ranked[: max(int(max_items), 1)]:
            lines.append(f"- {label}: {value}")
        return "\n".join(lines)

    def drain_legacy_situational_entries(self) -> List[Dict[str, Any]]:
        graph = self.load_graph()
        root_id = str(graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        nodes = graph.get("nodes") or {}
        root = nodes.get(root_id) or {}
        if not isinstance(root, dict):
            return []

        attributes = root.get("attributes") or {}
        if not isinstance(attributes, dict):
            return []

        drained: List[Dict[str, Any]] = []
        kept: Dict[str, Any] = {}
        changed = False

        for name, entries in attributes.items():
            if not _attribute_is_situational(name, entries):
                kept[name] = entries
                continue
            changed = True
            for entry in _active_values(entries):
                value = str(entry.get("value") or "").strip()
                if not value:
                    continue
                drained.append(
                    {
                        "key": _canonical_attribute_name(name),
                        "label": _display_attribute_name(name),
                        "value": value,
                        "updated_at": int(entry.get("updated_at") or time.time()),
                        "expires_at": entry.get("expires_at"),
                    }
                )

        if changed:
            root["attributes"] = kept
            self._save_graph(graph)
            if drained:
                self._log("[WorldModel] Migrated situational entries out of world_model.json.")
        return drained

    def list_for_display(self) -> str:
        rendered = self.render_prompt_state("", max_entities=8)
        if not rendered:
            return "No world model stored."
        return rendered

    def upsert_fact(self, key: str, value: str) -> bool:
        fact_key = str(key or "").strip()
        fact_value = str(value or "").strip()
        if not fact_key or not fact_value:
            return False

        graph = self.load_graph()
        root = self._ensure_root(graph)
        attr_name = _canonical_attribute_name(fact_key)
        mode = "set" if attr_name in _SINGULAR_ATTRIBUTES else "add"
        changed = self._apply_attribute_operation(root, attr_name, fact_value, mode=mode, expires_at=None)
        if attr_name == "name":
            root["label"] = fact_value
            root["aliases"] = _normalize_aliases([*root.get("aliases", []), fact_value, "user", "me"])
            changed = True
        if changed:
            self._save_graph(graph)
        return changed

    def remove_fact(self, key: str) -> bool:
        target = str(key or "").strip()
        if not target:
            return False

        graph = self.load_graph()
        root_id = str(graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        root = self._ensure_root(graph)
        removed_rendered_fact = self._remove_rendered_fact(graph, root_id=root_id, target=target)
        if removed_rendered_fact:
            self._save_graph(graph)
            return True
        attr_name = _canonical_attribute_name(target)
        changed = False

        if attr_name in root.get("attributes", {}):
            del root["attributes"][attr_name]
            changed = True
            if attr_name == "name":
                root["label"] = "User"
                root["aliases"] = _normalize_aliases(["user", "me"])

        if not changed:
            nodes = graph.get("nodes") or {}
            candidate_id = None
            for node_id, node in nodes.items():
                if node_id == root_id or not isinstance(node, dict):
                    continue
                label = str(node.get("label") or "").strip().lower()
                aliases = {str(item).strip().lower() for item in (node.get("aliases") or [])}
                if target.lower() == label or target.lower() in aliases:
                    candidate_id = node_id
                    break
            if candidate_id is not None:
                graph["edges"] = [
                    edge
                    for edge in (graph.get("edges") or [])
                    if str(edge.get("source") or "") != candidate_id
                    and str(edge.get("target") or "") != candidate_id
                ]
                nodes.pop(candidate_id, None)
                changed = True

        if changed:
            self._save_graph(graph)
        return changed

    def _remove_rendered_fact(self, graph: Dict[str, Any], *, root_id: str, target: str) -> bool:
        match = re.match(r"^\s*([^:]+):\s*(.+?)\s*$", str(target or ""))
        if not match:
            return False

        label = str(match.group(1) or "").strip()
        value = str(match.group(2) or "").strip()
        if not label or not value:
            return False

        root = self._ensure_root(graph)
        attributes = root.get("attributes") or {}
        attr_name = _canonical_attribute_name(label)
        if attr_name in attributes:
            original_entries = attributes.get(attr_name)
            if isinstance(original_entries, list):
                kept_entries = [
                    entry
                    for entry in original_entries
                    if str((entry or {}).get("value") or "").strip().lower() != value.lower()
                ]
                if len(kept_entries) != len(original_entries):
                    if kept_entries:
                        attributes[attr_name] = kept_entries
                    else:
                        attributes.pop(attr_name, None)
                    root["updated_at"] = int(time.time())
                    return True

        relation_name = _relation_name_from_display(label)
        if not relation_name:
            return False

        nodes = graph.get("nodes") or {}
        edges = graph.get("edges") or []
        matching_target_ids = {
            node_id
            for node_id, node in nodes.items()
            if node_id != root_id and _label_matches_node(node, value)
        }
        if not matching_target_ids:
            return False

        original_edge_count = len(edges)
        kept_edges = [
            edge
            for edge in edges
            if not (
                str(edge.get("source") or "") == root_id
                and _canonical_relation(edge.get("relation")) == relation_name
                and str(edge.get("target") or "") in matching_target_ids
            )
        ]
        if len(kept_edges) == original_edge_count:
            return False

        graph["edges"] = kept_edges
        for target_id in list(matching_target_ids):
            if relation_name in _TEMP_WORKSPACE_RELATIONS and self._node_is_orphaned(graph, target_id):
                nodes.pop(target_id, None)
        root["updated_at"] = int(time.time())
        return True

    @staticmethod
    def _node_is_orphaned(graph: Dict[str, Any], node_id: str) -> bool:
        for edge in graph.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            if str(edge.get("source") or "") == node_id or str(edge.get("target") or "") == node_id:
                return False
        node = (graph.get("nodes") or {}).get(node_id) or {}
        if not isinstance(node, dict):
            return True
        attributes = node.get("attributes") or {}
        for name, entries in attributes.items():
            if _canonical_attribute_name(name) in _TEMP_WORKSPACE_ATTRIBUTE_NAMES:
                continue
            if _entry_values(entries):
                return False
        return True

    def update_knowledge_async(self, recent_history: List[Dict[str, str]]) -> None:
        if not recent_history:
            return
        digest = history_digest(recent_history)
        if not digest:
            return
        force_refresh = history_contains_explicit_profile_disclosure(recent_history)
        candidate_refresh = force_refresh or history_contains_world_model_candidate(recent_history)

        with self._lock:
            if digest == self._last_profile_digest:
                return
            if not candidate_refresh:
                self._last_profile_digest = digest
                self._profile_refresh_counter = 0
                self._log("[WorldModel] Refresh skipped; no durable world-model candidate in recent user turns.")
                return
            if force_refresh:
                self._profile_refresh_counter = 0
            else:
                self._profile_refresh_counter += 1
                if self._profile_refresh_counter < PROFILE_REFRESH_EVERY_CALLS:
                    self._log(
                        f"[WorldModel] Refresh deferred ({self._profile_refresh_counter}/{PROFILE_REFRESH_EVERY_CALLS})."
                    )
                    return
                self._profile_refresh_counter = 0
            self._last_profile_digest = digest

        self._log("[WorldModel] Refreshing world model...")
        thread = threading.Thread(target=self._do_update_world_model, args=(recent_history,), daemon=True)
        thread.start()

    def update_world_model_async(self, recent_history: List[Dict[str, str]]) -> None:
        self.update_knowledge_async(recent_history)

    def _do_update_world_model(self, history: List[Dict[str, str]]) -> None:
        try:
            current_graph = self.load_graph()
            user_history_text = history_user_text(history)
            prompt = build_world_model_extraction_prompt(current_graph, history)
            result = self.llm.generate([{"role": "user", "content": prompt}], temperature=0.1)
            parsed = self._parse_json_result(result)
            if not parsed:
                self._log("[WorldModel] Extractor returned no JSON.")
                return

            changed = self._apply_patch(current_graph, parsed, user_history_text=user_history_text)
            if not changed:
                self._log("[WorldModel] No graph changes detected.")
                return

            self._save_graph(current_graph)
            self._log("[WorldModel] world_model.json updated.")
        except Exception as exc:
            self._log(f"[WorldModel] Update failed: {exc}")

    def _apply_patch(self, graph: Dict[str, Any], payload: Dict[str, Any], *, user_history_text: str) -> bool:
        changed = False
        nodes_payload = payload.get("entities") or []
        relations_payload = payload.get("relationships") or []

        for entity in nodes_payload:
            if not isinstance(entity, dict):
                continue
            node = self._merge_entity(graph, entity, user_history_text=user_history_text)
            if node:
                changed = True

        for relation in relations_payload:
            if not isinstance(relation, dict):
                continue
            if self._merge_relationship(graph, relation, user_history_text=user_history_text):
                changed = True

        return changed

    def _target_payload_attributes(self, target_payload: Any) -> list[dict[str, Any]]:
        if not isinstance(target_payload, dict):
            return []
        attrs = target_payload.get("attributes") or []
        return [item for item in attrs if isinstance(item, dict)]

    def _entity_payload_is_temporary_workspace_artifact(
        self,
        entity_type: str,
        label: str,
        attributes: list[dict[str, Any]],
    ) -> bool:
        if str(entity_type or "").strip().lower() != "project":
            return False
        if _looks_like_workspace_artifact_value(label):
            return True
        for attribute in attributes:
            name = _canonical_attribute_name(attribute.get("name"))
            value = str(attribute.get("value") or "").strip()
            if name in _TEMP_WORKSPACE_ATTRIBUTE_NAMES:
                return True
            if _looks_like_workspace_artifact_value(value):
                return True
        return False

    def _node_is_temporary_workspace_artifact(self, node: Dict[str, Any]) -> bool:
        if not isinstance(node, dict):
            return False
        if str(node.get("type") or "").strip().lower() != "project":
            return False
        if _looks_like_workspace_artifact_value(str(node.get("label") or "")):
            return True
        attributes = node.get("attributes") or {}
        if not isinstance(attributes, dict):
            return False
        for name, entries in attributes.items():
            canonical = _canonical_attribute_name(name)
            if canonical in _TEMP_WORKSPACE_ATTRIBUTE_NAMES:
                return True
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                if _looks_like_workspace_artifact_value(str(entry.get("value") or "")):
                    return True
        return False

    def _resolve_relationship_target_identity(
        self,
        graph: Dict[str, Any],
        *,
        source_id: str,
        relation_name: str,
        target_payload: Any,
        user_history_text: str,
    ) -> tuple[str, str, str]:
        if isinstance(target_payload, dict):
            target_label = str(target_payload.get("label") or "").strip()
            target_type = str(target_payload.get("type") or "entity").strip().lower() or "entity"
            explicit_id = str(target_payload.get("id") or "").strip()
        else:
            target_label = str(target_payload or "").strip()
            target_type = "entity"
            explicit_id = ""

        if explicit_id:
            return target_label, target_type, explicit_id
        if not target_label:
            return target_label, target_type, _entity_id(target_type, target_label)

        nodes = graph.get("nodes") or {}
        matching_ids = [
            node_id
            for node_id, node in nodes.items()
            if _label_matches_node(node, target_label, target_type)
        ]

        edges = [edge for edge in (graph.get("edges") or []) if isinstance(edge, dict) and _active_entry(edge)]
        same_relation_target = next(
            (
                str(edge.get("target") or "")
                for edge in edges
                if str(edge.get("source") or "") == source_id
                and _canonical_relation(edge.get("relation")) == relation_name
                and str(edge.get("target") or "") in matching_ids
            ),
            "",
        )
        if same_relation_target:
            return target_label, target_type, same_relation_target

        if matching_ids and _history_indicates_distinct_same_name_entities(user_history_text, target_label):
            scoped_id = _relation_scoped_entity_id(target_type, target_label, relation_name)
            return target_label, target_type, scoped_id

        if len(matching_ids) == 1:
            return target_label, target_type, matching_ids[0]

        scoped_id = _relation_scoped_entity_id(target_type, target_label, relation_name)
        if scoped_id in nodes:
            return target_label, target_type, scoped_id
        return target_label, target_type, _entity_id(target_type, target_label)

    def _merge_entity(self, graph: Dict[str, Any], entity: Dict[str, Any], *, user_history_text: str) -> bool:
        entity_type = str(entity.get("type") or "entity").strip().lower() or "entity"
        label = str(entity.get("label") or "").strip()
        entity_id = str(entity.get("id") or "").strip()
        if entity_id == "person:user":
            label = label or str(self._ensure_root(graph).get("label") or "User")
        if not entity_id:
            if not label:
                return False
            entity_id = _entity_id(entity_type, label)

        nodes = graph.setdefault("nodes", {})
        current = nodes.get(entity_id)
        created = not isinstance(current, dict)
        if not isinstance(current, dict):
            current = {
                "id": entity_id,
                "type": entity_type,
                "label": label or entity_id.split(":", 1)[-1].replace("-", " ").title(),
                "aliases": [],
                "attributes": {},
                "updated_at": int(time.time()),
            }
            nodes[entity_id] = current

        changed = False
        aliases = _normalize_aliases([*current.get("aliases", []), *(entity.get("aliases") or [])])
        if aliases != current.get("aliases"):
            current["aliases"] = aliases
            changed = True

        if label and (profile_update_is_grounded("name", label, user_history_text) or entity_id == "person:user"):
            if current.get("label") != label:
                current["label"] = label
                changed = True
            if entity_id == "person:user":
                root_aliases = _normalize_aliases([*current.get("aliases", []), label, "user", "me"])
                if root_aliases != current.get("aliases"):
                    current["aliases"] = root_aliases
                    changed = True

        entity_attributes = [item for item in (entity.get("attributes") or []) if isinstance(item, dict)]
        workspace_artifact = self._entity_payload_is_temporary_workspace_artifact(entity_type, label, entity_attributes)

        for attribute in entity_attributes:
            if not isinstance(attribute, dict):
                continue
            name = _canonical_attribute_name(attribute.get("name"))
            value = str(attribute.get("value") or "").strip()
            if not name or not value:
                continue
            if not profile_fact_shape_is_allowed(name, value):
                continue
            if not profile_update_is_grounded(name, value, user_history_text):
                continue
            ttl_str = attribute.get("ttl")
            expires_at = resolve_fact_expiry(
                key=name,
                value=value,
                ttl_str=ttl_str,
                history_text=user_history_text,
            )
            if expires_at is None and workspace_artifact:
                expires_at = default_expiry_for_transient_fact(user_history_text)
            mode = str(attribute.get("mode") or ("set" if name in _SINGULAR_ATTRIBUTES else "add")).strip().lower()
            if self._apply_attribute_operation(current, name, value, mode=mode, expires_at=expires_at):
                changed = True

        if changed:
            current["updated_at"] = int(time.time())
        elif created:
            nodes.pop(entity_id, None)
        return changed

    def _merge_relationship(self, graph: Dict[str, Any], relation: Dict[str, Any], *, user_history_text: str) -> bool:
        source_id = str(relation.get("source") or "").strip() or "person:user"
        relation_name = _canonical_relation(relation.get("relation"))
        target_payload = relation.get("target")
        if not relation_name or not target_payload:
            return False

        target_label, target_type, target_id = self._resolve_relationship_target_identity(
            graph,
            source_id=source_id,
            relation_name=relation_name,
            target_payload=target_payload,
            user_history_text=user_history_text,
        )

        if not target_label and target_id != "person:user":
            return False
        if target_id != "person:user" and not profile_update_is_grounded("relationship", target_label, user_history_text):
            return False

        self._ensure_node(graph, source_id, "entity", source_id.split(":", 1)[-1].replace("-", " ").title())
        target_node = self._ensure_node(graph, target_id, target_type, target_label or "Entity")
        if target_label:
            target_node["label"] = target_label
            target_node["aliases"] = _normalize_aliases([*target_node.get("aliases", []), target_label])

        ttl_str = relation.get("ttl")
        expires_at = resolve_fact_expiry(
            key=f"relation:{relation_name}",
            value=target_label or target_id,
            ttl_str=ttl_str,
            history_text=user_history_text,
        )
        if (
            expires_at is None
            and relation_name in _TEMP_WORKSPACE_RELATIONS
            and self._node_is_temporary_workspace_artifact(target_node)
        ):
            expires_at = default_expiry_for_transient_fact(user_history_text)
        mode = str(relation.get("mode") or "add").strip().lower()

        edges = graph.setdefault("edges", [])
        before = len(edges)
        if mode == "set":
            edges[:] = [
                edge
                for edge in edges
                if not (
                    str(edge.get("source") or "") == source_id
                    and str(edge.get("relation") or "") == relation_name
                )
            ]
        elif mode == "remove":
            original = len(edges)
            edges[:] = [
                edge
                for edge in edges
                if not (
                    str(edge.get("source") or "") == source_id
                    and str(edge.get("relation") or "") == relation_name
                    and str(edge.get("target") or "") == target_id
                )
            ]
            return len(edges) != original

        signature = (source_id, relation_name, target_id)
        for edge in edges:
            if (
                str(edge.get("source") or "") == signature[0]
                and str(edge.get("relation") or "") == signature[1]
                and str(edge.get("target") or "") == signature[2]
            ):
                if edge.get("expires_at") != expires_at:
                    edge["expires_at"] = expires_at
                    edge["updated_at"] = int(time.time())
                    return True
                return len(edges) != before

        edges.append(
            {
                "source": source_id,
                "relation": relation_name,
                "target": target_id,
                "expires_at": expires_at,
                "updated_at": int(time.time()),
            }
        )
        return True

    def _scrub_graph_memory(self) -> None:
        graph = self.load_graph()
        nodes = graph.get("nodes") or {}
        changed = False

        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            attributes = node.get("attributes") or {}
            if not isinstance(attributes, dict):
                continue
            cleaned: Dict[str, list[Dict[str, Any]]] = {}
            for name, entries in attributes.items():
                if not isinstance(entries, list):
                    changed = True
                    continue
                kept: list[Dict[str, Any]] = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        changed = True
                        continue
                    value = str(entry.get("value") or "").strip()
                    if not profile_fact_shape_is_allowed(name, value):
                        changed = True
                        continue
                    kept.append(entry)
                if kept:
                    cleaned[name] = kept
                elif entries:
                    changed = True
            if cleaned != attributes:
                node["attributes"] = cleaned

        if changed:
            self._save_graph(graph)
            self._log("[WorldModel] Removed malformed or meta memory entries from world_model.json.")

    def _normalize_graph_memory(self) -> None:
        graph = self.load_graph()
        nodes = graph.get("nodes") or {}
        edges = graph.get("edges") or []
        changed = False
        default_expiry = default_expiry_for_transient_fact("")

        for node in nodes.values():
            if not isinstance(node, dict) or not self._node_is_temporary_workspace_artifact(node):
                continue
            attributes = node.get("attributes") or {}
            for entries in attributes.values():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("expires_at") is None:
                        entry["expires_at"] = default_expiry
                        entry["updated_at"] = int(time.time())
                        changed = True

        for edge in edges:
            if not isinstance(edge, dict):
                continue
            if _canonical_relation(edge.get("relation")) not in _TEMP_WORKSPACE_RELATIONS:
                continue
            target = nodes.get(str(edge.get("target") or "")) or {}
            if not self._node_is_temporary_workspace_artifact(target):
                continue
            if edge.get("expires_at") is None:
                edge["expires_at"] = default_expiry
                edge["updated_at"] = int(time.time())
                changed = True

        if changed:
            self._save_graph(graph)
            self._log("[WorldModel] Normalized temporary workspace-artifact memory with expiry.")

    def _apply_attribute_operation(
        self,
        node: Dict[str, Any],
        name: str,
        value: str,
        *,
        mode: str,
        expires_at: Optional[int],
    ) -> bool:
        attributes = node.setdefault("attributes", {})
        items = [entry for entry in attributes.get(name, []) if isinstance(entry, dict)]
        normalized_value = value.strip().lower()
        changed = False

        if mode == "remove":
            new_items = [entry for entry in items if str(entry.get("value") or "").strip().lower() != normalized_value]
            if len(new_items) != len(items):
                if new_items:
                    attributes[name] = new_items
                else:
                    attributes.pop(name, None)
                return True
            return False

        candidate = {"value": value, "expires_at": expires_at, "updated_at": int(time.time())}
        if mode == "set":
            same_value = (
                len(items) == 1
                and str(items[0].get("value") or "").strip() == value
                and items[0].get("expires_at") == expires_at
            )
            if same_value:
                return False
            attributes[name] = [candidate]
            return True

        for entry in items:
            if str(entry.get("value") or "").strip().lower() == normalized_value:
                if entry.get("expires_at") != expires_at:
                    entry["expires_at"] = expires_at
                    entry["updated_at"] = int(time.time())
                    changed = True
                return changed

        items.append(candidate)
        attributes[name] = items
        return True

    def _ensure_root(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        return self._ensure_node(graph, WorldModelStore.ROOT_ENTITY_ID, "person", "User")

    def _ensure_node(self, graph: Dict[str, Any], node_id: str, node_type: str, label: str) -> Dict[str, Any]:
        nodes = graph.setdefault("nodes", {})
        node = nodes.get(node_id)
        if not isinstance(node, dict):
            node = {
                "id": node_id,
                "type": node_type,
                "label": label,
                "aliases": [label] if label and label.lower() != "user" else ["user", "me"],
                "attributes": {},
                "updated_at": int(time.time()),
            }
            nodes[node_id] = node
        return node

    def _render_node_block(self, graph: Dict[str, Any], entity_id: str, *, include_relations: bool, query: str = "") -> List[str]:
        nodes = graph.get("nodes") or {}
        node = nodes.get(entity_id)
        if not isinstance(node, dict):
            return []

        label = str(node.get("label") or entity_id).strip()
        node_type = str(node.get("type") or "entity").strip()
        duplicate_labels = sum(
            1
            for other_id, other in nodes.items()
            if other_id != entity_id and isinstance(other, dict) and str(other.get("label") or "").strip().lower() == label.lower()
        )
        display_label = label
        if duplicate_labels:
            incoming_relations = sorted(
                {
                    _RELATION_DISPLAY.get(_canonical_relation(edge.get("relation")), _canonical_relation(edge.get("relation")).replace("_", " "))
                    for edge in (graph.get("edges") or [])
                    if isinstance(edge, dict)
                    and str(edge.get("target") or "") == entity_id
                    and _active_entry(edge)
                }
            )
            hint = ", ".join(incoming_relations) if incoming_relations else entity_id.split(":", 1)[-1]
            display_label = f"{label} [{hint}]"
        details: List[str] = []

        attributes = node.get("attributes") or {}
        for name in sorted(attributes.keys()):
            if not _attribute_should_render_in_prompt(name, attributes.get(name), query=query):
                continue
            values = _entry_values(attributes.get(name))
            if not values:
                continue
            if _canonical_attribute_name(name) == "name" and len(values) == 1 and values[0] == label:
                continue
            details.append(f"- {_display_attribute_name(name)}: {'; '.join(values)}")

        incoming_lines = []
        for edge in graph.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            if str(edge.get("target") or "") != entity_id or not _active_entry(edge):
                continue
            source = nodes.get(str(edge.get("source") or "")) or {}
            source_label = str(source.get("label") or edge.get("source") or "").strip()
            relation = _RELATION_DISPLAY.get(
                _canonical_relation(edge.get("relation")),
                _canonical_relation(edge.get("relation")).replace("_", " "),
            )
            if source_label:
                incoming_lines.append(f"- related to {source_label} as: {relation}")

        if include_relations:
            edge_lines = []
            for edge in graph.get("edges") or []:
                if not isinstance(edge, dict):
                    continue
                if str(edge.get("source") or "") != entity_id:
                    continue
                if not _active_entry(edge):
                    continue
                relation = _RELATION_DISPLAY.get(
                    _canonical_relation(edge.get("relation")),
                    _canonical_relation(edge.get("relation")).replace("_", " "),
                )
                target = nodes.get(str(edge.get("target") or "")) or {}
                target_label = str(target.get("label") or edge.get("target") or "").strip()
                if target_label:
                    edge_lines.append(f"- {relation}: {target_label}")
            details.extend(edge_lines[:8])
        elif duplicate_labels or not details:
            details.extend(incoming_lines[:4])
        if not details:
            return []
        return [f"Entity: {display_label} ({node_type})", *details]

    def _select_relevant_entities(self, graph: Dict[str, Any], query: str, *, max_entities: int) -> List[str]:
        nodes = graph.get("nodes") or {}
        root_id = str(graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        query_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", str(query or "").lower())
            if len(token) > 2
        }
        scores: List[tuple[int, str]] = []
        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            score = 0
            haystacks = [
                str(node.get("label") or "").lower(),
                " ".join(str(item).lower() for item in (node.get("aliases") or [])),
            ]
            attributes = node.get("attributes") or {}
            for entries in attributes.values():
                haystacks.extend(str(value).lower() for value in _entry_values(entries))
            blob = " ".join(haystacks)
            for token in query_tokens:
                if token in blob:
                    score += 2
            if node_id == root_id:
                score += 100
            elif any(
                str(edge.get("source") or "") == root_id and str(edge.get("target") or "") == node_id
                for edge in (graph.get("edges") or [])
                if isinstance(edge, dict) and _active_entry(edge)
            ):
                score += 5
            if score > 0 or not query_tokens:
                scores.append((score, node_id))

        scores.sort(key=lambda item: (-item[0], item[1]))
        selected = [node_id for _, node_id in scores[:max(max_entities, 1)]]
        if root_id not in selected:
            selected.insert(0, root_id)
        return selected[:max(max_entities, 1)]

    def _save_graph(self, graph: Dict[str, Any]) -> None:
        metadata = graph.setdefault("metadata", {})
        metadata["updated_at"] = int(time.time())
        self.store.save_graph(graph)
        self._sync_legacy_knowledge_mirror(graph)
        callback = self._graph_saved_callback
        if callback is not None:
            try:
                callback(json.loads(json.dumps(graph)))
            except Exception as exc:
                self._log(f"[WorldModel] Graph save callback failed: {exc}")

    def _flatten_legacy_knowledge(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        nodes = graph.get("nodes") or {}
        root_id = str(graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        root = nodes.get(root_id) or {}
        if not isinstance(root, dict):
            return {}

        facts: Dict[str, Any] = {}
        attributes = root.get("attributes") or {}
        for name, entries in attributes.items():
            values = _entry_values(entries)
            if not values:
                continue
            label = _display_attribute_name(name)
            expires_at = None
            active_entries = _active_values(entries)
            if active_entries:
                expires = [entry.get("expires_at") for entry in active_entries if entry.get("expires_at") is not None]
                expires_at = min(expires) if expires else None
            facts[label] = {"value": "; ".join(values), "expires_at": expires_at}

        family_lines: List[str] = []
        relation_edges = [
            edge
            for edge in (graph.get("edges") or [])
            if isinstance(edge, dict)
            and str(edge.get("source") or "") == root_id
            and _active_entry(edge)
        ]
        label_counts: Dict[str, int] = {}
        for edge in relation_edges:
            target = nodes.get(str(edge.get("target") or "")) or {}
            target_label = str(target.get("label") or edge.get("target") or "").strip()
            if not target_label:
                continue
            label_counts[target_label] = label_counts.get(target_label, 0) + 1
        for edge in relation_edges:
            target = nodes.get(str(edge.get("target") or "")) or {}
            target_label = str(target.get("label") or edge.get("target") or "").strip()
            if not target_label:
                continue
            relation = _canonical_relation(edge.get("relation"))
            if relation in {"child", "partner", "friend"}:
                family_lines.append(_relation_description(relation, target_label))
            relation_hint = _RELATION_DISPLAY.get(relation, relation.replace("_", " "))
            if label_counts.get(target_label, 0) > 1:
                key = f"{target_label} ({relation_hint})"
            else:
                key = target_label
            description = _relation_description(relation, target_label)
            if key in facts and facts[key].get("value") != description:
                key = f"{target_label} ({relation_hint})"
                suffix = 2
                while key in facts and facts[key].get("value") != description:
                    key = f"{target_label} ({relation_hint} {suffix})"
                    suffix += 1
            facts[key] = {"value": description, "expires_at": edge.get("expires_at")}

        if family_lines:
            facts["Family/Relationships"] = {"value": ", ".join(family_lines), "expires_at": None}
        return facts

    def _sync_legacy_knowledge_mirror(self, graph: Optional[Dict[str, Any]] = None) -> None:
        if self.knowledge_store is None:
            return
        payload = self._flatten_legacy_knowledge(graph or self.load_graph())
        try:
            self.knowledge_store.save(payload)
        except Exception as exc:
            self._log(f"[WorldModel] knowledge.json mirror sync failed: {exc}")

    def _migrate_legacy_knowledge_if_needed(self) -> None:
        if self.knowledge_store is None:
            return

        graph = self.load_graph()
        metadata = graph.setdefault("metadata", {})
        nodes = graph.get("nodes") or {}
        root_id = str(graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        only_root = set(nodes.keys()) == {root_id} and not (graph.get("edges") or [])
        if not only_root or metadata.get("migrated_from_legacy_knowledge"):
            return

        legacy = self.knowledge_store.load_active()
        if not legacy:
            return

        root = self._ensure_root(graph)
        changed = False
        for key, entry in legacy.items():
            if not isinstance(entry, dict):
                continue
            value = str(entry.get("value") or "").strip()
            if not value:
                continue
            expires_at = entry.get("expires_at")
            if self._apply_legacy_fact(graph, root, key, value, expires_at=expires_at):
                changed = True

        if changed:
            metadata["migrated_from_legacy_knowledge"] = True
            self._save_graph(graph)
            self._log("[WorldModel] Migrated legacy knowledge.json into world_model.json.")

    def _apply_legacy_fact(
        self,
        graph: Dict[str, Any],
        root: Dict[str, Any],
        key: str,
        value: str,
        *,
        expires_at: Optional[int],
    ) -> bool:
        raw_key = str(key or "").strip()
        canonical = _canonical_attribute_name(raw_key)
        changed = False

        if canonical in {
            "name",
            "location",
            "occupation",
            "likes_interests",
            "future_plans",
            "birth_date",
            "vehicle",
            "gender",
        }:
            if not profile_fact_shape_is_allowed(canonical, value):
                return False
            mode = "set" if canonical in _SINGULAR_ATTRIBUTES else "add"
            if self._apply_attribute_operation(root, canonical, value, mode=mode, expires_at=expires_at):
                changed = True
            if canonical == "name":
                root["label"] = value
                root["aliases"] = _normalize_aliases([*root.get("aliases", []), value, "user", "me"])
                changed = True
            return changed

        if canonical == "family_relationships":
            for chunk in re.split(r"\s*,\s*", value):
                match = re.search(
                    r"\b(daughter|son|girlfriend|boyfriend|wife|husband|partner|friend)\b(?: named)?\s+([A-Za-z][A-Za-z .'-]+)",
                    chunk,
                    re.IGNORECASE,
                )
                if not match:
                    continue
                relation = _infer_relation(match.group(1)) or "related_to"
                label = match.group(2).strip()
                target_type = "person"
                target_id = _entity_id(target_type, label)
                self._ensure_node(graph, target_id, target_type, label)
                if self._merge_relationship(
                    graph,
                    {
                        "source": WorldModelStore.ROOT_ENTITY_ID,
                        "relation": relation,
                        "target": {"id": target_id, "type": target_type, "label": label},
                        "mode": "add",
                    },
                    user_history_text=label.lower(),
                ):
                    changed = True
            if not changed:
                changed = self._apply_attribute_operation(root, canonical, value, mode="add", expires_at=expires_at)
            return changed

        if raw_key[:1].isupper():
            entity_type = _node_type_from_key(raw_key)
            target_id = _entity_id(entity_type, raw_key)
            target = self._ensure_node(graph, target_id, entity_type, raw_key)
            relation = _infer_relation(value)
            if relation:
                if self._merge_relationship(
                    graph,
                    {
                        "source": WorldModelStore.ROOT_ENTITY_ID,
                        "relation": relation,
                        "target": {"id": target_id, "type": entity_type, "label": raw_key},
                        "mode": "add",
                    },
                    user_history_text=raw_key.lower(),
                ):
                    changed = True
            if self._apply_attribute_operation(target, "description", value, mode="set", expires_at=expires_at):
                changed = True
            return changed

        return self._apply_attribute_operation(root, canonical, value, mode="add", expires_at=expires_at)

    def _parse_json_result(self, text: str) -> Optional[Dict[str, Any]]:
        try:
            payload = str(text or "").strip()
            if "```" in payload:
                start = payload.find("```")
                end = payload.rfind("```")
                if start != -1 and end != -1 and start < end:
                    payload = payload[start + 3 : end]
                    if payload.startswith("json"):
                        payload = payload[4:]
            payload = payload.strip()
            start = payload.find("{")
            end = payload.rfind("}")
            if start == -1 or end == -1 or start >= end:
                return None
            return json.loads(payload[start : end + 1])
        except json.JSONDecodeError:
            return None

    def consolidate_memory_async(self, history: List[Dict[str, str]]) -> None:
        if not history:
            return
        thread = threading.Thread(target=self._do_consolidate_memory, args=(history,), daemon=True)
        thread.start()

    def _do_consolidate_memory(self, history: List[Dict[str, str]]) -> None:
        try:
            self._log("[Memory] Analyzing recent turn for facts...")

            text_history = ""
            for item in history:
                role = item.get("role", "user").capitalize()
                content = item.get("content", "")
                text_history += f"{role}: {content}\n"

            prompt = build_memory_archivist_prompt(text_history)
            result = self.llm.generate([{"role": "user", "content": prompt}], temperature=0.1)

            facts: list[str] = []
            try:
                clean_result = result.strip()
                if "```json" in clean_result:
                    clean_result = clean_result.split("```json")[1].split("```")[0]
                elif "```" in clean_result:
                    clean_result = clean_result.split("```")[1].split("```")[0]

                start = clean_result.find("[")
                end = clean_result.rfind("]")
                if start != -1 and end != -1:
                    facts = json.loads(clean_result[start : end + 1])
            except json.JSONDecodeError:
                facts = []

            if not facts:
                self._log("[Memory] No new facts extracted.")
                return

            from .brain import get_brain

            brain = self.memory_brain or get_brain(self.data_dir)
            date_str = time.strftime("%b %d, %Y")
            for fact in facts:
                if not isinstance(fact, str) or not fact:
                    continue
                clean_fact = " ".join(fact.strip().split())
                if not fact_is_grounded(clean_fact, history):
                    self._log(f"[Memory] Ignored ungrounded fact: {clean_fact}")
                    continue
                brain.remember(
                    text=clean_fact,
                    metadata={"type": "semantic_fact", "date": date_str},
                )
                self._log(f"[Memory] Stored: {clean_fact}")
        except Exception as exc:
            self._log(f"[Memory] Error: {exc}")


@register_hook("on_turn_end")
def _hook_consolidate_recent_memory(orc, *, reporter_just_ran: bool = False) -> None:
    del reporter_just_ran
    if bool(getattr(orc, "synthetic_user_turn", False)):
        return
    recent_messages = orc.chat.recent_messages(3)
    if orc.knowledge_enabled and len(recent_messages) >= 3:
        orc.knowledge.consolidate_memory_async(recent_messages)


@register_hook("on_turn_end")
def _hook_refresh_profile_knowledge(orc, *, reporter_just_ran: bool = False) -> None:
    del reporter_just_ran
    if bool(getattr(orc, "synthetic_user_turn", False)):
        return
    profile_messages = orc.chat.recent_messages(8)
    if orc.knowledge_enabled and len(profile_messages) >= 4:
        orc.knowledge.update_knowledge_async(profile_messages)
