from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
from dataclasses import asdict, dataclass
from pathlib import Path

# Prevent heavy ML libraries from hanging the smoke test at import time.
class _StubModule(types.ModuleType):
    def __getattr__(self, name: str):
        raise ImportError(f"{self.__name__} is stubbed in smoke test")

for _mod_name in ("resemblyzer", "sentence_transformers"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _StubModule(_mod_name)

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
from core.commands import handle_command
from core.style import StyleManager
import memory.brain as brain_module
from memory.user_runtime import ActiveUserRuntime

brain_module.PiperBrain._vector_backend_dependencies_available = staticmethod(lambda: False)


@dataclass(frozen=True)
class UserRuntimeSmokeReport:
    success: bool
    admin_user_id: str
    unknown_user_id: str
    standard_user_id: str
    startup_active_user_id: str
    unknown_memory_path: str
    admin_memory_path: str
    standard_memory_path: str
    unknown_summary_path: str
    admin_summary_path: str
    standard_summary_path: str
    admin_document_dir: str
    standard_document_dir: str
    admin_tasks: list[str]
    standard_tasks: list[str]
    standard_root_label: str
    admin_style: str
    standard_style: str
    restart_public_user_id: str
    command_checks: dict[str, bool]
    identity_checks: dict[str, bool]
    auth_checks: dict[str, bool]
    harness_checks: dict[str, bool]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _runtime_checks() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="piper-user-runtime-") as raw_tmp:
        data_dir = Path(raw_tmp) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        style_mgr = StyleManager(data_dir / "styles", active_filename="jarvis.style")
        runtime = ActiveUserRuntime(
            data_dir,
            llm_client=None,
            admin_user_id="admin_baris",
            admin_name="Baris",
            default_style_filename=style_mgr.active_filename,
        )

        startup_profile = runtime.active_profile()
        unknown_memory_path = runtime.current_memory_path()
        unknown_summary_path = runtime.current_conversation_summary_path()
        admin_profile = runtime.registry.profile_for_id("admin_baris")
        users_payload = json.loads((data_dir / "users.json").read_text(encoding="utf-8"))
        unknown_memory_path.parent.mkdir(parents=True, exist_ok=True)
        unknown_memory_path.write_text('{"role":"user","content":"stale unknown turn"}\n', encoding="utf-8")
        unknown_summary_path.write_text('{"summary":"stale unknown summary"}\n', encoding="utf-8")

        relation_phrase_result = runtime.observe_typed_identity_hint("i am his friend, do you know baris?")
        profile_after_relation_phrase = runtime.active_profile()
        fake_profile_after_relation_phrase = runtime.registry.profile_for_id("his_friend")
        reserved_phrase_result = runtime.observe_typed_identity_hint("I'm still calibrating")
        profile_after_reserved_phrase = runtime.active_profile()
        reserved_profile = runtime.registry.profile_for_id("still_calibrating")
        mood_phrase_result = runtime.observe_typed_identity_hint("not bad")
        profile_after_mood_phrase = runtime.active_profile()
        mood_profile = runtime.registry.profile_for_id("not")

        switch_result = runtime.observe_typed_identity_hint("I'm Max")
        standard_profile = runtime.active_profile()
        generic_relation_result = runtime.observe_typed_identity_hint("i am his friend, do you know baris?")
        runtime.current_state_owner().task_store.add("max_task", "pending")
        runtime.set_active_style_filename("grey.style")
        runtime.current_knowledge_manager().upsert_fact("favorite_drink", "coffee")

        runtime.switch_active_user("unknown")
        ekin_partner_intro = runtime.observe_typed_identity_hint("I'm Ekin, Baris's partner")
        ekin_partner_profile = runtime.active_profile()
        runtime.switch_active_user("unknown")
        ekin_friend_intro = runtime.observe_typed_identity_hint("I'm Ekin, Baris's friend")
        ekin_friend_profile = runtime.active_profile()
        admin_graph_after_same_name_split = runtime.knowledge_manager_for("admin_baris").load_graph()
        same_name_edges = [
            edge
            for edge in (admin_graph_after_same_name_split.get("edges") or [])
            if isinstance(edge, dict)
            and str(edge.get("source") or "") == "person:user"
            and str(edge.get("target") or "").startswith("person:ekin")
        ]
        runtime.switch_active_user("unknown")
        ambiguous_ekin = runtime.observe_typed_identity_hint("I'm Ekin")
        active_after_ambiguous_ekin = runtime.active_profile().user_id
        manual_ekin_switch = runtime.request_typed_user_switch("Ekin")
        active_after_manual_ekin_switch = runtime.active_profile().user_id
        runtime.switch_active_user("unknown")
        resolved_friend_ekin = runtime.observe_typed_identity_hint("I'm Ekin, Baris's friend")
        active_after_resolved_friend_ekin = runtime.active_profile().user_id
        runtime.switch_active_user(standard_profile.user_id)

        spelling_data_dir = Path(raw_tmp) / "spelling_case"
        spelling_runtime = ActiveUserRuntime(
            spelling_data_dir,
            llm_client=None,
            admin_user_id="admin_baris",
            admin_name="Baris",
            default_style_filename=style_mgr.active_filename,
        )
        spelling_akin_intro = spelling_runtime.observe_typed_identity_hint("I'm Akin")
        spelling_akin_profile = spelling_runtime.active_profile()
        spelling_correction = spelling_runtime.observe_typed_identity_hint("I will spell my name, e-k-i-n.")
        spelling_corrected_profile = spelling_runtime.active_profile()
        spelling_alias_matches = spelling_runtime.registry.matching_profiles("Akin")
        spelling_duplicate_ekin_profile = spelling_runtime.registry.profile_for_id("ekin")
        spelling_relation = spelling_runtime.observe_typed_identity_hint("I am his girlfriend. I am Turkish.")
        spelling_admin_graph = spelling_runtime.knowledge_manager_for("admin_baris").load_graph()
        spelling_partner_relation = any(
            str(edge.get("source") or "") == "person:user"
            and str(edge.get("target") or "") == f"person:{spelling_corrected_profile.user_id}"
            and str(edge.get("relation") or "") == "partner"
            for edge in (spelling_admin_graph.get("edges") or [])
            if isinstance(edge, dict)
        )

        standard_memory_path = runtime.current_memory_path()
        standard_summary_path = runtime.current_conversation_summary_path()
        standard_document_dir = runtime.current_document_manager().data_dir
        standard_brain = runtime.current_brain()
        standard_graph = runtime.current_state_owner().world_model_store.load_graph()
        root_id = str(standard_graph.get("root_entity_id") or "root")
        standard_root = dict((standard_graph.get("nodes") or {}).get(root_id) or {})
        admin_graph_after_mirror = runtime.knowledge_manager_for("admin_baris").load_graph()
        admin_nodes = admin_graph_after_mirror.get("nodes") or {}
        admin_edges = admin_graph_after_mirror.get("edges") or []
        mirrored_max = dict(admin_nodes.get("person:max") or {})
        mirrored_attrs = dict(mirrored_max.get("attributes") or {})
        mirrored_drinks = [
            str(entry.get("value") or "").strip()
            for entry in (mirrored_attrs.get("favorite_drink") or [])
            if isinstance(entry, dict)
        ]
        mirrored_friend = any(
            str(edge.get("source") or "") == "person:user"
            and str(edge.get("target") or "") == "person:max"
            and str(edge.get("relation") or "") == "friend"
            for edge in admin_edges
            if isinstance(edge, dict)
        )

        runtime.switch_active_user("admin_baris")
        runtime.current_state_owner().task_store.add("admin_task", "pending")
        runtime.set_active_style_filename("secretary.style")
        admin_style = runtime.current_style_filename()
        admin_memory_path = runtime.current_memory_path()
        admin_summary_path = runtime.current_conversation_summary_path()
        admin_document_dir = runtime.current_document_manager().data_dir
        admin_brain = runtime.current_brain()
        admin_tasks = sorted(runtime.current_state_owner().task_store.load().keys())
        set_password = runtime.set_admin_password("rosebud")

        runtime.switch_active_user(standard_profile.user_id)
        standard_style = runtime.current_style_filename()
        standard_tasks = sorted(runtime.current_state_owner().task_store.load().keys())
        public_active_user_block = runtime.render_active_user_block()

        password_prompt = runtime.request_typed_user_switch("admin_baris")
        active_after_prompt = runtime.active_profile().user_id
        wrong_password = runtime.submit_admin_password("wrong")
        active_after_wrong_password = runtime.active_profile().user_id
        correct_password = runtime.submit_admin_password("rosebud")
        active_after_correct_password = runtime.active_profile().user_id
        style_after_correct_password = runtime.current_style_filename()

        restarted = ActiveUserRuntime(
            data_dir,
            llm_client=None,
            admin_user_id="admin_baris",
            admin_name="Baris",
            default_style_filename=style_mgr.active_filename,
        )
        restart_active_profile = restarted.active_profile()
        restart_admin_statement = restarted.observe_typed_identity_hint("I'm Baris")
        restarted.cancel_pending_admin_password()
        restart_bare_admin_statement = restarted.observe_typed_identity_hint("Baris")
        restart_active_after_bare = restarted.active_profile().user_id
        restarted.cancel_pending_admin_password()
        restart_corrective_admin_statement = restarted.observe_typed_identity_hint("no i mean its me baris")
        restart_active_after_corrective = restarted.active_profile().user_id
        restart_unknown_memory_path = restarted.current_memory_path()
        restart_unknown_summary_path = restarted.current_conversation_summary_path()
        voice_result = restarted.activate_voice_match("baris", 0.94, margin=0.20)
        voice_active_profile = restarted.active_profile()
        voice_admin_unlocked = restarted.is_admin_unlocked()

        return {
            "startup_profile": startup_profile,
            "admin_profile": admin_profile,
            "unknown_profile": runtime.registry.profile_for_id("unknown"),
            "standard_profile": standard_profile,
            "switch_created": bool(getattr(switch_result, "created", False)),
            "users_payload": users_payload,
            "unknown_memory_path": str(unknown_memory_path),
            "admin_memory_path": str(admin_memory_path),
            "standard_memory_path": str(standard_memory_path),
            "unknown_summary_path": str(unknown_summary_path),
            "admin_summary_path": str(admin_summary_path),
            "standard_summary_path": str(standard_summary_path),
            "admin_document_dir": str(admin_document_dir),
            "standard_document_dir": str(standard_document_dir),
            "admin_brain_dir": str(getattr(admin_brain, "data_dir", "")),
            "standard_brain_dir": str(getattr(standard_brain, "data_dir", "")),
            "admin_tasks": admin_tasks,
            "standard_tasks": standard_tasks,
            "standard_root_label": str(standard_root.get("label") or ""),
            "admin_style": admin_style,
            "standard_style": standard_style,
            "identity_checks": {
                "startup_is_unknown": startup_profile.user_id == "unknown",
                "unknown_profile_exists": bool(runtime.registry.profile_for_id("unknown")),
                "unknown_not_persisted_in_registry": "unknown" not in (users_payload.get("users") or {}),
                "relation_phrase_stays_unknown": relation_phrase_result is None and profile_after_relation_phrase.user_id == "unknown",
                "relation_phrase_does_not_create_fake_user": fake_profile_after_relation_phrase is None,
                "reserved_voice_status_phrase_stays_unknown": reserved_phrase_result is None and profile_after_reserved_phrase.user_id == "unknown",
                "reserved_voice_status_phrase_not_persisted": reserved_profile is None,
                "mood_phrase_stays_unknown": mood_phrase_result is None and profile_after_mood_phrase.user_id == "unknown",
                "mood_phrase_not_persisted_as_user": mood_profile is None,
                "statement_switches_to_max": bool(getattr(switch_result, "switched", False)) and standard_profile.user_id == "max",
                "max_created": bool(getattr(switch_result, "created", False)),
                "generic_relation_attaches_to_identified_max": generic_relation_result is None and standard_profile.user_id == "max",
                "same_name_partner_profile_created": bool(getattr(ekin_partner_intro, "switched", False)) and ekin_partner_profile.user_id == "ekin_partner",
                "same_name_friend_profile_created": bool(getattr(ekin_friend_intro, "switched", False)) and ekin_friend_profile.user_id == "ekin_friend",
                "same_name_profiles_keep_shared_display_name": ekin_partner_profile.name == "Ekin" and ekin_friend_profile.name == "Ekin",
                "same_name_admin_world_has_distinct_relations": {
                    (str(edge.get("target") or ""), str(edge.get("relation") or ""))
                    for edge in same_name_edges
                } >= {("person:ekin_partner", "partner"), ("person:ekin_friend", "friend")},
                "plain_same_name_statement_requires_clarification": bool(getattr(ambiguous_ekin, "requires_identity_clarification", False)),
                "plain_same_name_statement_keeps_unknown_active": active_after_ambiguous_ekin == "unknown",
                "manual_same_name_switch_requires_clarification": bool(getattr(manual_ekin_switch, "requires_identity_clarification", False)),
                "manual_same_name_switch_keeps_unknown_active": active_after_manual_ekin_switch == "unknown",
                "relation_hint_resolves_same_name_identity": bool(getattr(resolved_friend_ekin, "switched", False)) and active_after_resolved_friend_ekin == "ekin_friend",
                "spelled_ekin_correction_keeps_same_profile": bool(getattr(spelling_akin_intro, "switched", False))
                and spelling_akin_profile.user_id == spelling_corrected_profile.user_id,
                "spelled_ekin_displays_corrected_name": bool(getattr(spelling_correction, "switched", False) or getattr(spelling_correction, "status", "") == "noop")
                and spelling_corrected_profile.name == "Ekin",
                "spelled_ekin_stores_akin_spoken_alias": "Akin" in set(spelling_corrected_profile.spoken_aliases),
                "spoken_akin_matches_corrected_ekin": any(
                    profile.user_id == spelling_corrected_profile.user_id and profile.name == "Ekin"
                    for profile in spelling_alias_matches
                ),
                "spelled_ekin_does_not_create_plain_duplicate": spelling_duplicate_ekin_profile is None,
                "generic_his_girlfriend_attaches_partner_relation": spelling_relation is None and bool(spelling_partner_relation),
                "public_baris_privacy_guidance_present": "private memory is not surfaced in this public session" in public_active_user_block.lower(),
                "admin_mirror_has_person_node": str(mirrored_max.get("label") or "").lower() == "max",
                "admin_mirror_copies_stable_fact": "coffee" in [item.lower() for item in mirrored_drinks],
                "admin_mirror_friend_relation": bool(mirrored_friend),
                "restart_returns_to_unknown": restart_active_profile.user_id == "unknown",
                "typed_baris_statement_requires_password": bool(getattr(restart_admin_statement, "requires_password", False)),
                "bare_baris_statement_requires_password": bool(getattr(restart_bare_admin_statement, "requires_password", False)),
                "bare_baris_statement_keeps_unknown_active": restart_active_after_bare == "unknown",
                "corrective_baris_statement_requires_password": bool(getattr(restart_corrective_admin_statement, "requires_password", False)),
                "corrective_baris_statement_keeps_unknown_active": restart_active_after_corrective == "unknown",
                "restart_clears_unknown_memory": not restart_unknown_memory_path.exists(),
                "restart_clears_unknown_summary": not restart_unknown_summary_path.exists(),
                "voice_match_baris_unlocks_admin": bool(getattr(voice_result, "switched", False))
                and voice_active_profile.user_id == "admin_baris"
                and bool(voice_admin_unlocked),
            },
            "auth_checks": {
                "set_password": bool(set_password.success),
                "password_configured": bool(runtime.admin_password_configured()),
                "prompt_requires_password": bool(password_prompt.requires_password),
                "active_stays_public_during_prompt": active_after_prompt == standard_profile.user_id,
                "wrong_password_rejected": wrong_password.status == "password_failed",
                "active_stays_public_after_wrong_password": active_after_wrong_password == standard_profile.user_id,
                "correct_password_switches": correct_password.switched and active_after_correct_password == "admin_baris",
                "admin_style_restored_after_unlock": style_after_correct_password == "secretary.style",
                "restart_relocks_admin_to_unknown": restart_active_profile.user_id == "unknown",
            },
            "restart_public_user_id": restart_active_profile.user_id,
        }


