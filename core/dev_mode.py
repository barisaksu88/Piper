"""Pure helpers for dev-mode guard logic.

These functions contain no I/O, no registry hooks, and no state mutations.
They are used by startup paths to decide whether local-dev-only shortcuts
should be permitted.
"""

from __future__ import annotations

_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def is_dev_trusted_admin_text_allowed(
    *,
    web_ui_enabled: bool,
    web_ui_host: str,
    require_localhost: bool,
) -> bool:
    """Return whether dev trusted-admin text-input mode may activate.

    Rules:
    - If Web UI is disabled (DPG/local fallback): always allowed.
    - If ``require_localhost`` is False: always allowed.
    - Otherwise: allowed only when ``web_ui_host`` is a known localhost form.
    """
    if not web_ui_enabled:
        return True
    if not require_localhost:
        return True
    normalized = str(web_ui_host or "").strip().lower()
    return normalized in _LOCALHOST_HOSTS
