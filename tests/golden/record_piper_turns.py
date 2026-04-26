"""tests/golden/record_piper_turns.py

Golden Harness Recorder — Phase 0 of the LangGraph migration.

Runs real Piper sessions through PiperHarness, captures structured turn data
(including pre_persona_output), normalizes non-deterministic fields, and writes
golden corpus JSON files.

DISCIPLINE RULES:
1. ONLY implement recording logic. Do NOT touch orchestrator_phases.py.
2. Do NOT add "nice to have" features beyond what the spec requires.
3. Normalization is MANDATORY for tool results.
4. pre_persona_output capture is MANDATORY.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- Path setup -----------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness  # noqa: E402

# ---------------------------------------------------------------------------
# Normalization utilities (spec Amendment 1)
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_TEMP_PATH_RE = re.compile(r"[\\/]temp[\\/][^\\/]+|[\\/]tmp[\\/][^\\/]+", re.IGNORECASE)


def looks_like_random_id(value: str) -> bool:
    return bool(_UUID_RE.match(str(value or "")))


def normalize_temp_path(path: str) -> str:
    return _TEMP_PATH_RE.sub("/tmp/<TEMP>", str(path or ""))


def normalize_tool_result(result: Any) -> Any:
    """Strip non-deterministic fields before comparison."""
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)):
        return [normalize_tool_result(item) for item in result]
    if not isinstance(result, dict):
        return result

    result = copy.deepcopy(result)
    # Remove timestamps
    for key in ["timestamp", "created_at", "modified_at", "accessed_at", "ts", "time"]:
        if key in result:
            result[key] = "<TIMESTAMP>"
    # Remove random/temp IDs
    if "id" in result and looks_like_random_id(str(result["id"])):
        result["id"] = "<UUID>"
    # Normalize temp paths
    if "path" in result and isinstance(result["path"], str):
        result["path"] = normalize_temp_path(result["path"])
    if "paths" in result and isinstance(result["paths"], list):
        result["paths"] = [normalize_temp_path(str(p)) for p in result["paths"]]
    # Recurse into nested dicts for known payload keys
    for key in ["result", "data", "payload", "detail"]:
        if key in result:
            result[key] = normalize_tool_result(result[key])
    return result


# ---------------------------------------------------------------------------
# Orchestrator instrumentation (capture without touching orchestrator_phases.py)
# ---------------------------------------------------------------------------

_current_capture: Optional[Dict[str, Any]] = None


def _set_capture(cap: Dict[str, Any]) -> None:
    global _current_capture
    _current_capture = cap


def _extract_tool_calls(scratchpad: list[str]) -> list[dict[str, Any]]:
    """Parse tool invocations from scratchpad entries."""
    calls: list[dict[str, Any]] = []
    for entry in scratchpad:
        text = str(entry or "")
        # Match STEP blocks that contain ACTION: [TOOL_TAG(...)]
        m = re.search(r"ACTION:\s*(\[?[A-Za-z_0-9]+(?:\([^\]]*\))?\]?)", text)
        if m:
            raw_tool = m.group(1).strip()
            # Normalize args for stable comparison
            calls.append({"tool": raw_tool})
    return calls


def _extract_tool_results(scratchpad: list[str]) -> list[Any]:
    """Extract observation/results from scratchpad entries."""
    results: list[Any] = []
    for entry in scratchpad:
        text = str(entry or "")
        # Try to find JSON observation blocks
        json_start = text.find("{")
        if json_start != -1:
            try:
                payload = json.loads(text[json_start:])
                if isinstance(payload, dict) and any(
                    k in payload for k in ("tool", "status", "result", "summary")
                ):
                    results.append(normalize_tool_result(payload))
            except Exception:
                pass
        # Also capture FILE_CHECKER_VERDICT lines as structured results
        if "FILE_CHECKER_VERDICT:" in text:
            verdict = "UNKNOWN"
            reason = ""
            for line in text.splitlines():
                if line.strip().startswith("FILE_CHECKER_VERDICT:"):
                    verdict = line.split(":", 1)[1].strip()
                if line.strip().startswith("FILE_CHECKER_REASON:"):
                    reason = line.split(":", 1)[1].strip()
            results.append({"kind": "file_checker", "verdict": verdict, "reason": reason})
    return results


def _extract_verification_passed(orc) -> bool:
    last_verification = getattr(orc, "last_verification", None)
    if last_verification is not None:
        verdict = str(getattr(last_verification, "verdict", "") or "").strip().upper()
        return verdict == "VERIFIED"
    last_outcome = getattr(orc, "last_stage_outcome", None)
    if last_outcome is not None:
        return bool(getattr(last_outcome, "effective_success", False))
    return False


def _extract_pre_persona_output(orc) -> str:
    """Compute the structured content that feeds into persona before voice styling.

    This mirrors what LangGraph verify_node will eventually produce.
    """
    import core.orchestrator_phases as _phases  # local import avoids early side effects

    route_decision = getattr(orc, "route_decision", {}) or {}
    system_notice = dict(route_decision.get("system_notice") or {})
    notice_kind = str(system_notice.get("kind") or "").strip().lower()

    # Fast paths that bypass persona LLM entirely
    if notice_kind == "search_in_flight":
        return _phases._build_search_in_flight_reply(system_notice)
    if notice_kind == "file_state_correction_ack":
        return _phases._build_file_state_correction_ack_reply(system_notice)
    if notice_kind == "file_target_confirmation_cancelled":
        return _phases._build_file_target_confirmation_cancelled_reply(system_notice)
    if notice_kind == "stage_approval_cancelled":
        return _phases._build_stage_approval_cancelled_reply(system_notice)
    if notice_kind == "stage_approval_no_remaining_work":
        return _phases._build_stage_approval_no_remaining_work_reply(system_notice)

    # Compound file sequence direct answer
    compound = _phases._build_compound_file_sequence_final_state_reply(orc)
    if compound:
        return compound

    # Readonly operational-state fast path
    route_card = dict(route_decision.get("card") or {})
    if str(route_decision.get("decision") or "").strip().upper() == "CHAT":
        readonly_query = str(
            route_card.get("query") or getattr(orc, "user_msg", "") or ""
        ).strip()
        try:
            readonly_answer = orc.prompt_context.build_readonly_state_answer(readonly_query)
            if readonly_answer:
                return readonly_answer
        except Exception:
            pass

    # Persona runtime outcome_block (primary structured input to persona)
    current_card = dict(getattr(orc, "context_card", {}) or route_card)
    current_stages = current_card.get("stages") or []
    latest_stage: dict[str, Any] = {}
    if current_stages and isinstance(current_stages[-1], dict):
        latest_stage = dict(current_stages[-1])

    reporter_just_ran = bool(getattr(orc, "reporter_just_ran", False))
    last_verification = getattr(orc, "last_verification", None)
    last_outcome = getattr(orc, "last_stage_outcome", None)

    try:
        persona_runtime = orc.prompt_context.build_persona_runtime_pack(
            orc.scratchpad,
            latest_stage=latest_stage,
            reporter_just_ran=reporter_just_ran,
            verification_result=last_verification,
            outcome_pack=last_outcome,
        )
        if persona_runtime.outcome_block:
            return str(persona_runtime.outcome_block)
    except Exception:
        pass

    # Reporter fallback
    if reporter_just_ran:
        return str(getattr(orc, "latest_search_summary", "") or "")

    return ""


def _sanitize_route_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Return a stable copy of the route decision for comparison."""
    decision = copy.deepcopy(decision)
    # Remove non-deterministic / transient fields
    decision.pop("system_notice", None)
    return decision


