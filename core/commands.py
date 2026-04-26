"""Text command handling for Piper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import List, Optional, Tuple

from core.style import StyleManager


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    action: Optional[str] = None
    ui_message: Optional[str] = None
    style_filename: Optional[str] = None
    password_value: Optional[str] = None
    user_query: Optional[str] = None
    document_path: Optional[str] = None
    vision_path: Optional[str] = None
    vision_prompt: Optional[str] = None
    support_note: Optional[str] = None
    graph_action: Optional[str] = None


_VISION_CMD_RE = re.compile(
    r"""^/vision(?:\s+(?P<path>"[^"]+"|'[^']+'|\S+))?(?:\s+(?P<question>.+))?$""",
    re.IGNORECASE,
)


def _parse_vision_command(text: str) -> Tuple[str, str]:
    match = _VISION_CMD_RE.match((text or "").strip())
    if not match:
        return "", ""

    raw_path = str(match.group("path") or "").strip().strip('"').strip("'")
    question = str(match.group("question") or "").strip()
    if not question:
        question = "Describe this image briefly."
    return raw_path, question


def available_style_files(style_mgr: StyleManager) -> List[str]:
    try:
        d = style_mgr.styles_dir
        if not d.exists():
            return []
        return sorted([p.name for p in d.glob("*.style") if p.is_file()])
    except Exception:
        return []


def normalize_style_filename(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    if not n.lower().endswith(".style"):
        n += ".style"
    return n


def set_active_style(style_mgr: StyleManager, style_name: str) -> Tuple[bool, str]:
    filename = normalize_style_filename(style_name)
    if not filename:
        return False, ""

    p: Path = style_mgr.styles_dir / filename
    if not p.exists() or not p.is_file():
        return False, filename

    style_mgr.active_filename = filename
    style_mgr.save_preference()
    return True, filename


def handle_command(user_text: str, *, style_mgr: StyleManager) -> CommandResult:
    txt = (user_text or "").strip()
    if not txt:
        return CommandResult(False)

    low = txt.lower()

    if low in ("/clear", "clear"):
        return CommandResult(True, action="clear")

    if low in ("/new", "new"):
        return CommandResult(True, action="new_session")

    if low == "/users":
        return CommandResult(True, action="list_users")

    if low in ("/user", "/whoami"):
        return CommandResult(True, action="show_active_user")

    if low.startswith("/user "):
        target = txt.split(maxsplit=1)[1].strip()
        if not target:
            return CommandResult(True, ui_message="[UI] Usage: /user <name-or-id>")
        return CommandResult(True, action="switch_user", user_query=target)

    if low == "/adminpass":
        return CommandResult(True, ui_message="[UI] Usage: /adminpass <password>")

    if low.startswith("/adminpass "):
        password = txt.split(maxsplit=1)[1]
        if not str(password).strip():
            return CommandResult(True, ui_message="[UI] Usage: /adminpass <password>")
        return CommandResult(True, action="set_admin_password", password_value=password)

    if low == "/styles":
        opts = available_style_files(style_mgr)
        msg = "Styles: " + (", ".join(opts) if opts else "(none found)")
        return CommandResult(True, ui_message="[UI] " + msg)

    if low.startswith("/style"):
        parts = txt.split(maxsplit=1)
        if len(parts) < 2:
            opts = available_style_files(style_mgr)
            suffix = f" Available: {', '.join(opts)}" if opts else ""
            return CommandResult(True, ui_message=f"[UI] Usage: /style <name>.{suffix}")

        target = parts[1].strip()
        ok, fname = set_active_style(style_mgr, target)
        if ok:
            return CommandResult(
                True,
                action="new_session",
                ui_message=f"[UI] Style changed to {fname}. Session cleared.",
                style_filename=fname,
            )
        opts = available_style_files(style_mgr)
        suffix = f" Available: {', '.join(opts)}" if opts else ""
        return CommandResult(True, ui_message=f"[UI] Style not found: {target}.{suffix}")

    if low == "/ingest":
        return CommandResult(True, ui_message="[UI] Usage: /ingest <path-to-document>")

    if low.startswith("/ingest "):
        parts = txt.split(maxsplit=1)
        path = parts[1].strip().strip('"').strip("'") if len(parts) > 1 else ""
        if not path:
            return CommandResult(True, ui_message="[UI] Usage: /ingest <path-to-document>")
        return CommandResult(True, action="ingest_document", document_path=path)

    if low == "/vision":
        return CommandResult(True, ui_message='[UI] Usage: /vision "<image-path>" <question>')

    if low.startswith("/vision "):
        path, question = _parse_vision_command(txt)
        if not path:
            return CommandResult(True, ui_message='[UI] Usage: /vision "<image-path>" <question>')
        return CommandResult(
            True,
            action="vision_query",
            vision_path=path,
            vision_prompt=question,
        )

    if low in {"/graph", "/langgraph"}:
        return CommandResult(True, action="langgraph_recovery", graph_action="status")

    if low.startswith("/graph ") or low.startswith("/langgraph "):
        parts = txt.split(maxsplit=1)
        subcommand = parts[1].strip().lower() if len(parts) > 1 else ""
        if subcommand in {"status", "show", "inspect"}:
            return CommandResult(True, action="langgraph_recovery", graph_action="status")
        if subcommand in {"resume", "recover", "continue"}:
            return CommandResult(True, action="langgraph_recovery", graph_action="resume")
        if subcommand in {"clear", "discard", "forget"}:
            return CommandResult(True, action="langgraph_recovery", graph_action="clear")
        return CommandResult(
            True,
            ui_message="[UI] Usage: /graph status | /graph resume | /graph clear",
        )

    return CommandResult(False)
