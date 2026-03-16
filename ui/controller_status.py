from __future__ import annotations

import re
from typing import Dict

import dearpygui.dearpygui as dpg

from ui.controller_render import append_bounded_line_block

ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
STAGE_STEP_RE = re.compile(
    r"stage\s*(\d+)(?:\s*/\s*(\d+))?\s*(?:[-|]|:)\s*step\s*(\d+)(?:\s*/\s*(\d+))?",
    re.IGNORECASE,
)
MODE_COLOR_MAP: Dict[str, tuple[int, int, int, int]] = {
    "IDLE": (120, 180, 140, 255),
    "CODE SESSION": (150, 210, 255, 255),
    "ROUTING": (100, 175, 240, 255),
    "SEARCHING": (100, 175, 240, 255),
    "ANALYZING": (240, 200, 110, 255),
    "PLANNING": (235, 185, 90, 255),
    "THINKING": (255, 210, 120, 255),
    "GENERATING": (120, 220, 210, 255),
    "SPEAKING": (140, 220, 150, 255),
    "LISTENING": (255, 145, 120, 255),
    "TRANSCRIBING": (245, 190, 110, 255),
    "IMAGE WORK": (135, 205, 255, 255),
    "RESTARTING": (255, 175, 120, 255),
    "STOPPING": (255, 185, 120, 255),
    "CANCELED": (190, 155, 120, 255),
    "ERROR": (255, 110, 110, 255),
}
META_TEXT_COLOR = (120, 130, 150, 255)


def clean_ui_text(text: object) -> str:
    clean = ANSI_ESCAPE_RE.sub("", str(text or ""))
    clean = CONTROL_CHAR_RE.sub(" ", clean)
    clean = clean.replace("\r", " ").replace("\n", " ")
    return " ".join(clean.split()).strip()


def classify_runtime_mode(text: str) -> str:
    upper = text.upper().replace("MODE:", "").strip(" .")
    if not upper:
        return "IDLE"
    if "GENERAT" in upper:
        return "GENERATING"
    if "THINK" in upper:
        return "THINKING"
    if "ROUT" in upper:
        return "ROUTING"
    if "SEARCH" in upper:
        return "SEARCHING"
    if "ANALYZ" in upper:
        return "ANALYZING"
    if "PLAN" in upper:
        return "PLANNING"
    if "SPEAK" in upper:
        return "SPEAKING"
    if "LISTEN" in upper:
        return "LISTENING"
    if "TRANSCRIB" in upper:
        return "TRANSCRIBING"
    if "IMAGE" in upper or "PAUSING LLM" in upper:
        return "IMAGE WORK"
    if "RESTART" in upper:
        return "RESTARTING"
    if "STOPPING" in upper or "STOP REQUEST" in upper:
        return "STOPPING"
    if "CANCEL" in upper or "STOPPED" in upper:
        return "CANCELED"
    if "ERROR" in upper or "FAILED" in upper:
        return "ERROR"
    if upper in {"READY", "IDLE"}:
        return "IDLE"
    return upper


def _effective_runtime_mode(controller) -> str:
    mode_text = clean_ui_text(controller.runtime_mode) or "IDLE"
    if controller.has_active_code_session() and mode_text in {"IDLE", "CANCELED", "CODE SESSION"}:
        return "CODE SESSION"
    return mode_text


def refresh_top_bar(controller) -> None:
    mode_text = _effective_runtime_mode(controller)
    meta_parts = [
        part
        for part in [
            controller.stage_meta,
            getattr(controller, "code_session_meta", ""),
            controller.session_meta,
            controller.style_meta,
            getattr(controller, "screen_meta", ""),
        ]
        if part
    ]
    meta_text = " | ".join(meta_parts)

    if dpg.does_item_exist(controller.tags.status_text):
        dpg.set_value(controller.tags.status_text, mode_text)
        dpg.configure_item(
            controller.tags.status_text,
            color=MODE_COLOR_MAP.get(mode_text, META_TEXT_COLOR),
        )
    if dpg.does_item_exist(controller.tags.mode_indicator):
        dpg.set_value(controller.tags.mode_indicator, meta_text)
        dpg.configure_item(controller.tags.mode_indicator, color=META_TEXT_COLOR)


def set_status(controller, text: str) -> None:
    clean = clean_ui_text(text)
    if not clean:
        return

    stage_match = STAGE_STEP_RE.search(clean)
    if stage_match:
        stage_num = stage_match.group(1)
        total_stages = stage_match.group(2)
        step_num = stage_match.group(3)
        stage_label = f"Stage {stage_num}/{total_stages}" if total_stages else f"Stage {stage_num}"
        controller.stage_meta = f"{stage_label} | Step {step_num}"
        controller.runtime_mode = "THINKING"
        refresh_top_bar(controller)
        return

    controller.runtime_mode = classify_runtime_mode(clean)
    if controller.runtime_mode in {"IDLE", "CANCELED"}:
        controller.stage_meta = ""
    refresh_top_bar(controller)


def set_mode_indicator(controller, text: str) -> None:
    clean = clean_ui_text(text)
    clean = clean.removeprefix("MODE:").strip()
    controller.style_meta = f"Style: {clean}" if clean else "Style: DEFAULT"
    refresh_top_bar(controller)


def set_stage_meta(controller, text: str) -> None:
    controller.stage_meta = clean_ui_text(text)
    refresh_top_bar(controller)


def log_agent_monitor(controller, text: str) -> None:
    print(f"[MONITOR] {text}")
    if dpg.does_item_exist(controller.tags.agent_log_text):
        current = dpg.get_value(controller.tags.agent_log_text)
        dpg.set_value(
            controller.tags.agent_log_text,
            append_bounded_line_block(current, text, max_lines=200),
        )
        controller.refresh_text_view_height(controller.tags.agent_log_text)
        controller.request_autoscroll(controller.tags.agent_log_child)