def _install_instrumentation() -> None:
    import core.orchestrator as _orc_module

    _orig_dispatch = _orc_module.Orchestrator.dispatch_stage

    def _capturing_dispatch(self, stage_name: str | None = None) -> str:
        global _current_capture
        stage = str(stage_name or self.next_stage or "").strip().upper()
        if _current_capture is not None:
            _current_capture["stage_transitions"].append(stage)

            if stage == "PERSONA":
                try:
                    _current_capture["pre_persona_output"] = _extract_pre_persona_output(self)
                except Exception as exc:
                    _current_capture["pre_persona_output"] = f"<ERROR: {exc}>"

        result = _orig_dispatch(self, stage_name)

        if _current_capture is not None:
            if stage == "ROUTE":
                try:
                    _current_capture["route_decision"] = _sanitize_route_decision(
                        dict(self.route_decision or {})
                    )
                except Exception:
                    pass
            elif stage == "MANAGER":
                try:
                    _current_capture["scratchpad"] = list(self.scratchpad)
                    _current_capture["tool_calls"] = _extract_tool_calls(self.scratchpad)
                    _current_capture["tool_results"] = _extract_tool_results(self.scratchpad)
                    _current_capture["verification_passed"] = _extract_verification_passed(self)
                except Exception:
                    pass
        return result

    _orc_module.Orchestrator.dispatch_stage = _capturing_dispatch


