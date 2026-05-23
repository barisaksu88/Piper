"""Lightweight tests for dev trusted-admin text-input mode.

These tests require no LLM, no web search, and no real controller.
They validate config defaults and ActiveUserRuntime.dev-admin behavior.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config import CFG
from memory.user_runtime import (
    ActiveUserRuntime,
    DEFAULT_ADMIN_USER_ID,
    DEFAULT_ADMIN_NAME,
    DEFAULT_GUEST_USER_ID,
)


# ── Config defaults ──

class TestConfigDefaults:
    def test_dev_trusted_admin_text_input_defaults_false(self) -> None:
        assert getattr(CFG, "DEV_TRUSTED_ADMIN_TEXT_INPUT", None) is False

    def test_dev_trusted_admin_text_require_localhost_defaults_true(self) -> None:
        assert getattr(CFG, "DEV_TRUSTED_ADMIN_TEXT_REQUIRE_LOCALHOST", None) is True


# ── localhost guard (tests the real helper) ──

from core.dev_mode import is_dev_trusted_admin_text_allowed


class TestLocalhostGuard:
    def test_accepts_127_0_0_1(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="127.0.0.1", require_localhost=True
        ) is True

    def test_accepts_localhost(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="localhost", require_localhost=True
        ) is True

    def test_accepts_ipv6_loopback(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="::1", require_localhost=True
        ) is True

    def test_accepts_bracket_ipv6_loopback(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="[::1]", require_localhost=True
        ) is True

    def test_accepts_uppercase_localhost(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="LOCALHOST", require_localhost=True
        ) is True

    def test_accepts_whitespace_localhost(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="  localhost  ", require_localhost=True
        ) is True

    def test_rejects_0_0_0_0(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="0.0.0.0", require_localhost=True
        ) is False

    def test_rejects_external_host(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="192.168.1.50", require_localhost=True
        ) is False
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="example.com", require_localhost=True
        ) is False

    def test_rejects_empty_host_when_web_ui_enabled(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="", require_localhost=True
        ) is False

    def test_dpg_mode_considers_local(self) -> None:
        # When Web UI is disabled (DPG only), any host is treated as local
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=False, web_ui_host="0.0.0.0", require_localhost=True
        ) is True
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=False, web_ui_host="192.168.1.50", require_localhost=True
        ) is True

    def test_require_localhost_false_allows_non_localhost(self) -> None:
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="0.0.0.0", require_localhost=False
        ) is True
        assert is_dev_trusted_admin_text_allowed(
            web_ui_enabled=True, web_ui_host="192.168.1.50", require_localhost=False
        ) is True


# ── activate_dev_admin_override ──

@pytest.fixture
def tmp_runtime(tmp_path: Path) -> ActiveUserRuntime:
    llm = MagicMock()
    return ActiveUserRuntime(
        data_dir=tmp_path,
        llm_client=llm,
        admin_user_id=DEFAULT_ADMIN_USER_ID,
        admin_name=DEFAULT_ADMIN_NAME,
        default_style_filename="default.style",
    )


class TestActivateDevAdminOverride:
    def test_activates_admin_profile(self, tmp_runtime: ActiveUserRuntime) -> None:
        result = tmp_runtime.activate_dev_admin_override(source="text")
        assert result.status == "switched"
        assert result.profile.is_admin is True
        assert result.profile.user_id == DEFAULT_ADMIN_USER_ID
        assert "[DEV] Trusted admin text-input mode activated" in result.message
        assert "Baris" in result.message

    def test_unlocks_admin(self, tmp_runtime: ActiveUserRuntime) -> None:
        tmp_runtime.activate_dev_admin_override(source="text")
        assert tmp_runtime.is_admin_unlocked() is True

    def test_does_not_create_duplicate_admin(self, tmp_runtime: ActiveUserRuntime) -> None:
        result1 = tmp_runtime.activate_dev_admin_override(source="text")
        result2 = tmp_runtime.activate_dev_admin_override(source="text")
        assert result1.profile.user_id == result2.profile.user_id
        # Second call should be a noop switch (same user already active)
        assert result2.status == "switched"

    def test_does_not_weaken_typed_user_switch_password_check(self, tmp_runtime: ActiveUserRuntime) -> None:
        # Activate admin via dev override, set a password, then switch away
        tmp_runtime.activate_dev_admin_override(source="text")
        tmp_runtime.set_admin_password("secret123")
        # Switch to unknown
        tmp_runtime.switch_active_user(DEFAULT_GUEST_USER_ID)
        tmp_runtime._admin_unlocked = False
        # Normal typed switch to Baris should require password
        switch_result = tmp_runtime.request_typed_user_switch("Baris")
        assert switch_result.requires_password is True

    def test_does_not_change_voice_config(self, tmp_runtime: ActiveUserRuntime) -> None:
        from config import CFG

        before_voice_enabled = getattr(CFG, "VOICE_RECOGNITION_ENABLED", None)
        before_sim_high = getattr(CFG, "VOICE_SIMILARITY_THRESHOLD_HIGH", None)
        before_admin_sim = getattr(CFG, "VOICE_ADMIN_SIMILARITY_THRESHOLD", None)

        tmp_runtime.activate_dev_admin_override(source="text")

        assert getattr(CFG, "VOICE_RECOGNITION_ENABLED", None) == before_voice_enabled
        assert getattr(CFG, "VOICE_SIMILARITY_THRESHOLD_HIGH", None) == before_sim_high
        assert getattr(CFG, "VOICE_ADMIN_SIMILARITY_THRESHOLD", None) == before_admin_sim
