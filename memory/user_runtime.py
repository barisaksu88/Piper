from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import data_state_path
from memory.brain import get_brain
from memory.documents import DocumentMemoryManager
from memory.state_owner import SharedStateOwner
from memory.stores import JsonDictStore, WorldModelStore
from memory.transient_state import TransientStateManager
from memory.world_model import WorldModelManager


DEFAULT_ADMIN_USER_ID = "admin_baris"
DEFAULT_ADMIN_NAME = "Baris"
DEFAULT_GUEST_USER_ID = "unknown"
DEFAULT_GUEST_NAME = "Unknown"
DEFAULT_STANDARD_ROLE = "standard"
DEFAULT_ADMIN_ROLE = "admin"
DEFAULT_PASSWORD_ITERATIONS = 240000
_IDENTITY_STOPWORDS = {
    "a",
    "an",
    "am",
    "and",
    "are",
    "back",
    "busy",
    "feeling",
    "fine",
    "here",
    "hungry",
    "i",
    "im",
    "i'm",
    "just",
    "me",
    "my",
    "sleepy",
    "stressed",
    "testing",
    "tired",
    "trying",
    "using",
    "we",
    "we're",
    "working",
}
_IDENTITY_LEADING_STOPWORDS = {
    "his",
    "her",
    "their",
    "our",
    "your",
    "the",
    "this",
    "that",
    "someone",
    "somebody",
}
_IDENTITY_RELATION_TOKENS = {
    "friend",
    "girlfriend",
    "boyfriend",
    "wife",
    "husband",
    "partner",
    "daughter",
    "son",
    "kid",
    "child",
    "mother",
    "mom",
    "father",
    "dad",
    "parent",
    "brother",
    "sister",
    "cousin",
    "uncle",
    "aunt",
    "roommate",
    "coworker",
    "colleague",
    "boss",
    "fiance",
    "fiancee",
    "neighbor",
    "neighbour",
}
_SELF_IDENTIFY_PATTERNS = (
    re.compile(r"(?is)^\s*my name is\s+(?P<name>[a-z][a-z .'-]{0,40}?)(?:\s*,.*)?\s*[.?!]*$"),
    re.compile(r"(?is)^\s*this is\s+(?P<name>[a-z][a-z .'-]{0,40}?)(?:\s*,.*)?\s*[.?!]*$"),
    re.compile(r"(?is)^\s*(?:i am|i'm|im)\s+(?P<name>[a-z][a-z .'-]{0,40}?)(?:\s*,.*)?\s*[.?!]*$"),
    re.compile(
        r"(?is)^\s*(?:(?:no|nah|nope|wait|sorry)\s+)?(?:i\s+mean\s+)?"
        r"(?:it'?s|its)\s+me\s+(?P<name>[a-z][a-z .'-]{0,40}?)(?:\s*,.*)?\s*[.?!]*$"
    ),
)
_RELATION_TO_ADMIN_PATTERNS = (
    (re.compile(r"(?i)\b(?:baris'?s friend|friend of baris)\b"), "friend"),
    (
        re.compile(
            r"(?i)\b(?:baris'?s\s+(?:girlfriend|boyfriend|wife|husband|partner)|"
            r"(?:girlfriend|boyfriend|wife|husband|partner)\s+of\s+baris)\b"
        ),
        "partner",
    ),
    (re.compile(r"(?i)\b(?:daughter|son|kid|child)\s+of\s+baris\b|\bbaris'?s\s+(?:daughter|son|kid|child)\b"), "child"),
    (re.compile(r"(?i)\b(?:mother|mom|father|dad|parent)\s+of\s+baris\b|\bbaris'?s\s+(?:mother|mom|father|dad|parent)\b"), "parent"),
)
_GENERIC_RELATION_TO_ADMIN_PATTERNS = (
    (re.compile(r"(?i)\b(?:i am|i'm|im)\s+his\s+friend\b"), "friend"),
    (
        re.compile(
            r"(?i)\b(?:i am|i'm|im)\s+his\s+(?:girlfriend|boyfriend|wife|husband|partner)\b"
        ),
        "partner",
    ),
    (re.compile(r"(?i)\b(?:i am|i'm|im)\s+his\s+(?:daughter|son|kid|child)\b"), "child"),
    (re.compile(r"(?i)\b(?:i am|i'm|im)\s+his\s+(?:mother|mom|father|dad|parent)\b"), "parent"),
)
_RELATION_DISAMBIGUATION_LABELS = {
    "friend": "Baris's friend",
    "partner": "Baris's partner",
    "child": "Baris's child",
    "parent": "Baris's parent",
}


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "user"


def _normalize_token(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _active_edge(entry: dict[str, Any]) -> bool:
    try:
        expires_at = entry.get("expires_at")
    except Exception:
        expires_at = None
    if expires_at is None:
        return True
    try:
        return int(expires_at) > int(time.time())
    except Exception:
        return True


def _relation_scoped_user_id(name: str, relation: str) -> str:
    return _slugify(f"{name}_{relation}")


def _relation_disambiguation_label(relation: str) -> str:
    key = str(relation or "").strip().lower()
    if not key:
        return ""
    return _RELATION_DISAMBIGUATION_LABELS.get(key, f"Baris's {key.replace('_', ' ')}")


def _join_with_or(parts: list[str]) -> str:
    values = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} or {values[1]}"
    return f"{', '.join(values[:-1])}, or {values[-1]}"


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    name: str
    role: str = DEFAULT_STANDARD_ROLE
    memory_silo: str = ""
    voice_embedding_path: str = ""
    style_filename: str = ""

    @property
    def is_admin(self) -> bool:
        return str(self.role or "").strip().lower() == DEFAULT_ADMIN_ROLE

    @property
    def is_unknown(self) -> bool:
        return _slugify(self.user_id) == DEFAULT_GUEST_USER_ID

    def resolved_data_dir(self, root_data_dir: Path) -> Path:
        silo = str(self.memory_silo or "").strip()
        if not silo or silo == ".":
            return Path(root_data_dir)
        return Path(root_data_dir) / silo


@dataclass(frozen=True)
class IdentityCandidate:
    user_id: str
    name: str
    relations: tuple[str, ...] = ()
    profile: UserProfile | None = None

    @property
    def primary_relation(self) -> str:
        return self.relations[0] if self.relations else ""


@dataclass(frozen=True)
class UserSwitchResult:
    profile: UserProfile
    created: bool = False


@dataclass(frozen=True)
class UserActivationResult:
    status: str
    profile: UserProfile
    created: bool = False
    message: str = ""

    @property
    def switched(self) -> bool:
        return self.status == "switched"

    @property
    def requires_password(self) -> bool:
        return self.status == "password_required"

    @property
    def requires_identity_clarification(self) -> bool:
        return self.status == "identity_clarification_required"


@dataclass(frozen=True)
class AdminPasswordResult:
    success: bool
    message: str


def _pbkdf2_digest(password: str, *, salt_b64: str, iterations: int) -> str:
    try:
        salt = base64.b64decode(str(salt_b64 or "").encode("ascii"))
    except Exception:
        salt = b""
    if not salt:
        return ""
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            salt,
            max(int(iterations), 1),
        )
    except Exception:
        return ""
    return base64.b64encode(digest).decode("ascii")