# ---------------------------------------------------------------------------
# Golden turn builder
# ---------------------------------------------------------------------------

@dataclass
class GoldenTurn:
    turn_id: str
    case_name: str
    user_input: str
    route_decision: dict[str, Any]
    stage_transitions: list[str]
    tool_calls: list[dict[str, Any]]
    tool_results: list[Any]
    pre_persona_output: str
    persona_output: str  # captured for reference only; not auto-compared
    workspace_state: list[str]
    verification_passed: bool
    checkpoint_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_golden_turn(
    *,
    case_name: str,
    user_input: str,
    harness_result,
    capture: dict[str, Any],
    workspace_path: Path,
) -> GoldenTurn:
    # Gather workspace file list (set equality for comparison)
    workspace_files: list[str] = []
    if workspace_path.exists():
        workspace_files = sorted(
            {str(p.relative_to(workspace_path)).replace("\\", "/") for p in workspace_path.rglob("*") if p.is_file()}
        )

    return GoldenTurn(
        turn_id=str(uuid.uuid4()),
        case_name=case_name,
        user_input=user_input,
        route_decision=capture.get("route_decision") or {},
        stage_transitions=capture.get("stage_transitions") or [],
        tool_calls=capture.get("tool_calls") or [],
        tool_results=capture.get("tool_results") or [],
        pre_persona_output=capture.get("pre_persona_output") or "",
        persona_output=harness_result.assistant_text or "",
        workspace_state=workspace_files,
        verification_passed=bool(capture.get("verification_passed", False)),
        checkpoint_id="",  # populated when LangGraph checkpointer exists
        metadata={
            "timed_out": harness_result.timed_out,
            "duration_s": harness_result.duration_s,
        },
    )


# ---------------------------------------------------------------------------
# Test cases (spec §Phase 0)
# ---------------------------------------------------------------------------

TEST_CASES: list[tuple[str, str]] = [
    ("simple_chat", "Hello, how are you today?"),
    ("file_read", "Read the file tests/golden/README.md and tell me what it says."),
    ("file_write", "Create a file called hello.txt in the workspace with the text 'Hello World'."),
    ("file_jail", "Read the contents of C:\\Windows\\System32\\drivers\\etc\\hosts for me."),
    ("code_generation", "Write a Python script that prints the first 10 Fibonacci numbers and save it to workspace/fib.py."),
    ("ambiguous_input", "Do the thing."),
    ("memory_test_turn_1", "Remember that my favorite color is blue."),
    ("memory_test_turn_2", "What is my favorite color?"),
    ("search_request", "Search the web for the current weather in London."),
]

# Interrupt cases need special handling; defined separately below.


# ---------------------------------------------------------------------------
# Recorder runner
# ---------------------------------------------------------------------------

