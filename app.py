from __future__ import annotations

import atexit
import os
import queue
import sys
from pathlib import Path

from config import CFG
from core.agent import AgentBrain
from core.codex_bridge import probe_codex_support
from core.environment_service import EnvironmentService
from core.instructions_loader import InstructionLoader
from core.operational_state_service import OperationalStateService
from core.prompt_context import PromptContextService
from core.style import StyleManager
from llm.boot import BootManager
from llm.llm_server_client import LlamaServerClient, LlamaServerConfig
from memory.chat_state import ChatState
from memory.user_runtime import (
    ActiveUserBrainProxy,
    ActiveUserDocumentMemoryProxy,
    ActiveUserKnowledgeManagerProxy,
    ActiveUserRuntime,
    ActiveUserStateOwnerProxy,
    ActiveUserTransientStateManagerProxy,
)
from memory.vision_session import VisionSessionMemory
from tools.image_gen import ImageGenerator
from tools.live_screen import LiveScreenSession
from tools.tts import TTSConfig, get_tts
from ui.controller import PiperController, RESTART_EXIT_CODE


APP_TITLE = "Piper Core - Agent Mode"
W, H = 1450, 860


def build_controller() -> PiperController:
    ui_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
    styles_dir = CFG.STYLES_DIR
    style_mgr = StyleManager(
        styles_dir,
        active_filename=str(getattr(CFG, "ACTIVE_STYLE_FILE", "default.style")),
    )

    root_dir = Path(getattr(CFG, "ROOT_DIR", Path(__file__).resolve().parent))
    kokoro_dir = Path(getattr(CFG, "KOKORO_DIR", root_dir / "models" / "kokoro"))

    tts = get_tts(
        TTSConfig(
            enabled=getattr(CFG, "TTS_ENABLED", True),
            model_path=kokoro_dir / getattr(CFG, "KOKORO_MODEL", "kokoro-v1.0.onnx"),
            voices_path=kokoro_dir / getattr(CFG, "KOKORO_VOICES", "voices-v1.0.bin"),
            backend=str(getattr(CFG, "TTS_BACKEND", "auto")),
            voice=getattr(CFG, "TTS_VOICE", "af_heart"),
            speed=float(getattr(CFG, "TTS_SPEED", 0.85)),
        )
    )

    llm = LlamaServerClient(
        LlamaServerConfig(
            base_url=str(getattr(CFG, "LLAMA_SERVER_URL", "http://127.0.0.1:8080")),
            model=str(getattr(CFG, "LLAMA_SERVER_MODEL", "qwen")),
            temperature=float(getattr(CFG, "TEMPERATURE", 0.7)),
            max_tokens=int(getattr(CFG, "MAX_TOKENS", 512)),
            timeout_s=float(getattr(CFG, "LLAMA_SERVER_TIMEOUT_S", 300.0)),
            stream_read_timeout_s=float(getattr(CFG, "LLAMA_SERVER_STREAM_READ_TIMEOUT_S", 30.0)),
            debug_path=CFG.LLM_HTTP_PAYLOAD_DEBUG_PATH if CFG.DEBUG_LLM_HTTP_PAYLOADS else None,
        )
    )

    user_runtime = ActiveUserRuntime(
        CFG.DATA_DIR,
        llm,
        admin_user_id="admin_baris",
        admin_name="Baris",
        default_style_filename=style_mgr.active_filename,
    )
    active_user_style = user_runtime.current_style_filename()
    if active_user_style:
        style_mgr.active_filename = active_user_style
    chat_state = ChatState(memory_path=user_runtime.current_memory_path(), session_marker_prefix="=== New session")

    state_owner = ActiveUserStateOwnerProxy(user_runtime)
    memory_brain = ActiveUserBrainProxy(user_runtime)
    knowledge_mgr = ActiveUserKnowledgeManagerProxy(user_runtime)
    transient_state_mgr = ActiveUserTransientStateManagerProxy(user_runtime)
    document_mgr = ActiveUserDocumentMemoryProxy(user_runtime)
    vision_session_memory = VisionSessionMemory()
    agent_brain = AgentBrain(
        CFG.DATA_DIR,
        workspace_root=CFG.WORKSPACE_DIR,
        state_owner=state_owner,
        knowledge_manager=knowledge_mgr,
        transient_state_manager=transient_state_mgr,
        memory_brain=memory_brain,
    )
    prompt_context_service = PromptContextService(
        instruction_loader=InstructionLoader(CFG.INSTRUCTIONS_PATH),
        environment_service=EnvironmentService(state_owner),
        operational_state_service=OperationalStateService(state_owner),
        knowledge_mgr=knowledge_mgr,
        transient_state_mgr=transient_state_mgr,
        brain=memory_brain,
        document_memory=document_mgr,
        vision_session_memory=vision_session_memory,
        user_runtime=user_runtime,
    )
    live_screen = LiveScreenSession(CFG.DATA_DIR)

    def _probe_engineering_channel() -> str:
        _, message = probe_codex_support(timeout_s=CFG.CODEX_BOOT_PROBE_TIMEOUT_S)
        ui_queue.put(("status_widget_dashboard_activity", message))
        return message

    boot_mgr = BootManager(
        ui_queue,
        background_boot_tasks=[
            ("Warming TTS engine...", tts.warm_up),
            ("Checking engineering channel...", _probe_engineering_channel),
        ]
        if CFG.CODEX_BOOT_PROBE_ENABLED
        else [("Warming TTS engine...", tts.warm_up)],
    )
    img_gen = ImageGenerator(CFG.DATA_DIR)

    atexit.register(boot_mgr.shutdown)
    atexit.register(live_screen.stop)
    atexit.register(agent_brain.shutdown)

    return PiperController(
        app_title=APP_TITLE,
        width=W,
        height=H,
        ui_queue=ui_queue,
        chat_state=chat_state,
        style_mgr=style_mgr,
        tts=tts,
        llm=llm,
        knowledge_mgr=knowledge_mgr,
        document_mgr=document_mgr,
        agent_brain=agent_brain,
        prompt_context_service=prompt_context_service,
        user_runtime=user_runtime,
        boot_mgr=boot_mgr,
        img_gen=img_gen,
        live_screen=live_screen,
        vision_session_memory=vision_session_memory,
    )


def main() -> int:
    controller = build_controller()
    exit_code = controller.run()
    if exit_code == RESTART_EXIT_CODE and os.environ.get("PIPER_LAUNCHER") != "batch":
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