def _command_checks() -> dict[str, bool]:
    with tempfile.TemporaryDirectory(prefix="piper-user-runtime-style-") as raw_tmp:
        styles_dir = Path(raw_tmp) / "styles"
        style_mgr = StyleManager(styles_dir, active_filename="jarvis.style")

        return {
            "list_users": handle_command("/users", style_mgr=style_mgr).action == "list_users",
            "show_active_user": handle_command("/user", style_mgr=style_mgr).action == "show_active_user",
            "show_active_user_alias": handle_command("/whoami", style_mgr=style_mgr).action == "show_active_user",
            "switch_user": (
                handle_command("/user Alice Example", style_mgr=style_mgr).action == "switch_user"
                and handle_command("/user Alice Example", style_mgr=style_mgr).user_query == "Alice Example"
            ),
            "set_admin_password": (
                handle_command("/adminpass swordfish", style_mgr=style_mgr).action == "set_admin_password"
                and handle_command("/adminpass swordfish", style_mgr=style_mgr).password_value == "swordfish"
            ),
        }


def _harness_checks() -> dict[str, bool]:
    print("[harness] Starting PiperHarness integration checks (this may take a while)...", flush=True)
    harness = PiperHarness(isolated_data=True, keep_data_copy=False)
    try:
        duplicate_name = "Talin"
        startup_profile = harness.user_runtime.active_profile()
        profiles_before_ambiguous = {profile.user_id for profile in harness.user_runtime.list_profiles()}
        startup_list_handled = harness._handle_command("/users")
        startup_messages = [
            str(message.get("content") or "")
            for message in harness.chat_state.get_messages_snapshot()
            if str(message.get("role") or "").lower() == "system"
        ]
        print("[harness] Sending ambiguous relation turn...", flush=True)
        ambiguous_turn = harness.send_text("i am his friend, do you know baris?")
        profile_after_ambiguous_turn = harness.user_runtime.active_profile()
        profiles_after_ambiguous = {profile.user_id for profile in harness.user_runtime.list_profiles()}
        print("[harness] Sending intro turn...", flush=True)
        intro_turn = harness.send_text("I'm Max")
        active_profile = harness.user_runtime.active_profile()
        intro_memory_path = str(harness.chat_state.memory_path)
        max_memory_path = str(harness.user_runtime.current_memory_path())
        print("[harness] Sending relation turn...", flush=True)
        relation_turn = harness.send_text("i am his friend, do you know baris?")
        profile_after_relation_turn = harness.user_runtime.active_profile()
        switch_messages = [
            str(message.get("content") or "")
            for message in harness.chat_state.get_messages_snapshot()
            if str(message.get("role") or "").lower() == "system"
        ]
        list_handled = harness._handle_command("/users")
        whoami_handled = harness._handle_command("/whoami")
        all_messages = [
            str(message.get("content") or "")
            for message in harness.chat_state.get_messages_snapshot()
            if str(message.get("role") or "").lower() == "system"
        ]
        harness.user_runtime.switch_active_user("admin_baris")
        harness.user_runtime.set_active_style_filename("secretary.style")
        harness.user_runtime.set_admin_password("rosebud")
        harness._handle_command("/user Max")
        print("[harness] Sending password prompt turn...", flush=True)
        prompt_turn = harness.send_text("I'm Baris")
        print("[harness] Sending wrong password turn...", flush=True)
        wrong_turn = harness.send_text("wrong")
        print("[harness] Sending correct password turn...", flush=True)
        correct_turn = harness.send_text("rosebud")
        active_after_correct_password = harness.user_runtime.active_profile().user_id
        style_after_correct_password = harness.style_mgr.active_filename
        harness.user_runtime.switch_active_user("unknown")
        harness.chat_state.bind_memory_path(harness.user_runtime.current_memory_path())
        harness.chat_state.begin_fresh_session(wipe_persistent=False)
        print("[harness] Sending bare prompt turn...", flush=True)
        bare_prompt_turn = harness.send_text("Baris")
        harness.send_text("/cancel")
        print("[harness] Sending corrective prompt turn...", flush=True)
        corrective_prompt_turn = harness.send_text("no i mean its me baris")
        harness.send_text("/cancel")
        harness.user_runtime.switch_active_user("unknown")
        harness.chat_state.bind_memory_path(harness.user_runtime.current_memory_path())
        harness.chat_state.begin_fresh_session(wipe_persistent=False)
        print("[harness] Sending duplicate-name partner turn...", flush=True)
        harness.send_text(f"I'm {duplicate_name}, Baris's partner")
        harness.user_runtime.switch_active_user("unknown")
        harness.chat_state.bind_memory_path(harness.user_runtime.current_memory_path())
        harness.chat_state.begin_fresh_session(wipe_persistent=False)
        print("[harness] Sending duplicate-name friend turn...", flush=True)
        harness.send_text(f"I'm {duplicate_name}, Baris's friend")
        harness.user_runtime.switch_active_user("unknown")
        harness.chat_state.bind_memory_path(harness.user_runtime.current_memory_path())
        harness.chat_state.begin_fresh_session(wipe_persistent=False)
        print("[harness] Sending ambiguous duplicate-name turn...", flush=True)
        duplicate_name_turn = harness.send_text(f"I'm {duplicate_name}")
        duplicate_name_active_profile = harness.user_runtime.active_profile()
        duplicate_name_command_handled = harness._handle_command(f"/user {duplicate_name}")
        duplicate_name_messages = [
            str(message.get("content") or "")
            for message in harness.chat_state.get_messages_snapshot()
            if str(message.get("role") or "").lower() == "system"
        ]
        snapshot = harness.chat_state.get_messages_snapshot()
        admin_graph = harness.user_runtime.knowledge_manager_for("admin_baris").load_graph()
        friend_relation = any(
            str(edge.get("source") or "") == "person:user"
            and str(edge.get("target") or "") == "person:max"
            and str(edge.get("relation") or "") == "friend"
            for edge in (admin_graph.get("edges") or [])
            if isinstance(edge, dict)
        )
        print("[harness] Integration checks complete.", flush=True)
        return {
            "startup_is_unknown": startup_profile.user_id == "unknown",
            "unknown_hidden_from_visible_profiles": "unknown" not in profiles_before_ambiguous,
            "ambiguous_relation_stays_unknown": profile_after_ambiguous_turn.user_id == "unknown",
            "ambiguous_relation_does_not_change_profile_set": profiles_after_ambiguous == profiles_before_ambiguous,
            "intro_auto_identified_max": active_profile.user_id == "max",
            "generic_relation_keeps_max_active": profile_after_relation_turn.user_id == "max",
            "startup_list_handled": bool(startup_list_handled),
            "startup_users_message_mentions_unknown_as_state": any(
                "current speaker: unknown" in message.lower() and "not a saved profile" in message.lower()
                for message in startup_messages
            ),
            "list_handled": bool(list_handled),
            "whoami_handled": bool(whoami_handled),
            "active_user_is_standard": (not active_profile.is_admin and not active_profile.is_unknown),
            "memory_path_rebound": str(harness.chat_state.memory_path) == str(harness.user_runtime.current_memory_path()),
            "ambiguous_turn_recorded_without_switch": any(
                str(message.get("role") or "") == "user" and "his friend" in str(message.get("content") or "").lower()
                for message in ambiguous_turn.messages
            ),
            "intro_turn_memory_rebound_to_max": intro_memory_path == max_memory_path,
            "generic_relation_turn_recorded_as_user": any(
                str(message.get("role") or "") == "user" and "his friend" in str(message.get("content") or "").lower()
                for message in relation_turn.messages
            ),
            "list_message_rendered": any(message.startswith("[UI] Users:") for message in all_messages),
            "whoami_message_rendered": any(message.startswith("[UI] Active user:") for message in all_messages),
            "password_prompt_rendered": any("password required" in message.lower() for message in prompt_turn.system_messages),
            "bare_name_password_prompt_rendered": any("password required" in message.lower() for message in bare_prompt_turn.system_messages),
            "corrective_identity_password_prompt_rendered": any("password required" in message.lower() for message in corrective_prompt_turn.system_messages),
            "wrong_password_rejected": any("incorrect admin password" in message.lower() for message in wrong_turn.system_messages),
            "correct_password_switches": active_after_correct_password == "admin_baris",
            "admin_style_restored": style_after_correct_password == "secretary.style",
            "memory_path_rebound_after_unlock": str(harness.chat_state.memory_path) == str(harness.user_runtime.current_memory_path()),
            "friend_relation_mirrored": bool(friend_relation),
            "duplicate_same_name_turn_prompts_clarification": any(
                f"more than one person named {duplicate_name.lower()}" in message.lower()
                for message in duplicate_name_turn.system_messages
            ),
            "duplicate_same_name_turn_keeps_unknown_active": duplicate_name_active_profile.user_id == "unknown",
            "duplicate_same_name_turn_not_persisted_as_user": not any(
                str(message.get("role") or "") == "user" and str(message.get("content") or "").strip() == f"I'm {duplicate_name}"
                for message in duplicate_name_turn.messages
            ),
            "duplicate_same_name_user_command_prompts_clarification": bool(duplicate_name_command_handled) and any(
                f"more than one person named {duplicate_name.lower()}" in message.lower()
                for message in duplicate_name_messages
            ),
            "password_not_persisted_to_chat": not any(
                str(message.get("role") or "") == "user"
                and str(message.get("content") or "") in {"I'm Baris", "Baris", "no i mean its me baris", "wrong", "rosebud"}
                for message in snapshot
            ),
        }
    finally:
        harness.close()


