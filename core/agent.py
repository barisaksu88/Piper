"""core/agent.py

Piper's Agentic Brain.
Parses LLM output for Menu/Tool tags and executes Python actions.
"""

import json
import re
import os
import shutil
import datetime
import hashlib
import fnmatch
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple
from memory.brain import get_brain
from memory.state_owner import SharedStateOwner
from core.runtime_control import CancellationToken, OperationCancelled
from tools.registry import get_tool_spec
from tools.workspace_runtime import WorkspaceToolRuntime

@dataclass
class AgentAction:
    action_type: str  # "REASON", "TOOL", "ANSWER", "UNKNOWN"
    tag: Optional[str] = None 
    payload: Optional[str] = None 
    content: str = "" 
    execute_result: Optional[Any] = None 

class AgentBrain:
    def __init__(
        self,
        data_dir: Path,
        *,
        state_owner: SharedStateOwner,
        knowledge_manager: Any | None = None,
        transient_state_manager: Any | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.workspace = data_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.state_owner = state_owner
        self.task_store = self.state_owner.task_store
        self.event_store = self.state_owner.event_store
        self.knowledge_store = self.state_owner.knowledge_store
        self.knowledge_manager = knowledge_manager
        self.transient_state_manager = transient_state_manager
        self.workspace_runtime = WorkspaceToolRuntime(self.workspace)
    @staticmethod
    def _normalize_lookup(text: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
        return re.sub(r"\s+", " ", cleaned).strip()
    def _resolve_existing_name(self, query: str, candidates: Iterable[str]) -> Optional[str]:
        query_norm = self._normalize_lookup(query)
        if not query_norm:
            return None

        best_name = None
        best_score: Tuple[int, int, int] = (-1, -1, -1)
        query_tokens = set(query_norm.split())

        for candidate in candidates:
            cand_norm = self._normalize_lookup(candidate)
            if not cand_norm:
                continue
            if cand_norm == query_norm:
                return candidate

            cand_tokens = set(cand_norm.split())
            overlap = len(query_tokens & cand_tokens)
            contains = int(query_norm in cand_norm or cand_norm in query_norm)
            token_cover = int(bool(query_tokens) and query_tokens.issubset(cand_tokens))
            score = (token_cover, contains, overlap)
            if score > best_score:
                best_name = candidate
                best_score = score

        if best_name and best_score >= (0, 1, 1):
            return best_name
        if best_name and best_score >= (1, 0, 1):
            return best_name
        return None
    @staticmethod
    def _split_completion_payload(payload: str) -> Tuple[str, str]:
        raw = (payload or "").strip()
        raw_lower = raw.lower()
        for delimiter in ("=>", "|", " outcome:", " note:"):
            idx = raw_lower.find(delimiter.lower())
            if idx != -1:
                subject = raw[:idx].strip(" :-")
                note = raw[idx + len(delimiter):].strip(" :-")
                return subject, note
        return raw, ""
    def _archive_resolution(self, *, kind: str, name: str, outcome_note: str = "", scheduled_date: str = "") -> None:
        fragments = [f"(u) completed {kind} '{name}'"]
        if scheduled_date:
            fragments.append(f"that was scheduled for {scheduled_date}")
        text = " ".join(fragments).strip() + "."
        if outcome_note:
            text += f" Outcome: {outcome_note}."
        try:
            brain = get_brain(self.data_dir)
            brain.remember(
                text=text,
                metadata={
                    "type": f"{kind}_resolution",
                    "date": datetime.datetime.now().strftime("%b %d, %Y"),
                },
            )
        except Exception as e:
            print(f"[Agent] Archive memory failed: {e}")

    def _reconcile_transient_operational_change(
        self,
        *,
        kind: str,
        action: str,
        name: str,
        source_text: str = "",
        scheduled_date: str = "",
    ) -> None:
        manager = self.transient_state_manager
        if manager is None or not hasattr(manager, "reconcile_operational_change"):
            return
        try:
            manager.reconcile_operational_change(
                kind=kind,
                action=action,
                name=name,
                source_text=source_text,
                scheduled_date=scheduled_date,
            )
        except Exception as exc:
            print(f"[Agent] Transient state reconcile failed: {exc}")
    def cleanup_old_events(self) -> int:
        """Removes events that have passed. Returns count of removed events."""
        removed_count = self.event_store.cleanup_old_events(now=datetime.datetime.now())
        if removed_count > 0:
            print(f"[Agent] Cleaned up {removed_count} old events.")
        return removed_count

    @staticmethod
    def _normalize_run_code_payload(code: str) -> str:
        payload = str(code or "").strip()
        payload = re.sub(r"^\s*<python\s+code>\s*", "", payload, flags=re.IGNORECASE)
        payload = re.sub(r"^\s*<python>\s*", "", payload, flags=re.IGNORECASE)
        payload = re.sub(r"\s*</python\s+code>\s*$", "", payload, flags=re.IGNORECASE)
        payload = re.sub(r"\s*</python>\s*$", "", payload, flags=re.IGNORECASE)
        return payload.strip()

    def parse_and_execute(self, llm_output: str, *, cancel_token: CancellationToken | None = None) -> AgentAction:
        """Parses LLM output, executes tools, returns result."""
        
        text = llm_output.strip()
        if not text:
            return AgentAction(action_type="ANSWER", tag="ANSWER", content="")

        # 1. CHECK BLOCK TAGS (Run Code)
        # We check this first because it's a large block, not a single line tag.
        run_code_match = re.search(r'\[RUN_CODE\](.*?)\[/RUN_CODE\]', text, re.DOTALL | re.IGNORECASE)
        if run_code_match:
            code = self._normalize_run_code_payload(run_code_match.group(1))
            result = self.exec_run_code(code, cancel_token=cancel_token)
            return AgentAction(
                action_type="TOOL", tag="RUN_CODE", payload=code, content=llm_output, execute_result=result
            )
        if re.search(r'\[RUN_CODE\]', text, re.IGNORECASE):
            parts = re.split(r'\[RUN_CODE\]', text, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2 and parts[1].strip():
                code = self._normalize_run_code_payload(parts[1].replace("[/RUN_CODE]", ""))
                result = self.exec_run_code(code, cancel_token=cancel_token)
                return AgentAction(
                    action_type="TOOL", tag="RUN_CODE", payload=code, content=llm_output, execute_result=result
                )
        malformed_run_code_match = re.search(r'\[RUN_CODE\s+(.*?)\s*\[/RUN_CODE\]', text, re.DOTALL | re.IGNORECASE)
        if malformed_run_code_match:
            code = self._normalize_run_code_payload(malformed_run_code_match.group(1))
            result = self.exec_run_code(code, cancel_token=cancel_token)
            return AgentAction(
                action_type="TOOL", tag="RUN_CODE", payload=code, content=llm_output, execute_result=result
            )
        malformed_run_code_inline = re.search(r'\[RUN_CODE\s+(.+)\]\s*$', text, re.DOTALL | re.IGNORECASE)
        if malformed_run_code_inline:
            code = self._normalize_run_code_payload(malformed_run_code_inline.group(1))
            result = self.exec_run_code(code, cancel_token=cancel_token)
            return AgentAction(
                action_type="TOOL", tag="RUN_CODE", payload=code, content=llm_output, execute_result=result
            )

        file_op_match = re.search(r'\[FILE_OP\](.*?)\[/FILE_OP\]', text, re.DOTALL | re.IGNORECASE)
        if file_op_match:
            payload_text = file_op_match.group(1).strip()
            result = self.exec_file_op(payload_text, cancel_token=cancel_token)
            return AgentAction(
                action_type="TOOL", tag="FILE_OP", payload=payload_text, content=llm_output, execute_result=result
            )
        if re.search(r'\[FILE_OP\]', text, re.IGNORECASE):
            parts = re.split(r'\[FILE_OP\]', text, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2 and parts[1].strip():
                payload_text = parts[1].replace("[/FILE_OP]", "").strip()
                result = self.exec_file_op(payload_text, cancel_token=cancel_token)
                return AgentAction(
                    action_type="TOOL", tag="FILE_OP", payload=payload_text, content=llm_output, execute_result=result
                )
        malformed_file_op_match = re.search(r'\[FILE_OP\s+(.*?)\s*\[/FILE_OP\]', text, re.DOTALL | re.IGNORECASE)
        if malformed_file_op_match:
            payload_text = malformed_file_op_match.group(1).strip()
            result = self.exec_file_op(payload_text, cancel_token=cancel_token)
            return AgentAction(
                action_type="TOOL", tag="FILE_OP", payload=payload_text, content=llm_output, execute_result=result
            )
        malformed_file_op_inline = re.search(r'\[FILE_OP\s+(.+)\]\s*$', text, re.DOTALL | re.IGNORECASE)
        if malformed_file_op_inline:
            payload_text = malformed_file_op_inline.group(1).strip()
            result = self.exec_file_op(payload_text, cancel_token=cancel_token)
            return AgentAction(
                action_type="TOOL", tag="FILE_OP", payload=payload_text, content=llm_output, execute_result=result
            )

        # 2. CHECK SINGLE TAGS
        # FIX: Added re.DOTALL to allow matching tags that contain newlines (like code blocks)
        match = re.search(r'\[([A-Za-z_]+)(?::\s*(.*?))?\]', text, re.DOTALL)
        
        if match:
            tag_raw = match.group(1)
            tag_name = tag_raw.upper()
            tag_payload = match.group(2).strip() if match.group(2) else None
            
            tool_spec = get_tool_spec(tag_name)
            if tool_spec:
                needs_arg = tool_spec.requires_arg
                exec_func_name = tool_spec.runtime_handler
                
                if needs_arg and not tag_payload:
                    return AgentAction(
                        action_type="TOOL", tag=tag_name, content=llm_output,
                        execute_result=f"Error: Tag [{tag_name}] requires an argument."
                    )
                
                result = ""
                if exec_func_name:
                    func = getattr(self, exec_func_name, None)
                    if func:
                        try:
                            if tag_name == "INSTALL_PACKAGE":
                                result = func(tag_payload, cancel_token=cancel_token)
                            else:
                                result = func(tag_payload)
                        except OperationCancelled:
                            raise
                        except Exception as e:
                            result = f"Execution Error: {e}"
                
                return AgentAction(
                    action_type="TOOL", tag=tag_name, payload=tag_payload, content=llm_output, execute_result=result
                )

        # 3. FALLBACK / ANSWER
        if "[ANSWER]" in text.upper():
            clean = re.sub(r'\[ANSWER\]', '', text, flags=re.IGNORECASE).strip()
            return AgentAction(action_type="ANSWER", tag="ANSWER", content=clean)

        return AgentAction(action_type="ANSWER", tag="ANSWER", content=llm_output)

    def _build_extension_inventory(
        self,
        root_path: Path,
        workspace_root: Path,
        *,
        extensions: set[str] | None = None,
    ) -> Dict[str, Any]:
        return self.workspace_runtime._build_extension_inventory(
            root_path,
            workspace_root,
            extensions=extensions,
        )

    def exec_file_op(self, payload_text: str, *, cancel_token: CancellationToken | None = None) -> Dict[str, Any]:
        return self.workspace_runtime.exec_file_op(payload_text, cancel_token=cancel_token)

    def exec_run_code(self, code: str, *, cancel_token: CancellationToken | None = None) -> Dict[str, Any]:
        return self.workspace_runtime.exec_run_code(code, cancel_token=cancel_token)

    def exec_install_package(self, package_name: str, *, cancel_token: CancellationToken | None = None) -> str:
        """Installs a python package using pip into the current venv."""
        import subprocess
        import sys
        import os
        
        # Safety: Block massive/unrelated packages
        BLOCKED_PACKAGES = ["torch", "tensorflow"] # Remove numpy/pandas if you want them
        if package_name.lower() in [b.lower() for b in BLOCKED_PACKAGES]:
            return f"BLOCKED: '{package_name}' is too large. Use standard libraries."

        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", package_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            start = time.monotonic()
            while True:
                if cancel_token is not None and cancel_token.is_cancelled:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise OperationCancelled(cancel_token.reason)
                if process.poll() is not None:
                    break
                if time.monotonic() - start > 120:
                    process.kill()
                    stdout, stderr = process.communicate()
                    return "ERROR: Installation timed out."
                time.sleep(0.1)

            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                # Find site-packages path for verification
                import site
                site_pkgs = site.getsitepackages()[0]
                
                # Special warning for Graphviz
                warning = ""
                if package_name.lower() == "graphviz":
                    warning = "\nWARNING: 'graphviz' requires external binaries (dot.exe). The Python wrapper is installed, but rendering will fail unless you install the Graphviz application on Windows and add it to PATH."
                
                return f"SUCCESS: Installed '{package_name}' to {site_pkgs}.{warning}\nPip Output:\n{stdout[-500:]}"
            else:
                return f"ERROR: Failed to install {package_name}.\n{stderr}"
        except Exception as e:
            return f"ERROR: {e}"
    def exec_add_task(self, task_name: str) -> str:
        self.task_store.add(task_name, "pending")
        self._reconcile_transient_operational_change(
            kind="task",
            action="add",
            name=task_name,
            source_text=task_name,
        )
        return f"Task added: {task_name}"
    def exec_delete_task(self, task_name: str) -> str:
        target = self._resolve_existing_name(task_name, self.task_store.load().keys()) or task_name
        if self.task_store.remove(target):
            return f"Task deleted: {target}"
        return f"Task not found: {task_name}"
    def exec_complete_task(self, payload: str) -> str:
        task_name, outcome_note = self._split_completion_payload(payload)
        target = self._resolve_existing_name(task_name, self.task_store.load().keys())
        if not target:
            return f"Task not found: {task_name}"
        prior_status = self.task_store.pop(target)
        if prior_status is None:
            return f"Task not found: {task_name}"
        self._reconcile_transient_operational_change(
            kind="task",
            action="complete",
            name=target,
            source_text=payload,
        )
        self._archive_resolution(kind="task", name=target, outcome_note=outcome_note)
        return f"Task completed and archived: {target}"
    def exec_list_tasks(self, _) -> str:
        pending = self.task_store.pending_names()
        if not pending:
            return "No pending tasks."
        return "Pending Tasks:\n- " + "\n- ".join(pending)
    @staticmethod
    def _extract_event_time(text: str) -> Optional[str]:
        """Parse an 'at HH:MM' / 'at 3pm' style time from a date phrase.

        Returns a normalised 'HH:MM' string, or None if no time is found.
        """
        m = re.search(
            r"(?i)\bat\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?",
            text,
        )
        if not m:
            return None
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        meridiem = (m.group(3) or "").lower().replace(".", "")
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return f"{hour:02d}:{minute:02d}"

    def exec_add_event(self, args: str) -> str:
        try:
            parts = args.rsplit(" on ", 1)
            if len(parts) != 2:
                return "Invalid format. Use: [ADD_EVENT: Name on <date phrase>]"
            name, date_text = parts[0].strip(), parts[1].strip()
            time_str = self._extract_event_time(date_text)
            resolved_date = self._resolve_event_date(date_text)
            if not resolved_date:
                return f"Invalid event date: {date_text}. Use YYYY-MM-DD or a simple phrase like tomorrow."
            self.event_store.add(name, resolved_date, time_str)
            self._reconcile_transient_operational_change(
                kind="event",
                action="add",
                name=name,
                source_text=args,
                scheduled_date=resolved_date,
            )
            time_label = f" at {time_str}" if time_str else ""
            return f"Event scheduled: {name} on {resolved_date}{time_label}"
        except Exception as e:
            return f"Error adding event: {e}"

    def exec_reschedule_event(self, args: str) -> str:
        try:
            parts = re.split(r"\s+to\s+", str(args or "").strip(), maxsplit=1, flags=re.IGNORECASE)
            if len(parts) != 2:
                return "Invalid format. Use: [RESCHEDULE_EVENT: Event name to <new date phrase>]"
            name_raw, date_text = parts[0].strip(), parts[1].strip()
            target = self._resolve_existing_name(name_raw, self.event_store.load().keys()) or name_raw
            time_str = self._extract_event_time(date_text)
            resolved_date = self._resolve_event_date(date_text)
            if not resolved_date:
                return f"Invalid date: {date_text}. Use YYYY-MM-DD or a phrase like 'next Friday'."
            self.event_store.remove(target)
            self.event_store.add(target, resolved_date, time_str)
            self._reconcile_transient_operational_change(
                kind="event",
                action="add",
                name=target,
                source_text=args,
                scheduled_date=resolved_date,
            )
            time_label = f" at {time_str}" if time_str else ""
            return f"Event rescheduled: {target} to {resolved_date}{time_label}"
        except Exception as e:
            return f"Error rescheduling event: {e}"

    def exec_remove_event(self, name: str) -> str:
        target = self._resolve_existing_name(name, self.event_store.load().keys()) or name
        if self.event_store.remove(target):
            self._reconcile_transient_operational_change(
                kind="event",
                action="remove",
                name=target,
                source_text=name,
            )
            return f"Event removed: {target}"
        return f"Event not found: {name}"
    def exec_complete_event(self, payload: str) -> str:
        event_name, outcome_note = self._split_completion_payload(payload)
        target = self._resolve_existing_name(event_name, self.event_store.load().keys())
        if not target:
            return f"Event not found: {event_name}"
        scheduled_date = self.event_store.pop(target)
        if scheduled_date is None:
            return f"Event not found: {event_name}"
        self._reconcile_transient_operational_change(
            kind="event",
            action="complete",
            name=target,
            source_text=payload,
            scheduled_date=scheduled_date,
        )
        self._archive_resolution(
            kind="event",
            name=target,
            outcome_note=outcome_note,
            scheduled_date=scheduled_date,
        )
        return f"Event completed and archived: {target}"
    def exec_list_events(self, _) -> str:
        valid_events = self.event_store.upcoming(now=datetime.datetime.now())
        if not valid_events:
            return "No upcoming events."
        return "Upcoming Events:\n- " + "\n- ".join(
            [f"{item['name']} on {item['date']}" for item in valid_events]
        )
    def _resolve_event_date(self, date_text: str) -> Optional[str]:
        raw = (date_text or "").strip().lower()
        if not raw:
            return None

        raw = re.sub(r"\b(on|by)\b", "", raw).strip()
        today = datetime.date.today()
        raw = re.sub(r"(?i)\bat\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\.?", "", raw).strip(" ,.-")

        try:
            return datetime.datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            pass

        raw_compact = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", raw)
        month_names = {
            "january": 1,
            "february": 2,
            "march": 3,
            "april": 4,
            "may": 5,
            "june": 6,
            "july": 7,
            "august": 8,
            "september": 9,
            "october": 10,
            "november": 11,
            "december": 12,
        }
        month_patterns = (
            r"\b(\d{1,2})\s+of\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b",
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})\b",
            r"\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        )
        for pattern in month_patterns:
            match = re.search(pattern, raw_compact)
            if not match:
                continue
            if match.group(1).isdigit():
                day = int(match.group(1))
                month = month_names[match.group(2)]
            else:
                month = month_names[match.group(1)]
                day = int(match.group(2))
            try:
                candidate = datetime.date(today.year, month, day)
            except ValueError:
                continue
            if candidate < today:
                try:
                    candidate = datetime.date(today.year + 1, month, day)
                except ValueError:
                    continue
            return candidate.strftime("%Y-%m-%d")

        bare_day_match = re.fullmatch(r"(?:the\s+)?(\d{1,2})", raw_compact)
        if bare_day_match:
            day = int(bare_day_match.group(1))
            if 1 <= day <= 31:
                month = today.month
                year = today.year
                while True:
                    try:
                        candidate = datetime.date(year, month, day)
                    except ValueError:
                        month += 1
                        if month > 12:
                            month = 1
                            year += 1
                        continue
                    if candidate < today:
                        month += 1
                        if month > 12:
                            month = 1
                            year += 1
                        continue
                    return candidate.strftime("%Y-%m-%d")

        weekday_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }

        if raw.startswith("tomorrow"):
            return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if raw in {"today", "tonight"}:
            return today.strftime("%Y-%m-%d")
        if raw == "next week":
            return (today + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        if raw == "this week":
            return today.strftime("%Y-%m-%d")
        if raw in weekday_map:
            target = weekday_map[raw]
            delta = (target - today.weekday()) % 7
            if delta == 0:
                delta = 7
            return (today + datetime.timedelta(days=delta)).strftime("%Y-%m-%d")
        if raw.startswith("next ") and raw[5:] in weekday_map:
            target = weekday_map[raw[5:]]
            delta = (target - today.weekday()) % 7
            if delta == 0:
                delta = 7
            delta += 7
            return (today + datetime.timedelta(days=delta)).strftime("%Y-%m-%d")
        if raw.startswith("this ") and raw[5:] in weekday_map:
            target = weekday_map[raw[5:]]
            delta = (target - today.weekday()) % 7
            return (today + datetime.timedelta(days=delta)).strftime("%Y-%m-%d")

        return None
    def exec_list_knowledge(self, _) -> str:
        try:
            if self.knowledge_manager is not None:
                return self.knowledge_manager.list_for_display()
            data = self.knowledge_store.load_active()
            if not data:
                return "No knowledge stored."
            lines = []
            for k, v in data.items():
                val = v.get('value') if isinstance(v, dict) else v
                lines.append(f"- {k}: {val}")
            return "User Knowledge:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error reading knowledge: {e}"
    def exec_update_knowledge(self, args: str) -> str:
        parts = args.split("=", 1)
        if len(parts) != 2: return "Invalid format. Use: key = value"
        key = parts[0].strip()
        value = parts[1].strip()
        if self.knowledge_manager is not None:
            if not self.knowledge_manager.upsert_fact(key, value):
                return "Error: Could not update world model memory."
        else:
            self.knowledge_store.upsert_value(key, value)
        
        # FIX: Return a boring success message to prevent the Controller 
        # from reading the data again and looping.
        return "System confirmation: Knowledge base updated successfully."
    def exec_remove_knowledge(self, key: str) -> str:
        if self.knowledge_manager is not None:
            if self.knowledge_manager.remove_fact(key):
                return f"Knowledge removed: {key}"
            return f"Key not found: {key}"
        data = self.knowledge_store.load_active()
        if not data:
            return "No knowledge file."
        if key in data:
            del data[key]
            self.knowledge_store.save(data)
            return f"Knowledge removed: {key}"
        return f"Key not found: {key}"
