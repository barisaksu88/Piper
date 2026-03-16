from __future__ import annotations

import queue
import shutil
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import memory.brain as brain_module
from config import CFG, data_debug_path
from core.agent import AgentBrain
from core.code_session import EmbeddedCodeSession
from core.commands import handle_command
from core.engineering_support import build_manual_codex_snapshot
from core.environment_service import EnvironmentService
from core.instructions_loader import InstructionLoader
from core.operational_state_service import OperationalStateService
from core.orchestrator import run_agent_loop
from core.pipeline import ChatPipeline
from core.prompt_context import PromptContextService
from core.style import StyleManager
from llm.boot import BootManager
from llm.llm_server_client import LlamaServerClient, LlamaServerConfig
from memory.brain import get_brain
from memory.chat_state import ChatState
from memory.documents import DocumentMemoryManager
from memory import KnowledgeManager
from memory.state_owner import SharedStateOwner
from memory.transient_state import TransientStateManager
from tools.image_gen import ImageGenerator
from tools.vision import VisionError, VisionRequest, analyze_image, resolve_vision_request

from .tts_probe import RecordingTTS


@dataclass(frozen=True)
class HarnessEvent:
    kind: str
    payload: Any
    ts: float


@dataclass(frozen=True)
class HarnessBootReport:
    ready: bool
    server_ready: bool
    brain_ready: bool
    events: List[Dict[str, Any]]
    isolated_data: bool
    data_dir: str
    live_data_dir: str


@dataclass(frozen=True)
class HarnessTurnResult:
    user_text: str
    assistant_text: str
    messages: List[Dict[str, Any]]
    system_messages: List[str]
    tts_utterances: List[Dict[str, Any]]
    tts_events: List[Dict[str, Any]]
    ui_events: List[Dict[str, Any]]
    status_history: List[str]
    images: List[str]
    timed_out: bool
    duration_s: float


class _HarnessDataOverlay:
    def __init__(self, *, live_data_dir: Path, enabled: bool, keep_copy: bool) -> None:
        self.live_data_dir = Path(live_data_dir)
        self.enabled = enabled
        self.keep_copy = keep_copy
        self.data_dir = self.live_data_dir
        self.root_dir: Optional[Path] = None
        self.kept_data_dir: Optional[Path] = None
        self._cfg_snapshot: Dict[str, Path] = {}

    def prepare(self) -> Path:
        if not self.enabled:
            return self.data_dir

        self.root_dir = Path(tempfile.mkdtemp(prefix="piper-harness-"))
        self.data_dir = self.root_dir / "data"
        shutil.copytree(self.live_data_dir, self.data_dir)
        self._clear_debug_files()
        self._apply_cfg_overlay()
        return self.data_dir

    def close(self) -> None:
        if self._cfg_snapshot:
            for key, value in self._cfg_snapshot.items():
                object.__setattr__(CFG, key, value)
            self._cfg_snapshot.clear()

        self._reset_runtime_caches()

        if not self.root_dir:
            return
        if self.keep_copy:
            self.kept_data_dir = self.data_dir
        else:
            shutil.rmtree(self.root_dir, ignore_errors=True)
        self.root_dir = None

    def _apply_cfg_overlay(self) -> None:
        self._cfg_snapshot = {
            "DATA_DIR": CFG.DATA_DIR,
            "MEMORY_PATH": CFG.MEMORY_PATH,
            "INSTRUCTIONS_PATH": CFG.INSTRUCTIONS_PATH,
            "CODEX_AUTO_REPAIR_ENABLED": CFG.CODEX_AUTO_REPAIR_ENABLED,
        }
        object.__setattr__(CFG, "DATA_DIR", self.data_dir)
        object.__setattr__(CFG, "MEMORY_PATH", self.data_dir / "state" / "memory.jsonl")
        object.__setattr__(CFG, "INSTRUCTIONS_PATH", self.data_dir / "prompts" / "instructions.txt")
        object.__setattr__(CFG, "CODEX_AUTO_REPAIR_ENABLED", False)
        self._reset_runtime_caches()

    def _clear_debug_files(self) -> None:
        for rel_path in (
            "llm_prompt_debug.txt",
            "llm_http_payload_debug.txt",
            "manager_debug.txt",
            "tts_debug.txt",
        ):
            path = data_debug_path(self.data_dir, rel_path)
            if path.exists():
                path.unlink()
        for rel_path in (
            self.data_dir / "state" / "codex_repair_request.json",
            self.data_dir / "state" / "codex_repair_status.json",
            self.data_dir / "state" / "codex_recovery.json",
        ):
            if rel_path.exists():
                rel_path.unlink()

    @staticmethod
    def _reset_runtime_caches() -> None:
        brain_module._brain = None