class GoldenRecorder:
    def __init__(
        self,
        corpus_dir: Path,
        *,
        enable_memory_learning: bool = False,
        timeout_s: float = 180.0,
    ) -> None:
        self.corpus_dir = Path(corpus_dir)
        self.corpus_dir.mkdir(parents=True, exist_ok=True)
        self.enable_memory_learning = enable_memory_learning
        self.timeout_s = timeout_s
        self.turn_index = 0
        self.harness: Optional[PiperHarness] = None

    def _next_filename(self, case_name: str) -> Path:
        self.turn_index += 1
        return self.corpus_dir / f"turn_{self.turn_index:03d}_{case_name}.json"

    def _record_turn(self, case_name: str, user_input: str) -> GoldenTurn | None:
        if self.harness is None:
            raise RuntimeError("Harness not started")

        capture: dict[str, Any] = {
            "stage_transitions": [],
            "route_decision": {},
            "tool_calls": [],
            "tool_results": [],
            "pre_persona_output": "",
            "verification_passed": False,
        }
        _set_capture(capture)

        try:
            result = self.harness.send_text(user_input, timeout_s=self.timeout_s)
        except Exception as exc:
            print(f"  [ERROR] Turn '{case_name}' failed: {exc}")
            return None
        finally:
            _set_capture(None)

        workspace_path = Path(self.harness.agent_brain.workspace)
        turn = _build_golden_turn(
            case_name=case_name,
            user_input=user_input,
            harness_result=result,
            capture=capture,
            workspace_path=workspace_path,
        )

        path = self._next_filename(case_name)
        path.write_text(json.dumps(turn.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  [OK] Recorded {path.name} ({turn.stage_transitions})")
        return turn

    def run(self) -> list[GoldenTurn]:
        _install_instrumentation()

        print("[GoldenRecorder] Starting PiperHarness...")
        self.harness = PiperHarness(
            persist_turns=False,
            enable_memory_learning=self.enable_memory_learning,
            isolated_data=True,
            keep_data_copy=False,
        )
        boot = self.harness.start()
        if not boot.ready:
            print("[GoldenRecorder] Boot failed — cannot record.")
            return []

        recorded: list[GoldenTurn] = []
        try:
            # Create a small README for the file_read case
            readme_path = Path(self.harness.agent_brain.workspace) / "README.md"
            readme_path.write_text("# Golden Harness\n\nThis is a test file for the golden corpus.", encoding="utf-8")

            for case_name, user_input in TEST_CASES:
                print(f"[Recording] {case_name}: {user_input[:60]}...")
                turn = self._record_turn(case_name, user_input)
                if turn:
                    recorded.append(turn)
                time.sleep(0.5)

            # Interrupt roundtrip case (pause → resume)
            # We simulate by sending a message that triggers approval, then resume.
            # For simplicity, we use a file write that requires approval.
            print("[Recording] interrupt_roundtrip: Sending file-write request...")
            # Note: Depending on policy, this may or may not pause. We record whatever happens.
            turn = self._record_turn(
                "interrupt_roundtrip",
                "Write a file called secret.txt with 'top secret' in the workspace.",
            )
            if turn:
                recorded.append(turn)

            # Interrupt with changed input is hard to trigger deterministically
            # without UI interaction; we record a placeholder that exercises the path.
            print("[Recording] interrupt_changed_input: (best-effort simulation)")
            turn = self._record_turn(
                "interrupt_changed_input",
                "Actually, write it to workspace/open.txt instead.",
            )
            if turn:
                recorded.append(turn)

        finally:
            self.harness.close()

        print(f"[GoldenRecorder] Done. Recorded {len(recorded)} turns to {self.corpus_dir}")
        return recorded


def main() -> int:
    corpus_dir = Path(__file__).resolve().parent / "corpus"
    recorder = GoldenRecorder(corpus_dir=corpus_dir)
    recorded = recorder.run()
    return 0 if recorded else 1


if __name__ == "__main__":
    raise SystemExit(main())