def run_smoke(include_harness: bool = False) -> UserRuntimeSmokeReport:
    runtime = _runtime_checks()
    command_checks = _command_checks()
    identity_checks = runtime["identity_checks"]
    auth_checks = runtime["auth_checks"]

    if include_harness:
        harness_checks = _harness_checks()
    else:
        harness_checks = {}

    startup_profile = runtime["startup_profile"]
    admin_profile = runtime["admin_profile"]
    unknown_profile = runtime["unknown_profile"]
    standard_profile = runtime["standard_profile"]
    users_payload = runtime["users_payload"]

    def _path_endswith(path: str, suffix: str) -> bool:
        return Path(path).as_posix().endswith(suffix)

    base_success_checks = [
        str(startup_profile.user_id) == "unknown",
        str(admin_profile.user_id) == "admin_baris",
        str(getattr(unknown_profile, "user_id", "")) == "unknown",
        bool(getattr(admin_profile, "is_admin", False)),
        bool(runtime["switch_created"]),
        str(standard_profile.user_id) == "max",
        users_payload.get("active_user_id") == "unknown",
        "admin_baris" in (users_payload.get("users") or {}),
        "unknown" not in (users_payload.get("users") or {}),
        _path_endswith(runtime["unknown_memory_path"], "runtime/unknown/state/memory.jsonl"),
        _path_endswith(runtime["admin_memory_path"], "state/memory.jsonl"),
        _path_endswith(runtime["standard_memory_path"], "users/max/state/memory.jsonl"),
        _path_endswith(runtime["unknown_summary_path"], "runtime/unknown/conversation_summary.json"),
        _path_endswith(runtime["admin_summary_path"], "conversation_summary.json"),
        _path_endswith(runtime["standard_summary_path"], "users/max/conversation_summary.json"),
        runtime["admin_document_dir"] != runtime["standard_document_dir"],
        _path_endswith(runtime["admin_document_dir"], "/data"),
        _path_endswith(runtime["standard_document_dir"], "/data/users/max"),
        runtime["admin_brain_dir"] != runtime["standard_brain_dir"],
        runtime["admin_tasks"] == ["admin_task"],
        runtime["standard_tasks"] == ["max_task"],
        runtime["standard_root_label"] == "Max",
        runtime["admin_style"] == "secretary.style",
        runtime["standard_style"] == "grey.style",
        all(command_checks.values()),
        all(identity_checks.values()),
        all(auth_checks.values()),
    ]

    if include_harness:
        base_success_checks.append(all(harness_checks.values()))

    success = all(base_success_checks)

    return UserRuntimeSmokeReport(
        success=bool(success),
        admin_user_id=str(admin_profile.user_id),
        unknown_user_id=str(getattr(unknown_profile, "user_id", "")),
        standard_user_id=str(standard_profile.user_id),
        startup_active_user_id=str(startup_profile.user_id),
        unknown_memory_path=str(runtime["unknown_memory_path"]),
        admin_memory_path=str(runtime["admin_memory_path"]),
        standard_memory_path=str(runtime["standard_memory_path"]),
        unknown_summary_path=str(runtime["unknown_summary_path"]),
        admin_summary_path=str(runtime["admin_summary_path"]),
        standard_summary_path=str(runtime["standard_summary_path"]),
        admin_document_dir=str(runtime["admin_document_dir"]),
        standard_document_dir=str(runtime["standard_document_dir"]),
        admin_tasks=list(runtime["admin_tasks"]),
        standard_tasks=list(runtime["standard_tasks"]),
        standard_root_label=str(runtime["standard_root_label"]),
        admin_style=str(runtime["admin_style"]),
        standard_style=str(runtime["standard_style"]),
        restart_public_user_id=str(runtime["restart_public_user_id"]),
        command_checks=command_checks,
        identity_checks=identity_checks,
        auth_checks=auth_checks,
        harness_checks=harness_checks,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify admin and standard-user silo wiring for multi-user Piper.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    parser.add_argument(
        "--include-harness",
        action="store_true",
        help="Also run full PiperHarness LLM integration checks (slower, may hang if server is unavailable).",
    )
    return parser


def main() -> int:
    _configure_stdio()
    args = build_parser().parse_args()
    report = run_smoke(include_harness=args.include_harness)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"ADMIN: {report.admin_user_id}")
        print(f"UNKNOWN: {report.unknown_user_id}")
        print(f"STANDARD: {report.standard_user_id}")
        print(f"ADMIN_TASKS: {report.admin_tasks}")
        print(f"STANDARD_TASKS: {report.standard_tasks}")
        print(f"STANDARD_ROOT_LABEL: {report.standard_root_label}")
        print(f"COMMAND_CHECKS: {report.command_checks}")
        print(f"IDENTITY_CHECKS: {report.identity_checks}")
        print(f"AUTH_CHECKS: {report.auth_checks}")
        print(f"HARNESS_CHECKS: {report.harness_checks}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
