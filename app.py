from __future__ import annotations

import logging
import os
import warnings

from config import CFG

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

logging.basicConfig(
    level=getattr(logging, CFG.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

warnings.filterwarnings(
    "ignore",
    message=r".*unauthenticated requests to the HF Hub.*",
)

for logger_name, level in (
    ("httpx", logging.WARNING),
    ("httpcore", logging.WARNING),
    ("huggingface_hub", logging.ERROR),
    ("huggingface_hub.utils._http", logging.ERROR),
    ("sentence_transformers", logging.WARNING),
    ("transformers", logging.WARNING),
):
    logging.getLogger(logger_name).setLevel(level)

import atexit
import queue
import sys
import time
from pathlib import Path

_MAX_RESTARTS = 3
_RESTART_WINDOW_S = 60

def _should_restart() -> bool:
    """Return True if restart count is under the cap, using env vars for persistence."""
    now = time.time()
    count_str = os.environ.get("PIPER_RESTART_COUNT", "0")
    first_str = os.environ.get("PIPER_RESTART_FIRST_TIME", "")
    try:
        count = int(count_str)
    except ValueError:
        count = 0
    first = float(first_str) if first_str else now
    elapsed = now - first
    if elapsed > _RESTART_WINDOW_S:
        count = 0
        first = now
    count += 1
    os.environ["PIPER_RESTART_COUNT"] = str(count)
    os.environ["PIPER_RESTART_FIRST_TIME"] = str(first)
    return count <= _MAX_RESTARTS

from core.agent import AgentBrain
from core.environment_service import EnvironmentService
from core.instructions_loader import InstructionLoader
from core.operational_state_service import OperationalStateService
from core.prompt_context import PromptContextService
from core.search.searxng_service import SearXNGService
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

    boot_mgr = BootManager(
        ui_queue,
        background_boot_tasks=[
            ("Warming TTS engine...", tts.warm_up),
        ]
    )
    img_gen = ImageGenerator(CFG.DATA_DIR)

    searxng_service = SearXNGService()
    searxng_service.ensure_available()
    atexit.register(searxng_service.shutdown)

    atexit.register(boot_mgr.shutdown)
    atexit.register(live_screen.stop)
    atexit.register(agent_brain.shutdown)

    return PiperController(
        searxng_service=searxng_service,
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
    web_ui_enabled = getattr(CFG, "WEB_UI_ENABLED", True)
    use_window = getattr(CFG, "WEB_UI_WINDOW", True)

    if web_ui_enabled:
        # Quiet websockets per-connection INFO logs in Web UI mode
        # (HTTP asset serving and open/close messages are not useful at INFO).
        logging.getLogger("websockets.server").setLevel(logging.WARNING)

        dist_dir = Path(getattr(CFG, "WEB_UI_FRONTEND_DIST_DIR", ""))
        src_dir = Path(__file__).resolve().parent / "web_ui" / "frontend" / "src"
        if not dist_dir.is_dir() or not (dist_dir / "index.html").is_file():
            logging.warning(
                "Web UI frontend dist not found at %s. "
                "Run: cd web_ui/frontend && npm run build",
                dist_dir,
            )
        elif src_dir.is_dir():
            # Auto-rebuild frontend if source files are newer than dist
            try:
                dist_mtime = max(
                    (f.stat().st_mtime for f in dist_dir.rglob("*") if f.is_file()),
                    default=0,
                )
                src_mtime = max(
                    (f.stat().st_mtime for f in src_dir.rglob("*") if f.is_file()),
                    default=0,
                )
                if src_mtime > dist_mtime:
                    import shutil
                    import subprocess

                    npm_cmd = shutil.which("npm")
                    if npm_cmd:
                        logging.info("Frontend source changed — rebuilding...")
                        result = subprocess.run(
                            [npm_cmd, "run", "build"],
                            cwd=str(src_dir.parent),
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )
                        if result.returncode == 0:
                            logging.info("Frontend rebuilt successfully.")
                        else:
                            logging.warning(
                                "Frontend rebuild failed: %s",
                                result.stderr.strip()[-200:] if result.stderr else "unknown error",
                            )
                    else:
                        logging.warning(
                            "npm not found in PATH. Frontend may be stale. "
                            "Run: cd web_ui/frontend && npm run build"
                        )
            except Exception as exc:
                logging.debug("Frontend auto-build check failed: %s", exc)

        if use_window:
            try:
                import webview  # type: ignore[import-untyped]  # noqa: F401
            except ImportError:
                logging.warning(
                    "pywebview not installed; falling back to browser mode. "
                    "Open http://%s:%s/ manually, or install pywebview for desktop window.",
                    getattr(CFG, "WEB_UI_HOST", "127.0.0.1"),
                    getattr(CFG, "WEB_UI_PORT", 8787),
                )
                use_window = False

        exit_code = controller.run_web(
            host=getattr(CFG, "WEB_UI_HOST", "127.0.0.1"),
            port=getattr(CFG, "WEB_UI_PORT", 8787),
            ws_path=getattr(CFG, "WEB_UI_WS_PATH", "/ws"),
            use_window=use_window,
        )
    else:
        exit_code = controller.run()
    if exit_code == RESTART_EXIT_CODE and os.environ.get("PIPER_LAUNCHER") != "batch":
        if _should_restart():
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])
        else:
            logging.fatal(
                "Restart loop detected: exceeded %s restarts within %s seconds. Exiting.",
                _MAX_RESTARTS, _RESTART_WINDOW_S,
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
