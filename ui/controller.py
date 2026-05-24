from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import dearpygui.dearpygui as dpg

from config import CFG
from core.code_session import EmbeddedCodeSession
from core.engines.proactive_monitor import ProactiveMonitor
from core.services.stats_collector import StatsCollector
from core.pipeline import ChatPipeline
from core.runtime_control import CancellationToken
from ui.layout import build_ui
from ui.event_speech import (
    EVENT_SPEECH_OFF,
    event_speech_message,
    event_speech_mode_label,
    normalize_event_speech_mode,
)
from ui.vision_commentary import (
    VISION_COMMENT_SKIP_TOKEN,
    build_vision_comment_prompt,
    recent_user_vision_context,
)
from ui.controller_actions import (
    do_generate_stream as do_generate_stream_action,
    on_clear as on_clear_action,
    on_code_clear as on_code_clear_action,
    on_code_run as on_code_run_action,
    on_code_send as on_code_send_action,
    on_document_picker_cancel as on_document_picker_cancel_action,
    on_document_picker_selected as on_document_picker_selected_action,
    on_open_document_picker as on_open_document_picker_action,
    on_mic_toggle as on_mic_toggle_action,