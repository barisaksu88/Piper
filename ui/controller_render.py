from __future__ import annotations

import re
import textwrap
from typing import Iterable


def clean_display_text(text: str) -> str:
    return (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("—", "--")
        .replace("…", "...")
    )


def normalize_message_display_spacing(text: str) -> str:
    cleaned = clean_display_text(text)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    # Preserve paragraph breaks, but normalize runaway blank-line runs.
    return re.sub(r"\n[ \t]*\n+", "\n\n", cleaned).strip("\n")


def _wrap_display_line(
    text: str,
    *,
    width: int,
    initial_indent: str,
    subsequent_indent: str,
) -> str:
    stripped = str(text or "").strip()
    if not stripped:
        return initial_indent.rstrip()

    bullet_match = re.match(r"^([-*]\s+)(.*)$", stripped)
    if bullet_match:
        marker, rest = bullet_match.groups()
        body = rest.strip() or marker.strip()
        return textwrap.fill(
            body,
            width=max(int(width), 24),
            initial_indent=initial_indent + marker,
            subsequent_indent=subsequent_indent + (" " * len(marker)),
            break_long_words=False,
            break_on_hyphens=False,
        )

    ordered_match = re.match(r"^(\d+\.\s+)(.*)$", stripped)
    if ordered_match:
        marker, rest = ordered_match.groups()
        body = rest.strip() or marker.strip()
        return textwrap.fill(
            body,
            width=max(int(width), 24),
            initial_indent=initial_indent + marker,
            subsequent_indent=subsequent_indent + (" " * len(marker)),
            break_long_words=False,
            break_on_hyphens=False,
        )

    return textwrap.fill(
        stripped,
        width=max(int(width), 24),
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def format_chat_message_block(role: str, content: str, *, wrap_columns: int) -> str:
    prefix = f"{str(role)}: "
    hanging = " " * len(prefix)
    normalized = normalize_message_display_spacing(content)
    if not normalized:
        return prefix.rstrip()

    paragraphs = re.split(r"\n\s*\n", normalized)
    rendered_paragraphs: list[str] = []
    first_paragraph = True

    for paragraph in paragraphs:
        raw_lines = [line for line in paragraph.splitlines() if line.strip()] or [""]
        wrapped_lines: list[str] = []
        line_prefix = prefix if first_paragraph else hanging
        for index, raw_line in enumerate(raw_lines):
            initial_indent = line_prefix if index == 0 else hanging
            wrapped_lines.append(
                _wrap_display_line(
                    raw_line,
                    width=wrap_columns,
                    initial_indent=initial_indent,
                    subsequent_indent=hanging,
                )
            )
        rendered_paragraphs.append("\n".join(wrapped_lines).rstrip())
        first_paragraph = False

    return "\n\n".join(part for part in rendered_paragraphs if part)


def renderable_chat_messages(messages_snapshot: Iterable[dict]) -> list[tuple[str, str]]:
    rendered: list[tuple[str, str]] = []
    for message in messages_snapshot:
        role = message.get("role", "user")
        content = message.get("content") or ""

        if message.get("hidden"):
            continue
        if role == "system":
            content_strip = content.strip()
            if content_strip.startswith("[Saved to file:"):
                continue
            if content_strip.startswith("System retrieved file"):
                continue
            if content_strip.startswith("Tool Response"):
                continue
        if role == "assistant" and not content.strip():
            continue
        # Defensive: exclude messages that are clearly internal backend
        # emissions rather than legitimate assistant replies.  Upstream
        # scrubbing (TagScrubber + pump) is the primary defence; this is
        # a last-line filter for chat.sync and DPG refresh.
        content_strip = content.strip()
        if content_strip.startswith("[ROUTER]"):
            continue
        if content_strip.startswith("[RECALL:"):
            continue
        rendered.append((str(role), normalize_message_display_spacing(content)))
    return rendered


def append_bounded_line_block(current: str, text: str, *, max_lines: int) -> str:
    lines = current.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    lines.append(text)
    return "\n".join(lines)