def _new_password_record(password: str, *, iterations: int = DEFAULT_PASSWORD_ITERATIONS) -> dict[str, Any]:
    salt = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    return {
        "admin_password_salt": salt,
        "admin_password_hash": _pbkdf2_digest(password, salt_b64=salt, iterations=iterations),
        "admin_password_iterations": max(int(iterations), 1),
    }


def _clean_identity_name(text: str) -> str:
    candidate = " ".join(str(text or "").strip().split())
    if not candidate:
        return ""
    # Strip trailing junk words that often follow a name in casual speech.
    candidate = re.sub(
        r"(?i)\b(?:"
        r"speaking|here|again|today|fine|sorry|good|okay|ok|great|"
        r"tired|busy|done|ready|back|confused|lost|sure|"
        r"cold|hot|nice|bad|sad|mad|later|maybe|tomorrow|tonight|yesterday|"
        r"now|then|there|home|away|out|up|down|early|soon"
        r")\b",
        "",
        candidate,
    )
    candidate = " ".join(candidate.split()).strip(" ,.!?")
    if not candidate:
        return ""
    tokens = re.findall(r"[a-z]+", candidate.lower())
    if not tokens or len(tokens) > 3:
        return ""
    if tokens[0] in _IDENTITY_LEADING_STOPWORDS:
        return ""
    if any(token in _IDENTITY_STOPWORDS for token in tokens):
        return ""
    if any(token in _IDENTITY_RELATION_TOKENS for token in tokens):
        return ""
    if any(token in {"working", "watching", "playing", "debugging", "thinking"} for token in tokens):
        return ""
    return candidate