class PiperHarness:
    def __init__(
        self,
        *,
        persist_turns: bool = False,
        enable_memory_learning: bool = False,
        isolated_data: bool = True,
        keep_data_copy: bool = False,
    ) -> None:
        self.persist_turns = persist_turns
        self.enable_memory_learning = enable_memory_learning
        self.isolated_data = isolated_data
        self.keep_data_copy = keep_data_copy if isolated_data else False
        self.live_data_dir = Path(CFG.DATA_DIR)
        self._data_overlay = _HarnessDataOverlay(
            live_data_dir=self.live_data_dir,
            enabled=self.isolated_data,
            keep_copy=self.keep_data_copy,
        )
        self.data_dir = self._data_overlay.prepare()
        self.kept_data_dir: Optional[Path] = None

        self.chat_state = ChatState(
            memory_path=CFG.MEMORY_PATH,
            session_marker_prefix="=== New session",
        )
        self.style_mgr = StyleManager(
            self.data_dir / "styles",
            active_filename=str(getattr(CFG, "ACTIVE_STYLE_FILE", "default.style")),
        )
        self.tts = RecordingTTS()
        self.ui_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.llm = LlamaServerClient(
            LlamaServerConfig(
                base_url=str(getattr(CFG, "LLAMA_SERVER_URL", "http://127.0.0.1:8080")),
                model=str(getattr(CFG, "LLAMA_SERVER_MODEL", "qwen")),
                temperature=float(getattr(CFG, "TEMPERATURE", 0.7)),
                max_tokens=int(getattr(CFG, "MAX_TOKENS", 512)),
                timeout_s=float(getattr(CFG, "LLAMA_SERVER_TIMEOUT_S", 300.0)),
                stream_read_timeout_s=float(getattr(CFG, "LLAMA_SERVER_STREAM_READ_TIMEOUT_S", 30.0)),
                debug_path=data_debug_path(self.data_dir, "llm_http_payload_debug.txt")
                if CFG.DEBUG_LLM_HTTP_PAYLOADS
                else None,
            )
        )
        self.state_owner = SharedStateOwner.for_data_dir(self.data_dir)
        self.knowledge_mgr = KnowledgeManager(
            self.data_dir,
            self.llm,
            world_model_store=self.state_owner.world_model_store,
            knowledge_store=self.state_owner.knowledge_store,
        )
        self.document_mgr = DocumentMemoryManager(self.data_dir)
        self.transient_state_mgr = TransientStateManager(
            situational_store=self.state_owner.situational_state_store,
            intent_store=self.state_owner.intent_state_store,
            knowledge_mgr=self.knowledge_mgr,
        )
        self.agent_brain = AgentBrain(
            self.data_dir,
            state_owner=self.state_owner,
            knowledge_manager=self.knowledge_mgr,
            transient_state_manager=self.transient_state_mgr,
        )
        self.prompt_context_service = PromptContextService(
            instruction_loader=InstructionLoader(CFG.INSTRUCTIONS_PATH),
            environment_service=EnvironmentService(self.state_owner),
            operational_state_service=OperationalStateService(self.state_owner),
            knowledge_mgr=self.knowledge_mgr,
            transient_state_mgr=self.transient_state_mgr,
            brain=get_brain(self.data_dir),
            document_memory=self.document_mgr,
        )
        self.boot_mgr = BootManager(self.ui_queue)
        self.img_gen = ImageGenerator(self.data_dir)
        self.pipeline = ChatPipeline(
            tts=self.tts,
            chat_append_fn=self.chat_state.append,
            chat_upsert_fn=self.chat_state.upsert_streaming_assistant,
            persist_turn_fn=self._persist_turn,
            set_status_fn=self._set_status,
        )
        self.code_session = EmbeddedCodeSession(
            self.data_dir / "workspace",
            lambda kind, payload: self.ui_queue.put((kind, payload)),
        )

        self._events: List[HarnessEvent] = []
        self._statuses: List[str] = []
        self._images: List[str] = []
        self._active_runs = 0
        self._active_lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._started = False
        self._boot_report: Optional[HarnessBootReport] = None

    def start(self) -> HarnessBootReport:
        if self._started:
            return self._boot_report or HarnessBootReport(False, False, False, [], self.isolated_data, str(self.data_dir), str(self.live_data_dir))

        self.agent_brain.cleanup_old_events()
        self.chat_state.load_recent_memory(limit=50)
        self.knowledge_mgr.set_logger(self._record_log)
        if not self.enable_memory_learning:
            self.knowledge_mgr.consolidate_memory_async = lambda history: None
            self.knowledge_mgr.update_knowledge_async = lambda history: None

        if self._server_healthy():
            self.boot_mgr.server_ready = True
            try:
                get_brain(self.data_dir)
                self.boot_mgr.brain_ready = True
            except Exception as exc:
                self._record_event("boot_error", f"Brain init failed: {exc}")
                self.boot_mgr.brain_ready = False
            self.boot_mgr.ready = self.boot_mgr.server_ready and self.boot_mgr.brain_ready
            self._record_event("boot_log", "Using existing LLM server.")
            if self.boot_mgr.ready:
                self._record_event("boot_ready", "")
        else:
            self.boot_mgr.run_sequence()
            self._pump_ui_queue()

        self._started = True
        self._boot_report = HarnessBootReport(
            ready=bool(self.boot_mgr.ready),
            server_ready=bool(self.boot_mgr.server_ready),
            brain_ready=bool(self.boot_mgr.brain_ready),
            events=[asdict(event) for event in self._events],
            isolated_data=self.isolated_data,
            data_dir=str(self.data_dir),
            live_data_dir=str(self.live_data_dir),
        )
        return self._boot_report

    def close(self) -> None:
        try:
            self.code_session.shutdown()
            self.boot_mgr.shutdown()
        finally:
            self.tts.shutdown()
            self._data_overlay.close()
            self.kept_data_dir = self._data_overlay.kept_data_dir

    def send_text(
        self,
        text: str,
        *,
        timeout_s: float = 180.0,
        idle_grace_s: float = 0.75,
    ) -> HarnessTurnResult:
        if not self._started:
            self.start()

        start_time = time.monotonic()
        msg_start = len(self.chat_state.get_messages_snapshot())
        event_start = len(self._events)
        utterance_start = len(self.tts.utterances)
        tts_event_start = len(self.tts.events)
        status_start = len(self._statuses)
        image_start = len(self._images)

        if not self._handle_command(text):
            self.chat_state.append("user", text)
            self._persist_turn("user", text)
            self._start_generation()

        timed_out = not self._wait_for_idle(timeout_s=timeout_s, idle_grace_s=idle_grace_s)
        snapshot = self.chat_state.get_messages_snapshot()
        new_messages = snapshot[msg_start:]
        assistant_messages = [m for m in new_messages if m.get("role") == "assistant"]
        assistant_text = assistant_messages[-1]["content"] if assistant_messages else ""
        system_messages = [
            str(m.get("content", ""))
            for m in new_messages
            if m.get("role") == "system" and not m.get("hidden")
        ]

        return HarnessTurnResult(
            user_text=text,
            assistant_text=assistant_text,
            messages=new_messages,
            system_messages=system_messages,
            tts_utterances=self.tts.snapshot_utterances(utterance_start),
            tts_events=self.tts.snapshot_events(tts_event_start),
            ui_events=[asdict(event) for event in self._events[event_start:]],
            status_history=list(self._statuses[status_start:]),
            images=list(self._images[image_start:]),
            timed_out=timed_out,
            duration_s=round(time.monotonic() - start_time, 3),
        )

    def dump_state(self) -> Dict[str, Any]:
        return {
            "boot": asdict(self._boot_report) if self._boot_report else None,
            "workspace": {
                "isolated_data": self.isolated_data,
                "data_dir": str(self.data_dir),
                "live_data_dir": str(self.live_data_dir),
                "keep_data_copy": self.keep_data_copy,
                "kept_data_dir": str(self.kept_data_dir) if self.kept_data_dir else None,
            },
            "debug_files": {
                "llm_prompt": str(data_debug_path(self.data_dir, "llm_prompt_debug.txt")),
                "llm_http": str(data_debug_path(self.data_dir, "llm_http_payload_debug.txt")),
                "manager": str(data_debug_path(self.data_dir, "manager_debug.txt")),
                "tts": str(data_debug_path(self.data_dir, "tts_debug.txt")),
            },
            "messages": self.chat_state.get_messages_snapshot(),
            "events": [asdict(event) for event in self._events],
            "statuses": list(self._statuses),
            "images": list(self._images),
            "tts_utterances": self.tts.snapshot_utterances(),
            "tts_events": self.tts.snapshot_events(),
        }

    def _handle_command(self, user_text: str) -> bool:
        res = handle_command(user_text, style_mgr=self.style_mgr)
        if not res.handled:
            return False
        if res.action == "clear":
            self.chat_state.clear()
        elif res.action == "new_session":
            self.chat_state.new_session()
        elif res.action == "codex_support":
            messages = self.chat_state.get_messages_snapshot()
            user_msg = ""
            for message in reversed(messages):
                if str(message.get("role") or "") == "user":
                    user_msg = str(message.get("content") or "").strip()
                    break
            decision = build_manual_codex_snapshot(
                log_path=CFG.CODEX_ESCALATION_LOG_PATH,
                note=res.support_note or "",
                user_msg=user_msg,
                history_tail=messages[-8:],
                monitor_text="",
                dashboard_text="",
                status_snapshot=self._statuses[-1] if self._statuses else "",
                source="harness_command",
            )
            self._record_event("codex_escalation", decision)
            self.chat_state.append("system", f"[UI] Codex support brief prepared: {decision.get('brief_path', '')}")
        elif res.action == "vision_query" and res.vision_path and res.vision_prompt:
            self.chat_state.append("user", user_text)
            self._persist_turn("user", user_text)
            self._start_vision_query(res.vision_path, res.vision_prompt)
            return True
        if res.ui_message:
            self.chat_state.append("system", res.ui_message)
        return True

    def _start_generation(self) -> None:
        with self._active_lock:
            self._active_runs += 1
        self._last_activity = time.monotonic()
        threading.Thread(target=self._run_agent_loop, daemon=True).start()

    def _start_vision_query(self, image_path: str, question: str) -> None:
        with self._active_lock:
            self._active_runs += 1
        self._last_activity = time.monotonic()
        threading.Thread(
            target=self._run_vision_query,
            args=(image_path, question),
            daemon=True,
        ).start()

    def _run_agent_loop(self) -> None:
        try:
            run_agent_loop(
                llm_client=self.llm,
                agent_brain=self.agent_brain,
                knowledge_mgr=self.knowledge_mgr,
                style_mgr=self.style_mgr,
                chat_state=self.chat_state,
                pipeline=self.pipeline,
                ui_queue=self.ui_queue,
                get_current_context_fn=self.chat_state.for_model,
                boot_mgr=self.boot_mgr,
                img_gen=self.img_gen,
                prompt_context_service=self.prompt_context_service,
            )
        except Exception as exc:
            self.ui_queue.put(("error", f"Harness Orchestrator Error: {exc}"))
        finally:
            self.ui_queue.put(("status", "IDLE"))
            with self._active_lock:
                self._active_runs -= 1
            self._last_activity = time.monotonic()

    def _run_vision_query(self, image_path: str, question: str) -> None:
        style_state = self.style_mgr.load(0.7, "af_heart", 0.9)
        try:
            resolved = resolve_vision_request(
                VisionRequest(
                    image_path=image_path,
                    question=question,
                )
            )
            self._set_status(f"Analyzing image: {resolved.image_path.name}")
            self.pipeline.handle_event(
                "start",
                "",
                tts_voice=style_state.tts_voice,
                tts_speed=style_state.tts_speed,
            )
            answer = analyze_image(
                self.llm,
                request=resolved,
                style_overlay=style_state.overlay or "",
                temperature=0.2,
                max_tokens=400,
                cancel_token=None,
            )
            self.pipeline.handle_event(
                "delta",
                answer,
                tts_voice=style_state.tts_voice,
                tts_speed=style_state.tts_speed,
            )
            self.pipeline.handle_event(
                "end",
                "",
                tts_voice=style_state.tts_voice,
                tts_speed=style_state.tts_speed,
            )
        except VisionError as exc:
            self.chat_state.remove_last_assistant_if_exact("Thinking...")
            self.pipeline.handle_event("error", f"[UI] {exc}", tts_voice=None, tts_speed=None)
        except Exception as exc:
            self.chat_state.remove_last_assistant_if_exact("Thinking...")
            self.pipeline.handle_event("error", f"Vision Error: {exc}", tts_voice=None, tts_speed=None)
        finally:
            self._set_status("IDLE")
            with self._active_lock:
                self._active_runs -= 1
            self._last_activity = time.monotonic()

    def _wait_for_idle(self, *, timeout_s: float, idle_grace_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._pump_ui_queue()
            with self._active_lock:
                active_runs = self._active_runs
            if (
                active_runs == 0
                and self.ui_queue.empty()
                and (time.monotonic() - self._last_activity) >= idle_grace_s
            ):
                return True
            time.sleep(0.05)
        self._pump_ui_queue()
        return False

    def _pump_ui_queue(self) -> None:
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                return

            self._last_activity = time.monotonic()
            self._record_event(kind, payload)

            if kind == "status":
                self._set_status(str(payload))
                continue
            if kind == "assistant_stream_start":
                self.pipeline.handle_event("start", "", tts_voice=None, tts_speed=None)
                continue
            if kind == "assistant_stream_delta":
                text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
                self.pipeline.handle_event("delta", text, tts_voice=None, tts_speed=None)
                continue
            if kind == "assistant_stream_end":
                self.pipeline.handle_event("end", "", tts_voice=None, tts_speed=None)
                continue
            if kind == "error":
                self.pipeline.handle_event("error", str(payload), tts_voice=None, tts_speed=None)
                continue
            if kind == "show_image":
                self._images.append(str(payload))
                continue
            if kind == "code_session_launch":
                try:
                    path = str((payload or {}).get("path") or "").strip()
                    if path:
                        self.code_session.start_script(path)
                except Exception as exc:
                    self._record_event("code_session_error", str(exc))
                continue
            if kind in {
                "code_session_reset",
                "code_session_output",
                "code_session_status",
                "code_session_active",
                "code_session_focus",
                "ui_controls_refresh",
            }:
                continue
            if kind == "search_result":
                self._handle_search_result(payload)
                continue

    def _handle_search_result(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        query = str(payload.get("query", ""))
        data = str(payload.get("data", ""))
        self.chat_state.append_message(
            {
                "role": "system",
                "content": f"Background search complete for '{query}'. Data:\n{data[:16000]}",
                "hidden": True,
            }
        )
        self.chat_state.append_message(
            {
                "role": "system",
                "content": "The web search is complete. Summarize the findings for the user now.",
                "hidden": True,
            }
        )
        self._start_generation()

    def _set_status(self, text: str) -> None:
        self._statuses.append(text)

    def _persist_turn(self, role: str, content: str) -> None:
        if self.persist_turns:
            self.chat_state.persist_turn(role, content)

    def _record_event(self, kind: str, payload: Any) -> None:
        self._events.append(HarnessEvent(kind=kind, payload=payload, ts=time.time()))

    def _record_log(self, text: str) -> None:
        self._record_event("memory_log", text)

    @staticmethod
    def _server_healthy() -> bool:
        try:
            req = urllib.request.Request(f"{CFG.LLAMA_SERVER_URL}/health", method="GET")
            with urllib.request.urlopen(req, timeout=1) as resp:
                return resp.status == 200
        except Exception:
            return False