def _extract_self_identified_name(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    # First pass: match at start of string (clean inputs)
    for pattern in _SELF_IDENTIFY_PATTERNS:
        match = pattern.match(raw)
        if not match:
            continue
        candidate = _clean_identity_name(match.group("name") or "")
        if candidate:
            return candidate
    # Second pass: search anywhere in text for natural name introductions.
    # _clean_identity_name filters out common non-name words to keep false
    # positives low on risky patterns like "it's <name>" and "call me <name>".
    _NAME_STOP_BOUNDARY = r"(?:\s*[,.!?]|\s+and\s|\s+from\s|\s+not\s|\s+but\s|\s+or\s|$)"
    _SEARCH_FALLBACK_PATTERNS = (
        re.compile(rf"(?i)\bmy name(?:'?s| is)\s+(?P<name>[a-z][a-z .'-]{{0,40}}?){_NAME_STOP_BOUNDARY}"),
        re.compile(rf"(?i)\bthis is\s+(?P<name>[a-z][a-z .'-]{{0,40}}?){_NAME_STOP_BOUNDARY}"),
        re.compile(rf"(?i)\b(?:it'?s me|it is me)\s+(?P<name>[a-z][a-z .'-]{{0,40}}?){_NAME_STOP_BOUNDARY}"),
        re.compile(rf"(?i)\b(?:i'm|i am|im)\s+(?P<name>[a-z][a-z .'-]{{0,40}}?){_NAME_STOP_BOUNDARY}"),
        # Natural variants that need the non-name filter in _clean_identity_name
        re.compile(rf"(?i)\b(?:it'?s|its)\s+(?P<name>[a-z][a-z .'-]{{0,40}}?){_NAME_STOP_BOUNDARY}"),
        re.compile(rf"(?i)\bcall me\s+(?P<name>[a-z][a-z .'-]{{0,40}}?){_NAME_STOP_BOUNDARY}"),
        re.compile(rf"(?i)\b(?:you can call me|people call me|everyone calls me|my friends call me)\s+(?P<name>[a-z][a-z .'-]{{0,40}}?){_NAME_STOP_BOUNDARY}"),
        re.compile(rf"(?i)\b(?:i go by|goes by)\s+(?P<name>[a-z][a-z .'-]{{0,40}}?){_NAME_STOP_BOUNDARY}"),
        re.compile(rf"(?i)\bname is\s+(?P<name>[a-z][a-z .'-]{{0,40}}?){_NAME_STOP_BOUNDARY}"),
    )
    for pattern in _SEARCH_FALLBACK_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        candidate = _clean_identity_name(match.group("name") or "")
        if candidate:
            return candidate
    return ""


def _extract_relation_to_admin(text: str) -> str:
    blob = str(text or "").strip()
    if not blob:
        return ""
    for pattern, relation in _RELATION_TO_ADMIN_PATTERNS:
        if pattern.search(blob):
            return relation
    if re.search(r"(?i)\bbaris\b", blob):
        for pattern, relation in _GENERIC_RELATION_TO_ADMIN_PATTERNS:
            if pattern.search(blob):
                return relation
    return ""


class UserRegistry:
    SCHEMA_VERSION = 3

    def __init__(
        self,
        data_dir: Path,
        *,
        admin_user_id: str = DEFAULT_ADMIN_USER_ID,
        admin_name: str = DEFAULT_ADMIN_NAME,
        default_style_filename: str = "default.style",
    ) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "users.json"
        self.store = JsonDictStore(self.path)
        self.admin_user_id = _slugify(admin_user_id)
        self.admin_name = str(admin_name or DEFAULT_ADMIN_NAME).strip() or DEFAULT_ADMIN_NAME
        self.default_style_filename = str(default_style_filename or "default.style").strip() or "default.style"
        self._lock = threading.RLock()
        self._ensure_payload()

    def _guess_legacy_admin_name(self) -> str:
        world_model_path = data_state_path(self.data_dir, "world_model.json")
        try:
            payload = json.loads(world_model_path.read_text(encoding="utf-8"))
        except Exception:
            return self.admin_name
        root_id = str(payload.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        nodes = payload.get("nodes") or {}
        root = nodes.get(root_id) or {}
        label = str(root.get("label") or "").strip()
        if not label or label.lower() == "user":
            return self.admin_name
        return label

    def _default_admin_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.admin_user_id,
            "name": self._guess_legacy_admin_name(),
            "role": DEFAULT_ADMIN_ROLE,
            # Keep the admin user on the legacy root data dir for now so the
            # existing Baris state remains intact while we introduce per-user silos.
            "memory_silo": ".",
            "voice_embedding_path": "",
            "style_filename": self.default_style_filename,
        }

    def _default_guest_payload(self) -> dict[str, Any]:
        return {
            "user_id": DEFAULT_GUEST_USER_ID,
            "name": DEFAULT_GUEST_NAME,
            "role": DEFAULT_STANDARD_ROLE,
            "memory_silo": f"runtime/{DEFAULT_GUEST_USER_ID}",
            "voice_embedding_path": "",
            "style_filename": self.default_style_filename,
        }

    def _default_auth_payload(self) -> dict[str, Any]:
        return {
            "admin_password_hash": "",
            "admin_password_salt": "",
            "admin_password_iterations": DEFAULT_PASSWORD_ITERATIONS,
            "last_public_user_id": DEFAULT_GUEST_USER_ID,
        }

    def _default_payload(self) -> dict[str, Any]:
        admin = self._default_admin_payload()
        return {
            "schema_version": self.SCHEMA_VERSION,
            "active_user_id": DEFAULT_GUEST_USER_ID,
            "auth": self._default_auth_payload(),
            "users": {
                self.admin_user_id: admin,
            },
        }

    def _coerce_profile(self, raw: dict[str, Any], *, fallback_id: str = "") -> UserProfile:
        user_id = _slugify(str(raw.get("user_id") or fallback_id or "user"))
        name = " ".join(str(raw.get("name") or user_id).strip().split()) or user_id
        role = str(raw.get("role") or DEFAULT_STANDARD_ROLE).strip().lower() or DEFAULT_STANDARD_ROLE
        if role not in {DEFAULT_ADMIN_ROLE, DEFAULT_STANDARD_ROLE}:
            role = DEFAULT_STANDARD_ROLE
        memory_silo = str(raw.get("memory_silo") or "").strip()
        voice_embedding_path = str(raw.get("voice_embedding_path") or "").strip()
        style_filename = str(raw.get("style_filename") or "").strip()
        return UserProfile(
            user_id=user_id,
            name=name,
            role=role,
            memory_silo=memory_silo,
            voice_embedding_path=voice_embedding_path,
            style_filename=style_filename,
        )

    def _coerce_auth_payload(self, raw: Any, *, users: dict[str, dict[str, Any]]) -> dict[str, Any]:
        payload = self._default_auth_payload()
        if isinstance(raw, dict):
            payload["admin_password_hash"] = str(raw.get("admin_password_hash") or "").strip()
            payload["admin_password_salt"] = str(raw.get("admin_password_salt") or "").strip()
            try:
                payload["admin_password_iterations"] = max(
                    int(raw.get("admin_password_iterations") or DEFAULT_PASSWORD_ITERATIONS),
                    1,
                )
            except Exception:
                payload["admin_password_iterations"] = DEFAULT_PASSWORD_ITERATIONS
            payload["last_public_user_id"] = _slugify(str(raw.get("last_public_user_id") or DEFAULT_GUEST_USER_ID))
        last_public_user_id = str(payload.get("last_public_user_id") or DEFAULT_GUEST_USER_ID).strip() or DEFAULT_GUEST_USER_ID
        if last_public_user_id != DEFAULT_GUEST_USER_ID and last_public_user_id not in users:
            last_public_user_id = DEFAULT_GUEST_USER_ID
        if last_public_user_id != DEFAULT_GUEST_USER_ID:
            candidate = users.get(last_public_user_id) or {}
            profile = self._coerce_profile(candidate, fallback_id=last_public_user_id)
            if profile.is_admin:
                last_public_user_id = DEFAULT_GUEST_USER_ID
        else:
            last_public_user_id = DEFAULT_GUEST_USER_ID
        payload["last_public_user_id"] = last_public_user_id
        return payload

    def load_payload(self) -> dict[str, Any]:
        data = self.store.load()
        if not data:
            return self._default_payload()

        payload = self._default_payload()
        payload["schema_version"] = int(data.get("schema_version") or self.SCHEMA_VERSION)
        payload["active_user_id"] = str(data.get("active_user_id") or DEFAULT_GUEST_USER_ID).strip() or DEFAULT_GUEST_USER_ID

        users_payload = data.get("users")
        users: dict[str, dict[str, Any]] = {}
        if isinstance(users_payload, dict):
            for raw_key, raw_profile in users_payload.items():
                if not isinstance(raw_profile, dict):
                    continue
                profile = self._coerce_profile(raw_profile, fallback_id=str(raw_key))
                if profile.is_unknown:
                    continue
                users[profile.user_id] = {
                    "user_id": profile.user_id,
                    "name": profile.name,
                    "role": profile.role,
                    "memory_silo": profile.memory_silo,
                    "voice_embedding_path": profile.voice_embedding_path,
                    "style_filename": profile.style_filename,
                }
        users.pop("guest", None)
        if not users:
            users[self.admin_user_id] = self._default_admin_payload()
        payload["users"] = users
        payload["auth"] = self._coerce_auth_payload(data.get("auth"), users=users)
        return payload

    def save_payload(self, payload: dict[str, Any]) -> None:
        data = self._default_payload()
        if isinstance(payload, dict):
            data.update(payload)
        users_payload = data.get("users")
        if not isinstance(users_payload, dict):
            users_payload = {}
        normalized_users: dict[str, dict[str, Any]] = {}
        for raw_key, raw_profile in users_payload.items():
            if not isinstance(raw_profile, dict):
                continue
            profile = self._coerce_profile(raw_profile, fallback_id=str(raw_key))
            if profile.is_unknown:
                continue
            normalized_id = profile.user_id
            normalized_users[normalized_id] = {
                "user_id": normalized_id,
                "name": profile.name,
                "role": profile.role,
                "memory_silo": profile.memory_silo,
                "voice_embedding_path": profile.voice_embedding_path,
                "style_filename": profile.style_filename,
            }
        if self.admin_user_id not in normalized_users:
            normalized_users[self.admin_user_id] = self._default_admin_payload()
        data["users"] = normalized_users
        data["auth"] = self._coerce_auth_payload(data.get("auth"), users=normalized_users)
        active_user_id = _slugify(str(data.get("active_user_id") or DEFAULT_GUEST_USER_ID).strip() or DEFAULT_GUEST_USER_ID)
        if active_user_id != DEFAULT_GUEST_USER_ID and active_user_id not in normalized_users:
            active_user_id = DEFAULT_GUEST_USER_ID
        data["active_user_id"] = active_user_id
        data["schema_version"] = self.SCHEMA_VERSION
        self.store.save(data)

    def _ensure_payload(self) -> None:
        with self._lock:
            stored = self.store.load()
            payload = self.load_payload()
            users = dict(payload.get("users") or {})
            changed = False
            stored_users = stored.get("users") if isinstance(stored, dict) else {}
            if isinstance(stored_users, dict) and (
                "guest" in stored_users or DEFAULT_GUEST_USER_ID in stored_users
            ):
                changed = True
            try:
                stored_schema_version = int((stored or {}).get("schema_version") or 0)
            except Exception:
                stored_schema_version = 0
            if stored_schema_version < self.SCHEMA_VERSION:
                changed = True
            if "guest" in users:
                users.pop("guest", None)
                changed = True
            if DEFAULT_GUEST_USER_ID in users:
                users.pop(DEFAULT_GUEST_USER_ID, None)
                changed = True
            if self.admin_user_id not in users:
                users[self.admin_user_id] = self._default_admin_payload()
                changed = True
            admin = self._coerce_profile(users[self.admin_user_id], fallback_id=self.admin_user_id)
            if not admin.is_admin:
                users[self.admin_user_id] = self._default_admin_payload()
                changed = True
            active_user_id = _slugify(str(payload.get("active_user_id") or DEFAULT_GUEST_USER_ID).strip() or DEFAULT_GUEST_USER_ID)
            if active_user_id != DEFAULT_GUEST_USER_ID and active_user_id not in users:
                payload["active_user_id"] = DEFAULT_GUEST_USER_ID
                changed = True
            payload["users"] = users
            payload["auth"] = self._coerce_auth_payload(payload.get("auth"), users=users)
            if changed or not self.path.exists():
                self.save_payload(payload)

    def list_profiles(self) -> list[UserProfile]:
        with self._lock:
            payload = self.load_payload()
            users = payload.get("users") or {}
            profiles = [
                self._coerce_profile(raw_profile, fallback_id=str(user_id))
                for user_id, raw_profile in users.items()
                if isinstance(raw_profile, dict)
            ]
        profiles.sort(
            key=lambda item: (
                0 if item.is_admin else 1 if item.is_unknown else 2,
                item.name.lower(),
                item.user_id,
            )
        )
        return profiles

    def matching_profiles(self, token: str) -> list[UserProfile]:
        normalized = _normalize_token(token)
        if not normalized:
            return []
        if normalized in {
            _normalize_token(DEFAULT_GUEST_USER_ID),
            _normalize_token(DEFAULT_GUEST_NAME),
        }:
            return [self._coerce_profile(self._default_guest_payload(), fallback_id=DEFAULT_GUEST_USER_ID)]
        exact_id_matches: list[UserProfile] = []
        name_matches: list[UserProfile] = []
        for profile in self.list_profiles():
            if _normalize_token(profile.user_id) == normalized:
                exact_id_matches.append(profile)
                continue
            if _normalize_token(profile.name) == normalized:
                name_matches.append(profile)
                continue
            if _normalize_token(profile.name.replace(" ", "_")) == normalized:
                name_matches.append(profile)
        return exact_id_matches or name_matches

    def active_profile(self) -> UserProfile:
        with self._lock:
            payload = self.load_payload()
            users = payload.get("users") or {}
            active_user_id = _slugify(str(payload.get("active_user_id") or DEFAULT_GUEST_USER_ID).strip() or DEFAULT_GUEST_USER_ID)
            if active_user_id == DEFAULT_GUEST_USER_ID:
                return self._coerce_profile(self._default_guest_payload(), fallback_id=DEFAULT_GUEST_USER_ID)
            raw = users.get(active_user_id) or users.get(self.admin_user_id) or self._default_admin_payload()
            return self._coerce_profile(raw, fallback_id=active_user_id)

    def profile_for_id(self, user_id: str) -> UserProfile | None:
        target = _slugify(user_id)
        if target == DEFAULT_GUEST_USER_ID:
            return self._coerce_profile(self._default_guest_payload(), fallback_id=DEFAULT_GUEST_USER_ID)
        with self._lock:
            payload = self.load_payload()
            raw = (payload.get("users") or {}).get(target)
        if not isinstance(raw, dict):
            return None
        return self._coerce_profile(raw, fallback_id=target)

    def resolve_profile(self, token: str) -> UserProfile | None:
        matches = self.matching_profiles(token)
        return matches[0] if matches else None

    def _allocate_user_id(self, name: str) -> str:
        base = _slugify(name)
        return self._allocate_specific_user_id(base)

    def _allocate_specific_user_id(self, base_token: str) -> str:
        base = _slugify(base_token)
        candidate = base
        index = 2
        existing_ids = {profile.user_id for profile in self.list_profiles()}
        existing_ids.add(DEFAULT_GUEST_USER_ID)
        while candidate in existing_ids:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    def _new_standard_profile(self, name: str, *, preferred_user_id: str = "") -> UserProfile:
        clean_name = " ".join(str(name or "").strip().split()) or "User"
        user_id = self._allocate_specific_user_id(preferred_user_id) if preferred_user_id else self._allocate_user_id(clean_name)
        return UserProfile(
            user_id=user_id,
            name=clean_name,
            role=DEFAULT_STANDARD_ROLE,
            memory_silo=f"users/{user_id}",
            voice_embedding_path="",
            style_filename=self.default_style_filename,
        )

    @staticmethod
    def _profile_payload(profile: UserProfile) -> dict[str, Any]:
        return {
            "user_id": profile.user_id,
            "name": profile.name,
            "role": profile.role,
            "memory_silo": profile.memory_silo,
            "voice_embedding_path": profile.voice_embedding_path,
            "style_filename": profile.style_filename,
        }

    def activate_profile(self, profile: UserProfile) -> UserSwitchResult:
        with self._lock:
            payload = self.load_payload()
            users = dict(payload.get("users") or {})
            created = bool(not profile.is_unknown and profile.user_id not in users)
            if not profile.is_unknown:
                users[profile.user_id] = self._profile_payload(profile)
            payload["users"] = users
            payload["active_user_id"] = profile.user_id
            auth = self._coerce_auth_payload(payload.get("auth"), users=users)
            if not profile.is_admin and not profile.is_unknown:
                auth["last_public_user_id"] = profile.user_id
            payload["auth"] = auth
            self.save_payload(payload)
        return UserSwitchResult(profile=profile, created=created)

    def switch_active_user(self, token: str) -> UserSwitchResult:
        normalized = " ".join(str(token or "").strip().split())
        if not normalized:
            return UserSwitchResult(profile=self.active_profile(), created=False)
        existing = self.resolve_profile(normalized)
        profile = existing
        if profile is None:
            profile = self._new_standard_profile(normalized)
        return self.activate_profile(profile)

    def set_active_style_filename(self, style_filename: str) -> None:
        filename = str(style_filename or "").strip()
        if not filename:
            return
        active = self.active_profile()
        if active.is_unknown:
            return
        with self._lock:
            payload = self.load_payload()
            users = dict(payload.get("users") or {})
            raw = dict(users.get(active.user_id) or {})
            raw["style_filename"] = filename
            users[active.user_id] = raw
            payload["users"] = users
            self.save_payload(payload)

    def admin_password_record(self) -> dict[str, Any]:
        with self._lock:
            payload = self.load_payload()
            users = dict(payload.get("users") or {})
            return dict(self._coerce_auth_payload(payload.get("auth"), users=users))

    def admin_password_configured(self) -> bool:
        auth = self.admin_password_record()
        return bool(str(auth.get("admin_password_hash") or "").strip() and str(auth.get("admin_password_salt") or "").strip())

    def set_admin_password_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            payload = self.load_payload()
            users = dict(payload.get("users") or {})
            auth = self._coerce_auth_payload(payload.get("auth"), users=users)
            auth["admin_password_hash"] = str(record.get("admin_password_hash") or "").strip()
            auth["admin_password_salt"] = str(record.get("admin_password_salt") or "").strip()
            try:
                auth["admin_password_iterations"] = max(
                    int(record.get("admin_password_iterations") or DEFAULT_PASSWORD_ITERATIONS),
                    1,
                )
            except Exception:
                auth["admin_password_iterations"] = DEFAULT_PASSWORD_ITERATIONS
            payload["auth"] = auth
            self.save_payload(payload)

    def public_fallback_profile(self) -> UserProfile:
        with self._lock:
            payload = self.load_payload()
            users = dict(payload.get("users") or {})
            auth = self._coerce_auth_payload(payload.get("auth"), users=users)
            fallback_id = str(auth.get("last_public_user_id") or DEFAULT_GUEST_USER_ID).strip() or DEFAULT_GUEST_USER_ID
            raw = users.get(fallback_id) or users.get(DEFAULT_GUEST_USER_ID) or self._default_guest_payload()
        return self._coerce_profile(raw, fallback_id=fallback_id)


class ActiveUserRuntime:
    def __init__(
        self,
        data_dir: Path,
        llm_client: Any,
        *,
        admin_user_id: str = DEFAULT_ADMIN_USER_ID,
        admin_name: str = DEFAULT_ADMIN_NAME,
        default_style_filename: str = "default.style",
    ) -> None:
        self.data_dir = Path(data_dir)
        self.llm_client = llm_client
        self.registry = UserRegistry(
            self.data_dir,
            admin_user_id=admin_user_id,
            admin_name=admin_name,
            default_style_filename=default_style_filename,
        )
        self._lock = threading.RLock()
        self._state_owners: dict[str, SharedStateOwner] = {}
        self._knowledge_managers: dict[str, WorldModelManager] = {}
        self._transient_managers: dict[str, TransientStateManager] = {}
        self._document_managers: dict[str, DocumentMemoryManager] = {}
        self._admin_unlocked = False
        self._pending_admin_password_user_id = ""
        self._normalize_boot_identity_state()

    def list_profiles(self) -> list[UserProfile]:
        return self.registry.list_profiles()

    def active_profile(self) -> UserProfile:
        return self.registry.active_profile()

    def switch_active_user(self, token: str) -> UserSwitchResult:
        result = self.registry.switch_active_user(token)
        self._initialize_profile_state(result.profile)
        self._admin_unlocked = bool(result.profile.is_admin)
        self._pending_admin_password_user_id = ""
        if not result.profile.is_admin and not result.profile.is_unknown:
            self._mirror_profile_graph_to_admin(result.profile.user_id)
        return result

    def profile_role_label(self, profile: UserProfile | None = None) -> str:
        target = profile or self.active_profile()
        if target.is_admin:
            return "admin"
        if target.is_unknown:
            return "unknown"
        return "user"

    @staticmethod
    def _node_matches_identity_name(node: dict[str, Any], name: str) -> bool:
        candidate = _normalize_token(name)
        if not candidate or not isinstance(node, dict):
            return False
        if _normalize_token(node.get("label") or "") == candidate:
            return True
        aliases = [_normalize_token(item) for item in (node.get("aliases") or [])]
        return candidate in aliases

    def _identity_candidates_for_name(self, name: str) -> list[IdentityCandidate]:
        normalized_name = _normalize_token(name)
        if not normalized_name:
            return []
        by_user_id: dict[str, IdentityCandidate] = {}
        for profile in self.registry.matching_profiles(name):
            if profile.is_admin or profile.is_unknown:
                continue
            by_user_id[profile.user_id] = IdentityCandidate(
                user_id=profile.user_id,
                name=profile.name,
                relations=(),
                profile=profile,
            )

        admin_graph = self.knowledge_manager_for(self.registry.admin_user_id).load_graph()
        root_id = str(admin_graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        nodes = admin_graph.get("nodes") or {}
        relations_by_target: dict[str, set[str]] = {}
        for edge in admin_graph.get("edges") or []:
            if not isinstance(edge, dict) or not _active_edge(edge):
                continue
            if str(edge.get("source") or "") != root_id:
                continue
            target_id = str(edge.get("target") or "").strip()
            if not target_id:
                continue
            relation_name = str(edge.get("relation") or "").strip().lower()
            if not relation_name:
                continue
            relations_by_target.setdefault(target_id, set()).add(relation_name)

        for node_id, node in nodes.items():
            if not isinstance(node, dict) or str(node_id) == root_id:
                continue
            if not str(node_id).startswith("person:"):
                continue
            if not self._node_matches_identity_name(node, name):
                continue
            user_id = str(node_id).split(":", 1)[1].strip()
            if not user_id:
                continue
            existing = by_user_id.get(user_id)
            label = " ".join(str(node.get("label") or name).strip().split()) or name
            relation_tuple = tuple(sorted(relations_by_target.get(str(node_id), set())))
            profile = self.registry.profile_for_id(user_id)
            if existing is None:
                by_user_id[user_id] = IdentityCandidate(
                    user_id=user_id,
                    name=label,
                    relations=relation_tuple,
                    profile=profile,
                )
                continue
            merged_relations = tuple(sorted({*existing.relations, *relation_tuple}))
            by_user_id[user_id] = IdentityCandidate(
                user_id=existing.user_id,
                name=existing.name or label,
                relations=merged_relations,
                profile=existing.profile or profile,
            )

        return sorted(
            by_user_id.values(),
            key=lambda item: (
                item.name.lower(),
                item.user_id,
            ),
        )

    @staticmethod
    def _identity_candidate_choice_label(candidate: IdentityCandidate) -> str:
        relation_label = _relation_disambiguation_label(candidate.primary_relation)
        if relation_label:
            return relation_label
        return f"profile {candidate.user_id}"

    def _build_identity_clarification_message(self, name: str, candidates: list[IdentityCandidate]) -> str:
        choice_labels = [self._identity_candidate_choice_label(candidate) for candidate in candidates]
        unique_choices: list[str] = []
        for label in choice_labels:
            if label not in unique_choices:
                unique_choices.append(label)
        if len(unique_choices) >= 2 and all(not label.startswith("profile ") for label in unique_choices):
            question = f"Are you {_join_with_or(unique_choices)}?"
        else:
            question = "Tell me how you know Baris so I can pick the right one."
        clean_name = " ".join(str(name or "").strip().split()) or "that name"
        return f"[UI] I know more than one person named {clean_name} in Baris's world memory. {question}"

    def _resolve_identity_target_profile(self, name: str, relation_hint: str = "") -> tuple[UserProfile | None, bool, str]:
        normalized_token = _normalize_token(name)
        if not normalized_token:
            return None, False, ""

        direct_matches = self.registry.matching_profiles(name)
        exact_profile = next(
            (
                profile
                for profile in direct_matches
                if _normalize_token(profile.user_id) == normalized_token
            ),
            None,
        )
        if exact_profile is not None:
            return exact_profile, False, ""
        if len(direct_matches) == 1 and direct_matches[0].is_admin:
            return direct_matches[0], False, ""

        candidates = self._identity_candidates_for_name(name)
        relation_key = str(relation_hint or "").strip().lower()

        if relation_key:
            relation_matches = [
                candidate
                for candidate in candidates
                if relation_key in {item.lower() for item in candidate.relations}
            ]
            if len(relation_matches) == 1:
                target = relation_matches[0]
                if target.profile is not None:
                    return target.profile, False, ""
                return self.registry._new_standard_profile(target.name or name, preferred_user_id=target.user_id), True, ""
            if len(relation_matches) > 1:
                return None, False, self._build_identity_clarification_message(name, relation_matches)
            if len(candidates) == 1 and not candidates[0].relations:
                target = candidates[0]
                if target.profile is not None:
                    return target.profile, False, ""
                return self.registry._new_standard_profile(target.name or name, preferred_user_id=target.user_id), True, ""
            if len(candidates) > 1:
                return None, False, self._build_identity_clarification_message(name, candidates)
            preferred_user_id = _relation_scoped_user_id(name, relation_key)
            return self.registry._new_standard_profile(name, preferred_user_id=preferred_user_id), True, ""

        if len(candidates) == 1:
            target = candidates[0]
            if target.profile is not None:
                return target.profile, False, ""
            return self.registry._new_standard_profile(target.name or name, preferred_user_id=target.user_id), True, ""
        if len(candidates) > 1:
            return None, False, self._build_identity_clarification_message(name, candidates)
        return self.registry._new_standard_profile(name), True, ""

    def _extract_bare_known_identity_name(self, text: str) -> str:
        raw = " ".join(str(text or "").strip().split())
        if not raw:
            return ""
        if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{0,40}[.]?", raw):
            return ""
        candidate = _clean_identity_name(raw)
        if not candidate:
            return ""
        normalized_candidate = _normalize_token(candidate)
        if not normalized_candidate:
            return ""
        for profile in self.registry.matching_profiles(candidate):
            if _normalize_token(profile.name) == normalized_candidate:
                return candidate
            if _normalize_token(profile.user_id) == normalized_candidate:
                return candidate
        return ""

    def _activate_target_profile(self, target_profile: UserProfile) -> UserSwitchResult:
        result = self.registry.activate_profile(target_profile)
        self._initialize_profile_state(result.profile)
        self._admin_unlocked = bool(result.profile.is_admin)
        self._pending_admin_password_user_id = ""
        if not result.profile.is_admin and not result.profile.is_unknown:
            self._mirror_profile_graph_to_admin(result.profile.user_id)
            # Start voice enrollment for newly identified user
            try:
                from core.voice_recognition import get_voice_engine
                from config import CFG
                if CFG.VOICE_RECOGNITION_ENABLED:
                    engine = get_voice_engine()
                    if engine.available() and not engine._embeddings.get(result.profile.user_id):
                        engine.start_enrollment(result.profile.user_id)
            except Exception:
                pass
        return result

    def request_typed_user_switch(self, token: str) -> UserActivationResult:
        normalized = " ".join(str(token or "").strip().split())
        if not normalized:
            profile = self.active_profile()
            return UserActivationResult(status="noop", profile=profile, message=self.active_user_label())
        target_profile, created, clarification_message = self._resolve_identity_target_profile(normalized)
        if target_profile is None:
            profile = self.active_profile()
            return UserActivationResult(
                status="identity_clarification_required",
                profile=profile,
                created=False,
                message=clarification_message or "[UI] I need one more detail before I can switch users.",
            )
        if target_profile.is_admin and self.registry.admin_password_configured() and not self._admin_unlocked:
            self._pending_admin_password_user_id = target_profile.user_id
            return UserActivationResult(
                status="password_required",
                profile=target_profile,
                created=False,
                message=f"[UI] Password required for {target_profile.name} [{target_profile.user_id}; admin]. Type the password now or /cancel.",
            )
        result = self._activate_target_profile(target_profile)
        profile = result.profile
        role = "admin" if profile.is_admin else "user"
        if target_profile.is_admin and not self.registry.admin_password_configured():
            hint = " Admin password is not configured yet. Run /adminpass <password> while signed in to protect owner memory."
        else:
            hint = ""
        if result.created:
            message = f"[UI] Created and switched to {profile.name} [{profile.user_id}; {role}].{hint}"
        else:
            message = f"[UI] Switched to {profile.name} [{profile.user_id}; {role}].{hint}"
        return UserActivationResult(status="switched", profile=profile, created=result.created, message=message)

    def observe_typed_identity_hint(self, text: str) -> UserActivationResult | None:
        candidate = _extract_self_identified_name(text)
        relation_hint = _extract_relation_to_admin(text)
        current = self.active_profile()
        if not candidate and current.is_unknown:
            candidate = self._extract_bare_known_identity_name(text)
        if not candidate:
            if relation_hint and not current.is_admin and not current.is_unknown:
                self._upsert_admin_relationship_hint(current.user_id, relation_hint)
            return None
        target_profile, created, clarification_message = self._resolve_identity_target_profile(candidate, relation_hint)
        if target_profile is None:
            return UserActivationResult(
                status="identity_clarification_required",
                profile=current,
                created=False,
                message=clarification_message or "[UI] I need one more detail to identify who is speaking.",
            )
        if current.user_id == target_profile.user_id and not current.is_unknown:
            if relation_hint and not current.is_admin:
                self._upsert_admin_relationship_hint(current.user_id, relation_hint)
            return UserActivationResult(status="noop", profile=current, created=False, message="")
        if target_profile.is_admin:
            result = self.request_typed_user_switch(target_profile.user_id)
        else:
            switch_result = self._activate_target_profile(target_profile)
            result = UserActivationResult(
                status="switched",
                profile=switch_result.profile,
                created=switch_result.created,
                message="",
            )
        if result.switched and relation_hint and not result.profile.is_admin and not result.profile.is_unknown:
            self._upsert_admin_relationship_hint(result.profile.user_id, relation_hint)
        # Voice enrollment is triggered inside _activate_target_profile; no extra work needed here.
        return result

    def is_waiting_for_admin_password(self) -> bool:
        return bool(str(self._pending_admin_password_user_id or "").strip())

    def cancel_pending_admin_password(self) -> str:
        self._pending_admin_password_user_id = ""
        return "[UI] Admin sign-in canceled."

    def submit_admin_password(self, password: str) -> UserActivationResult:
        pending_user_id = str(self._pending_admin_password_user_id or "").strip()
        if not pending_user_id:
            profile = self.active_profile()
            return UserActivationResult(
                status="blocked",
                profile=profile,
                message="[UI] No admin password prompt is active.",
            )
        target_profile = self.registry.profile_for_id(pending_user_id) or self.registry.profile_for_id(self.registry.admin_user_id)
        if target_profile is None:
            self._pending_admin_password_user_id = ""
            profile = self.active_profile()
            return UserActivationResult(
                status="blocked",
                profile=profile,
                message="[UI] Admin profile is unavailable.",
            )
        if not self.verify_admin_password(password):
            return UserActivationResult(
                status="password_failed",
                profile=self.active_profile(),
                message="[UI] Incorrect admin password. Try again or /cancel.",
            )
        self._pending_admin_password_user_id = ""
        self.switch_active_user(target_profile.user_id)
        self._admin_unlocked = True
        profile = self.active_profile()
        return UserActivationResult(
            status="switched",
            profile=profile,
            message=f"[UI] Switched to {profile.name} [{profile.user_id}; admin].",
        )

    def admin_password_configured(self) -> bool:
        return self.registry.admin_password_configured()

    def verify_admin_password(self, password: str) -> bool:
        auth = self.registry.admin_password_record()
        digest = str(auth.get("admin_password_hash") or "").strip()
        salt = str(auth.get("admin_password_salt") or "").strip()
        iterations = int(auth.get("admin_password_iterations") or DEFAULT_PASSWORD_ITERATIONS)
        if not digest or not salt:
            return False
        actual = _pbkdf2_digest(password, salt_b64=salt, iterations=iterations)
        return bool(actual) and hmac.compare_digest(actual, digest)

    def set_admin_password(self, password: str) -> AdminPasswordResult:
        text = str(password or "")
        if not text.strip():
            return AdminPasswordResult(False, "[UI] Usage: /adminpass <password>")
        active = self.active_profile()
        if not active.is_admin:
            return AdminPasswordResult(False, "[UI] Switch to Baris first to set the admin password.")
        if self.registry.admin_password_configured() and not self._admin_unlocked:
            return AdminPasswordResult(False, "[UI] Admin password is locked. Unlock Baris before changing it.")
        record = _new_password_record(text)
        self.registry.set_admin_password_record(record)
        self._admin_unlocked = True
        self._pending_admin_password_user_id = ""
        return AdminPasswordResult(True, "[UI] Admin password saved.")

    def is_admin_unlocked(self) -> bool:
        return bool(self._admin_unlocked and self.active_profile().is_admin)

    def current_user_data_dir(self) -> Path:
        return self.active_profile().resolved_data_dir(self.data_dir)

    def current_memory_path(self) -> Path:
        return data_state_path(self.current_user_data_dir(), "memory.jsonl")

    def current_conversation_summary_path(self) -> Path:
        return self.current_user_data_dir() / "conversation_summary.json"

    def current_style_filename(self) -> str:
        profile = self.active_profile()
        filename = str(profile.style_filename or "").strip()
        if filename:
            return filename
        default_filename = str(self.registry.default_style_filename or "").strip()
        return default_filename or "default.style"

    def set_active_style_filename(self, style_filename: str) -> None:
        self.registry.set_active_style_filename(style_filename)

    def active_user_label(self) -> str:
        profile = self.active_profile()
        role = self.profile_role_label(profile)
        return f"{profile.name} [{profile.user_id}; {role}]"

    def render_active_user_block(self) -> str:
        profile = self.active_profile()
        lines = [
            "[ACTIVE USER]",
            f"User ID: {profile.user_id}",
            f"Name: {profile.name}",
            f"Role: {self.profile_role_label(profile)}",
        ]
        if profile.is_admin:
            lines.append("Access: Owner profile unlocked. Use this user's full private memory and configuration.")
        elif profile.is_unknown:
            lines.append("Access: Unknown public speaker. Do not assume you know who this is.")
            lines.append("Identity: Ask one short natural question to learn who is speaking before relying on personal memory.")
        else:
            lines.append("Access: Public profile. Use only this user's memory silo and do not reveal admin-private memory.")
            lines.append(
                "Privacy: If this speaker asks about Baris or owner-private details, explain that Baris's private memory is not surfaced in this public session."
            )
            lines.append("Do not frame that boundary as not knowing Baris well.")
            lines.extend(self._active_public_profile_gap_lines(profile))
        return "\n".join(lines)

    def _normalize_boot_identity_state(self) -> None:
        self._reset_unknown_runtime_state()
        self.registry.switch_active_user(DEFAULT_GUEST_USER_ID)
        self._initialize_profile_state(self.registry.active_profile())

    def _reset_unknown_runtime_state(self) -> None:
        profile = self.registry.profile_for_id(DEFAULT_GUEST_USER_ID)
        if profile is None:
            return
        target_dir = profile.resolved_data_dir(self.data_dir)
        try:
            shutil.rmtree(target_dir)
        except FileNotFoundError:
            return
        except Exception:
            for child in (
                target_dir / "state" / "memory.jsonl",
                target_dir / "conversation_summary.json",
            ):
                try:
                    child.unlink(missing_ok=True)
                except Exception:
                    pass

    def _initialize_profile_state(self, profile: UserProfile) -> None:
        user_data_dir = profile.resolved_data_dir(self.data_dir)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        owner = self.state_owner_for(profile.user_id)
        store = owner.world_model_store
        graph = store.load_graph()
        root_id = str(graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        nodes = graph.setdefault("nodes", {})
        root = nodes.get(root_id) or {}
        if not isinstance(root, dict):
            root = {}
        root.setdefault("id", root_id)
        root.setdefault("type", "person")
        aliases = [str(item).strip() for item in (root.get("aliases") or []) if str(item).strip()]
        alias_tokens = {item.lower() for item in aliases}
        if "user" not in alias_tokens:
            aliases.append("user")
        if "me" not in alias_tokens:
            aliases.append("me")
        if profile.name and profile.name.lower() not in alias_tokens:
            aliases.append(profile.name)
        if not str(root.get("label") or "").strip() or str(root.get("label") or "").strip().lower() == "user":
            root["label"] = profile.name
        root["aliases"] = aliases
        root["updated_at"] = int(time.time())
        if not isinstance(root.get("attributes"), dict):
            root["attributes"] = {}
        nodes[root_id] = root
        graph["nodes"] = nodes
        store.save_graph(graph)

    def state_owner_for(self, user_id: str) -> SharedStateOwner:
        key = _slugify(user_id)
        with self._lock:
            owner = self._state_owners.get(key)
            if owner is not None:
                return owner
            profile = self.registry.profile_for_id(key)
            if profile is None:
                raise KeyError(f"Unknown user_id: {user_id}")
            owner = SharedStateOwner.for_data_dir(profile.resolved_data_dir(self.data_dir))
            self._state_owners[key] = owner
            return owner

    def current_state_owner(self) -> SharedStateOwner:
        return self.state_owner_for(self.active_profile().user_id)

    def knowledge_manager_for(self, user_id: str) -> WorldModelManager:
        key = _slugify(user_id)
        with self._lock:
            manager = self._knowledge_managers.get(key)
            if manager is not None:
                return manager
            owner = self.state_owner_for(key)
            profile = self.registry.profile_for_id(key)
            if profile is None:
                raise KeyError(f"Unknown user_id: {user_id}")
            manager = WorldModelManager(
                profile.resolved_data_dir(self.data_dir),
                self.llm_client,
                world_model_store=owner.world_model_store,
                knowledge_store=owner.knowledge_store,
            )
            if hasattr(manager, "set_graph_saved_callback"):
                if key not in {self.registry.admin_user_id, DEFAULT_GUEST_USER_ID}:
                    manager.set_graph_saved_callback(
                        lambda graph, mirrored_user_id=key: self._mirror_profile_graph_to_admin(
                            mirrored_user_id,
                            source_graph=graph,
                        )
                    )
                else:
                    manager.set_graph_saved_callback(None)
            self._knowledge_managers[key] = manager
            return manager

    def current_knowledge_manager(self) -> WorldModelManager:
        return self.knowledge_manager_for(self.active_profile().user_id)

    def transient_state_manager_for(self, user_id: str) -> TransientStateManager:
        key = _slugify(user_id)
        with self._lock:
            manager = self._transient_managers.get(key)
            if manager is not None:
                return manager
            owner = self.state_owner_for(key)
            manager = TransientStateManager(
                situational_store=owner.situational_state_store,
                intent_store=owner.intent_state_store,
                knowledge_mgr=self.knowledge_manager_for(key),
            )
            self._transient_managers[key] = manager
            return manager

    def current_transient_state_manager(self) -> TransientStateManager:
        return self.transient_state_manager_for(self.active_profile().user_id)

    def document_manager_for(self, user_id: str) -> DocumentMemoryManager:
        key = _slugify(user_id)
        with self._lock:
            manager = self._document_managers.get(key)
            if manager is not None:
                return manager
            profile = self.registry.profile_for_id(key)
            if profile is None:
                raise KeyError(f"Unknown user_id: {user_id}")
            manager = DocumentMemoryManager(profile.resolved_data_dir(self.data_dir))
            self._document_managers[key] = manager
            return manager

    def current_document_manager(self) -> DocumentMemoryManager:
        return self.document_manager_for(self.active_profile().user_id)

    def brain_for(self, user_id: str) -> Any:
        profile = self.registry.profile_for_id(user_id)
        if profile is None:
            raise KeyError(f"Unknown user_id: {user_id}")
        return get_brain(profile.resolved_data_dir(self.data_dir))

    def current_brain(self) -> Any:
        return self.brain_for(self.active_profile().user_id)

    def _active_public_profile_gap_lines(self, profile: UserProfile) -> list[str]:
        if profile.is_admin:
            return []
        if profile.is_unknown:
            return [
                "Profile Gap: the speaker has not been identified yet.",
                "If natural, ask one short question to learn their name. Examples: 'I don't think we've been introduced — what's your name?', 'Who am I chatting with?'",
                "Once you learn their name, ask how they know Baris if that connection isn't clear.",
            ]
        graph = self.knowledge_manager_for(self.registry.admin_user_id).load_graph()
        nodes = graph.get("nodes") or {}
        root_id = str(graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        entity_id = f"person:{_slugify(profile.user_id)}"
        node = nodes.get(entity_id) or {}
        edges = [edge for edge in (graph.get("edges") or []) if isinstance(edge, dict)]
        has_relation = any(
            str(edge.get("source") or "") == root_id and str(edge.get("target") or "") == entity_id
            for edge in edges
        )
        lines: list[str] = ["Baris Memory Mirror: stable facts about this speaker should also live in Baris's people memory."]
        if not node:
            lines.append("Profile Gap: Baris does not yet have a person record for this speaker.")
            lines.append("If natural, ask one short question to confirm who they are.")
            return lines
        if not has_relation:
            lines.append("Profile Gap: this speaker's relationship to Baris is still unknown.")
            lines.append("If natural, ask one short question about how they know Baris.")
        return lines

    def _mirror_profile_graph_to_admin(self, user_id: str, *, source_graph: dict[str, Any] | None = None) -> None:
        key = _slugify(user_id)
        if key in {self.registry.admin_user_id, DEFAULT_GUEST_USER_ID}:
            return
        profile = self.registry.profile_for_id(key)
        if profile is None or profile.is_admin or profile.is_unknown:
            return
        source = source_graph or self.knowledge_manager_for(key).load_graph()
        source_nodes = source.get("nodes") or {}
        source_root_id = str(source.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        source_root = source_nodes.get(source_root_id) or {}
        if not isinstance(source_root, dict):
            return

        admin_manager = self.knowledge_manager_for(self.registry.admin_user_id)
        admin_graph = admin_manager.load_graph()
        admin_root = admin_manager._ensure_root(admin_graph)
        admin_root.setdefault("label", self.registry.admin_name)

        entity_id = f"person:{_slugify(profile.user_id)}"
        label = str(source_root.get("label") or profile.name or profile.user_id).strip() or profile.user_id
        person_node = admin_manager._ensure_node(admin_graph, entity_id, "person", label)
        person_node["label"] = label

        aliases = [str(item).strip() for item in (source_root.get("aliases") or []) if str(item).strip()]
        aliases.extend([profile.name, profile.user_id])
        person_node["aliases"] = self._normalize_aliases([item for item in aliases if item.lower() not in {"user", "me"}])

        attributes = person_node.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
            person_node["attributes"] = attributes
        source_attributes = source_root.get("attributes") or {}
        for name, entries in source_attributes.items():
            canonical = str(name or "").strip().lower()
            if not canonical or canonical.startswith(("pending_", "temporary_", "temp_", "current_", "recent_")):
                continue
            stable_entries: list[dict[str, Any]] = []
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("expires_at") is not None:
                    continue
                value = str(entry.get("value") or "").strip()
                if not value:
                    continue
                stable_entries.append(
                    {
                        "value": value,
                        "updated_at": int(entry.get("updated_at") or time.time()),
                        "expires_at": None,
                    }
                )
            if stable_entries:
                attributes[canonical] = stable_entries
        person_node["updated_at"] = int(time.time())
        admin_manager._save_graph(admin_graph)

    def _upsert_admin_relationship_hint(self, user_id: str, relation: str) -> None:
        key = _slugify(user_id)
        if key in {self.registry.admin_user_id, DEFAULT_GUEST_USER_ID}:
            return
        profile = self.registry.profile_for_id(key)
        if profile is None:
            return
        admin_manager = self.knowledge_manager_for(self.registry.admin_user_id)
        admin_graph = admin_manager.load_graph()
        root_id = str(admin_graph.get("root_entity_id") or WorldModelStore.ROOT_ENTITY_ID)
        target_id = f"person:{_slugify(profile.user_id)}"
        admin_manager._ensure_root(admin_graph)
        target = admin_manager._ensure_node(admin_graph, target_id, "person", profile.name)
        target["aliases"] = self._normalize_aliases([*(target.get("aliases") or []), profile.name, profile.user_id])
        admin_manager._merge_relationship(
            admin_graph,
            {
                "source": root_id,
                "relation": relation,
                "target": {"id": target_id, "type": "person", "label": profile.name},
                "mode": "add",
            },
            user_history_text=profile.name.lower(),
        )
        admin_manager._save_graph(admin_graph)

    @staticmethod
    def _normalize_aliases(values: list[str]) -> list[str]:
        aliases: list[str] = []
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


class ActiveUserStateOwnerProxy:
    def __init__(self, user_runtime: ActiveUserRuntime) -> None:
        self.user_runtime = user_runtime

    @property
    def data_dir(self) -> Path:
        return self.user_runtime.current_state_owner().data_dir

    @property
    def task_store(self) -> Any:
        return self.user_runtime.current_state_owner().task_store

    @property
    def event_store(self) -> Any:
        return self.user_runtime.current_state_owner().event_store

    @property
    def knowledge_store(self) -> Any:
        return self.user_runtime.current_state_owner().knowledge_store

    @property
    def world_model_store(self) -> Any:
        return self.user_runtime.current_state_owner().world_model_store

    @property
    def situational_state_store(self) -> Any:
        return self.user_runtime.current_state_owner().situational_state_store

    @property
    def intent_state_store(self) -> Any:
        return self.user_runtime.current_state_owner().intent_state_store


class ActiveUserKnowledgeManagerProxy:
    def __init__(self, user_runtime: ActiveUserRuntime) -> None:
        object.__setattr__(self, "user_runtime", user_runtime)
        object.__setattr__(self, "_attribute_overrides", {})
        object.__setattr__(self, "_logger_callback", None)

    def _current(self) -> Any:
        manager = self.user_runtime.current_knowledge_manager()
        logger = object.__getattribute__(self, "_logger_callback")
        if logger is not None:
            manager.set_logger(logger)
        for name, value in dict(object.__getattribute__(self, "_attribute_overrides")).items():
            setattr(manager, name, value)
        return manager

    def set_logger(self, callback) -> None:
        object.__setattr__(self, "_logger_callback", callback)
        self._current().set_logger(callback)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._current(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"user_runtime", "_attribute_overrides", "_logger_callback"}:
            object.__setattr__(self, name, value)
            return
        overrides = dict(object.__getattribute__(self, "_attribute_overrides"))
        overrides[name] = value
        object.__setattr__(self, "_attribute_overrides", overrides)
        setattr(self._current(), name, value)


class ActiveUserTransientStateManagerProxy:
    def __init__(self, user_runtime: ActiveUserRuntime) -> None:
        self.user_runtime = user_runtime

    def _current(self) -> Any:
        return self.user_runtime.current_transient_state_manager()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._current(), name)


class ActiveUserBrainProxy:
    def __init__(self, user_runtime: ActiveUserRuntime) -> None:
        self.user_runtime = user_runtime

    def recall(self, query: str, n_results: int = 10) -> list[dict[str, Any]]:
        return self.user_runtime.current_brain().recall(query, n_results=n_results)

    def remember(self, text: str, metadata: dict[str, Any] | None = None, doc_id: str | None = None) -> None:
        # Privacy model: unknown users do not write to long-term vector memory.
        try:
            if self.user_runtime.active_profile().is_unknown:
                return
        except Exception:
            pass
        self.user_runtime.current_brain().remember(text=text, metadata=metadata or {}, doc_id=doc_id)


class ActiveUserDocumentMemoryProxy:
    def __init__(self, user_runtime: ActiveUserRuntime) -> None:
        self.user_runtime = user_runtime

    def _current(self) -> DocumentMemoryManager:
        return self.user_runtime.current_document_manager()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._current(), name)
