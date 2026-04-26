from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ToolSpec:
    name: str
    domain: str
    requires_arg: bool
    runtime_handler: Optional[str]
    description: str
    rules: Tuple[str, ...] = ()
    syntax: Tuple[str, ...] = ()
    listed_in_domain: bool = True
    auto_finish_on_success: bool = False
    success_prefixes: Tuple[str, ...] = ()


_TOOL_SPECS: Tuple[ToolSpec, ...] = (
    ToolSpec(
        name="FILE_OP",
        domain="FILE_WORK",
        requires_arg=True,
        runtime_handler="exec_file_op",
        description="Use for direct file CRUD and JSON/text workspace operations with structured JSON payloads.",
        rules=(
            "Prefer FILE_OP over RUN_CODE for direct file creation, text writing, JSON writing, JSON updates, file reading, directory inspection, directory creation, and path moves/copies/deletes.",
            "FILE_OP payload must be valid JSON inside a FILE_OP block.",
            "Use FILE_OP for simple deterministic file work and structured file management; use RUN_CODE only for real computation, parsing, or transformations that cannot be expressed with FILE_OP.",
            "Do not embed a large multi-line source file or full program inside FILE_OP write_text JSON; prefer RUN_CODE for substantive code-file rewrites after inspection.",
            "Paths must be relative to the workspace.",
            "All FILE_OP paths are relative to the workspace root, not to a previous stage's folder.",
            "If a file lives under cmpdemo/project, the path must be cmpdemo/project/config.json, not project/config.json.",
            "For text files, use action write_text or append_text.",
            "For JSON files, use action write_json or update_json.",
            "For reading files, use action read_text, read_many, list_tree, or find_paths.",
            "Use read_text only for a single file with the singular key 'path'.",
            "Use read_many only for multiple files with the plural key 'paths'.",
            "For grouping or consolidating files by extension, use action extension_inventory first, then consolidate_by_extension.",
            "When the user says 'except X', 'leave out X', 'skip X', 'don't move X', 'ignore X', or any similar exclusion for consolidate_by_extension, always use the key 'exclude_files' containing a list of filenames to skip. Do not invent other key names.",
            "Use action find_paths when you need to verify whether a specific file exists, locate a missing dependency, or search by exact or partial basename/glob/substring.",
            "Use action delete_empty_dirs when the user wants empty folders removed after consolidation or cleanup.",
            "For creating folders, use action ensure_dir or ensure_dirs.",
            "For moving or renaming files or folders, use action move_path or move_many.",
            "For copying files or folders, use action copy_path or copy_many.",
            "For deleting files or folders, use action delete_path or delete_many.",
            "Use structured FILE_OP batch actions for bulk file management instead of ad-hoc RUN_CODE loops whenever possible.",
            "After one successful list_tree on an unchanged root, do not repeat the same list_tree call unless you inspect a different subdirectory or the workspace changed.",
            "For stages about a specific missing file or path, prefer find_paths over repeating list_tree.",
            "For broad reorganization or cleanup goals, use the existing inventory to plan bounded move_many or copy_many batches instead of inventory loops.",
            "For extension-based organization, prefer extension_inventory and consolidate_by_extension over hand-writing dozens of move_many entries.",
            "Do not invent ambiguous catch-all destination folders unless the user asked for that structure or you first paused for approval.",
            "Do not wrap JSON in quotes.",
            "Do not add extra narration inside the FILE_OP block.",
        ),
        syntax=(
            '[FILE_OP]\n{"action":"list_tree","root":".","max_depth":4}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"extension_inventory","root":"."}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"ensure_dir","path":"cmpdemo"}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"ensure_dirs","paths":["documents","images","scripts"]}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"consolidate_by_extension","root":"."}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"consolidate_by_extension","root":".","exclude_files":["important.pdf","keep_this.txt"]}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"delete_empty_dirs","root":"."}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"move_path","src":"drafts/todo.txt","dst":"documents/todo.txt"}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"move_many","moves":[{"src":"drafts/todo.txt","dst":"documents/todo.txt"},{"src":"drafts/logo.png","dst":"images/logo.png"}]}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"write_text","path":"cmpdemo/notes.txt","content":"alpha\\nbeta\\n"}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"write_json","path":"cmpdemo/project/config.json","data":{"name":"demo","version":1}}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"update_json","path":"cmpdemo/project/config.json","updates":{"version":2,"enabled":true}}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"read_text","path":"cmpdemo/notes.txt"}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"read_many","paths":["cmpdemo/notes.txt","cmpdemo/calc.py"]}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"find_paths","root":".","query":"logo.png","mode":"basename"}\n[/FILE_OP]',
            '[FILE_OP]\n{"action":"find_paths","root":".","query":"meeting notes","mode":"basename"}\n[/FILE_OP]',
        ),
    ),
    ToolSpec(
        name="RUN_CODE",
        domain="FILE_WORK",
        requires_arg=True,
        runtime_handler="exec_run_code",
        description="Use for file creation, file editing, calculations, text transformation, and structured workspace operations.",
        rules=(
            "Prefer FILE_OP first for direct text/JSON file CRUD and file reading.",
            "Tool block format must be exactly:",
            "[RUN_CODE]",
            "<python code>",
            "[/RUN_CODE]",
            "Do not include the literal placeholder text <python code> inside the executed code block.",
            "Code must be valid Python.",
            "RUN_CODE is not a shell. Do not use shell syntax such as > | && cat rm etc.",
            "Use relative paths only (the workspace directory is already the working directory).",
            "All paths are relative to the workspace root, not to a previously mentioned folder.",
            'To execute an existing workspace Python script, use run_workspace_script("relative/path.py"). Do not import subprocess or sys for that.',
            "If the stage goal modifies a file, the file itself must be changed.",
            "Use print function to display the outcome in detail.",
            "Do not compress compound Python statements onto one line.",
            "Statements such as with, if, for, while, try, def, class, and match must use real new lines and indentation.",
            "Recommended FILE_WORK workflow:",
            "1. Inspect the current file state if needed.",
            "2. Decide whether the goal is already satisfied.",
            "3. Modify the file only if required.",
            "4. Optionally inspect once after modification.",
            "5. Stop.",
            "Additional guidance:",
            "For text files, read the file, compute the correct final content, then rewrite it.",
            "For existing code files, prefer RUN_CODE over FILE_OP write_text once you need a substantive code edit or rewrite.",
            "Avoid writing complicated verification scripts.",
            "Prefer inspecting the actual file contents.",
            "Do not verify the same unchanged file repeatedly.",
            "If inspection shows the goal is satisfied, stop immediately.",
        ),
        syntax=(
            "[RUN_CODE]\n<python code>\n[/RUN_CODE]",
            '[RUN_CODE]\nrun_workspace_script("mini_game.py")\n[/RUN_CODE]',
        ),
    ),
    ToolSpec(
        name="CREATE_IMAGE",
        domain="IMAGE_WORK",
        requires_arg=True,
        runtime_handler=None,
        description="Generate an image from a text prompt.",
        rules=(
            "Use only when the user explicitly wants an image created or modified.",
            "Do not use for text files, charts, workflows, or reports.",
            "Put the entire tool call inside the tool field as a single string.",
            "These are direct state-change image tools.",
            "If the tool result shows success, stop immediately.",
            "Do not repeat image creation or modification to verify it unless the system explicitly reports failure.",
            "Be verbose in your prompt, explaining every detail of the image.",
        ),
        syntax=("[CREATE_IMAGE: 'a neon cyberpunk alley in the rain']",),
        auto_finish_on_success=True,
        success_prefixes=("Image saved to:",),
    ),
    ToolSpec(
        name="MODIFY_IMAGE",
        domain="IMAGE_WORK",
        requires_arg=True,
        runtime_handler=None,
        description="Modify the last generated image using a text instruction.",
        rules=(
            "Use only when the user explicitly wants an image created or modified.",
            "Do not use for text files, charts, workflows, or reports.",
            "Put the entire tool call inside the tool field as a single string.",
            "These are direct state-change image tools.",
            "If the tool result shows success, stop immediately.",
        ),
        syntax=("[MODIFY_IMAGE: 'remove the background person and change the jacket to black']",),
        auto_finish_on_success=True,
        success_prefixes=("Edited image saved to:",),
    ),
    ToolSpec(
        name="BROWSER_OP",
        domain="COMPUTER_USE",
        requires_arg=True,
        runtime_handler="exec_browser_op",
        description="Use for structured browser automation inside Piper's dedicated browser session.",
        rules=(
            "Use BROWSER_OP only for COMPUTER_USE stages.",
            "BROWSER_OP payload must be valid JSON inside a BROWSER_OP block.",
            "Prefer one concrete browser action per step.",
            "Use deterministic selectors first: role/name, label, id, name, or data-testid before brittle CSS/XPath fallbacks.",
            "When the active COMPUTER_USE stage names a requested topic but not a precise selector, use extract_text with a generic page selector plus a structured topic field instead of scraping the whole body blindly.",
            "When the active COMPUTER_USE stage names a download target hint, prefer click or download targets whose selector/text/href matches that hint before broad navigation.",
            "If the stage requires saving an artifact and the page already exposes the download control, prefer action download over a generic click.",
            "When navigating to http or https URLs, include allowed_domains so runtime scope can be enforced.",
            "Use capture_state or extract_text to verify browser state instead of narrating success from intent alone.",
            "If runtime reports a scope block, login wall, CAPTCHA, or unexpected dialog, stop and report the blocker honestly.",
            "Do not attempt purchases, payment submission, password-manager interaction, or MFA/CAPTCHA completion.",
        ),
        syntax=(
            '[BROWSER_OP]\n{"action":"goto_url","url":"https://example.com","allowed_domains":["example.com"]}\n[/BROWSER_OP]',
            '[BROWSER_OP]\n{"action":"click","selector":"#next-link"}\n[/BROWSER_OP]',
            '[BROWSER_OP]\n{"action":"type_text","selector":"#email","text":"alice@example.com"}\n[/BROWSER_OP]',
            '[BROWSER_OP]\n{"action":"extract_text","selector":"#status"}\n[/BROWSER_OP]',
            '[BROWSER_OP]\n{"action":"extract_text","selector":"body","topic":"warranty disclaimer"}\n[/BROWSER_OP]',
            '[BROWSER_OP]\n{"action":"download","text":"quarterly report","download_dir":"browser_downloads"}\n[/BROWSER_OP]',
            '[BROWSER_OP]\n{"action":"wait_for","selector":"#destination"}\n[/BROWSER_OP]',
            '[BROWSER_OP]\n{"action":"go_back"}\n[/BROWSER_OP]',
            '[BROWSER_OP]\n{"action":"download","selector":"#download-link","download_dir":"browser_downloads"}\n[/BROWSER_OP]',
            '[BROWSER_OP]\n{"action":"capture_state"}\n[/BROWSER_OP]',
        ),
    ),
    ToolSpec(
        name="LIST_KNOWLEDGE",
        domain="MEMORY_WORK",
        requires_arg=False,
        runtime_handler="exec_list_knowledge",
        description="List the stored world-model facts and relationships about the user.",
        rules=(
            "Only use when the user explicitly asks to store, remove, or retrieve knowledge.",
            "Do not invent argument formats.",
            "These are direct lookup or state-change memory tools.",
            "If the tool result clearly succeeds, stop immediately.",
            "Do not repeat successful memory actions to verify them.",
        ),
        syntax=("[LIST_KNOWLEDGE]",),
    ),
    ToolSpec(
        name="UPDATE_KNOWLEDGE",
        domain="MEMORY_WORK",
        requires_arg=True,
        runtime_handler="exec_update_knowledge",
        description="Store a durable user/world-model fact.",
        rules=(
            "Only use when the user explicitly asks to store, remove, or retrieve knowledge.",
            "Do not invent argument formats.",
            "These are direct lookup or state-change memory tools.",
            "If the tool result clearly succeeds, stop immediately.",
            "Do not repeat successful memory actions to verify them.",
        ),
        syntax=("[UPDATE_KNOWLEDGE: favorite_drink = coffee]",),
        auto_finish_on_success=True,
        success_prefixes=("System confirmation:",),
    ),
    ToolSpec(
        name="REMOVE_KNOWLEDGE",
        domain="MEMORY_WORK",
        requires_arg=True,
        runtime_handler="exec_remove_knowledge",
        description="Forget a durable world-model fact or related entity.",
        rules=(
            "Only use when the user explicitly asks to store, remove, or retrieve knowledge.",
            "Do not invent argument formats.",
            "These are direct lookup or state-change memory tools.",
            "If the tool result clearly succeeds, stop immediately.",
            "Do not repeat successful memory actions to verify them.",
        ),
        syntax=("[REMOVE_KNOWLEDGE: favorite_drink]",),
        auto_finish_on_success=True,
        success_prefixes=("Knowledge removed:",),
    ),
    ToolSpec(
        name="ADD_TASK",
        domain="TASK_EVENT_WORK",
        requires_arg=True,
        runtime_handler="exec_add_task",
        description="Add an undated pending task.",
        rules=(
            "Use only for undated to-do items.",
            "If the request includes a due date, occurrence date, birthday, appointment date, or deadline, use ADD_EVENT instead.",
            "Do not use for normal conversation.",
            "Do not invent parameter formats.",
            "These are direct state-change tools.",
            "If the action succeeds, stop immediately.",
            "Do not repeat the same task/event operation to verify it.",
        ),
        syntax=("[ADD_TASK: buy milk]",),
        auto_finish_on_success=True,
        success_prefixes=("Task added:",),
    ),
    ToolSpec(
        name="DELETE_TASK",
        domain="TASK_EVENT_WORK",
        requires_arg=True,
        runtime_handler="exec_delete_task",
        description="Delete a task without treating it as completed.",
        rules=(
            "Use for cleanup, mistakes, duplicates, or canceled tasks.",
            "Do not use for normal conversation.",
            "Do not invent parameter formats.",
            "These are direct state-change tools.",
            "If the action succeeds, stop immediately.",
            "Do not repeat the same task/event operation to verify it.",
        ),
        syntax=("[DELETE_TASK: buy milk]",),
        auto_finish_on_success=True,
        success_prefixes=("Task deleted:",),
    ),
    ToolSpec(
        name="COMPLETE_TASK",
        domain="TASK_EVENT_WORK",
        requires_arg=True,
        runtime_handler="exec_complete_task",
        description="Mark a task as completed, remove it from active tasks, and archive the completion as memory.",
        rules=(
            "Use when the user says they finished, did, handled, bought, submitted, or otherwise completed a task.",
            "Prefer this over DELETE_TASK when the task was actually accomplished.",
            "You may optionally append an outcome note after '=>' or '|'.",
            "These are direct state-change tools.",
            "If the action succeeds, stop immediately.",
            "Do not repeat the same completion operation to verify it.",
        ),
        syntax=("[COMPLETE_TASK: buy milk]", "[COMPLETE_TASK: buy Volvo => bought a BMW instead]"),
        auto_finish_on_success=True,
        success_prefixes=("Task completed and archived:",),
    ),
    ToolSpec(
        name="ADD_EVENT",
        domain="TASK_EVENT_WORK",
        requires_arg=True,
        runtime_handler="exec_add_event",
        description="Schedule a dated event or deadline.",
        rules=(
            "Use for anything with an occurrence date or deadline: birthdays, appointments, shifts, reminders by date, or due dates.",
            "If the request includes a date, deadline, or time reference, prefer ADD_EVENT over ADD_TASK.",
            "Do not use for normal conversation.",
            "Do not invent parameter formats.",
            "These are direct state-change tools.",
            "If the action succeeds, stop immediately.",
            "Do not repeat the same task/event operation to verify it.",
            "You may use YYYY-MM-DD or a simple relative phrase like tomorrow; runtime will resolve the date.",
        ),
        syntax=("[ADD_EVENT: Sarah birthday on 2026-11-20]", "[ADD_EVENT: buy milk on tomorrow]"),
        auto_finish_on_success=True,
        success_prefixes=("Event scheduled:",),
    ),
    ToolSpec(
        name="RESCHEDULE_EVENT",
        domain="TASK_EVENT_WORK",
        requires_arg=True,
        runtime_handler="exec_reschedule_event",
        description="Move an existing event to a new date, optionally with a new time.",
        rules=(
            "Use when the user postpones, moves, or reschedules an existing event.",
            "Prefer this over REMOVE_EVENT + ADD_EVENT for reschedule intent.",
            "Do not use for normal conversation.",
            "Do not invent parameter formats.",
            "These are direct state-change tools.",
            "If the action succeeds, stop immediately.",
            "Do not repeat the same reschedule operation to verify it.",
        ),
        syntax=(
            "[RESCHEDULE_EVENT: dentist appointment to 2026-03-27]",
            "[RESCHEDULE_EVENT: dentist appointment to next Friday at 14:00]",
        ),
        auto_finish_on_success=True,
        success_prefixes=("Event rescheduled:",),
    ),
    ToolSpec(
        name="REMOVE_EVENT",
        domain="TASK_EVENT_WORK",
        requires_arg=True,
        runtime_handler="exec_remove_event",
        description="Remove or cancel an event without treating it as completed.",
        rules=(
            "Use for canceled, mistaken, or unwanted events.",
            "Do not use for normal conversation.",
            "Do not invent parameter formats.",
            "These are direct state-change tools.",
            "If the action succeeds, stop immediately.",
            "Do not repeat the same task/event operation to verify it.",
        ),
        syntax=("[REMOVE_EVENT: Meeting]",),
        auto_finish_on_success=True,
        success_prefixes=("Event removed:",),
    ),
    ToolSpec(
        name="COMPLETE_EVENT",
        domain="TASK_EVENT_WORK",
        requires_arg=True,
        runtime_handler="exec_complete_event",
        description="Mark an event as completed, remove it from upcoming events, and archive the completion as memory.",
        rules=(
            "Use when the user says an event happened, was attended, was handled, or is done.",
            "Prefer this over REMOVE_EVENT when the event actually occurred.",
            "You may optionally append an outcome note after '=>' or '|'.",
            "These are direct state-change tools.",
            "If the action succeeds, stop immediately.",
            "Do not repeat the same completion operation to verify it.",
        ),
        syntax=("[COMPLETE_EVENT: dentist appointment]", "[COMPLETE_EVENT: Sarah birthday | bought flowers and visited]"),
        auto_finish_on_success=True,
        success_prefixes=("Event completed and archived:",),
    ),
    ToolSpec(
        name="LIST_TASKS",
        domain="TASK_EVENT_WORK",
        requires_arg=False,
        runtime_handler="exec_list_tasks",
        description="List pending tasks.",
        rules=(
            "Use when the user explicitly asks what tasks exist, or when you need to inspect task state.",
            "Prefer this over repeating ADD_TASK or DELETE_TASK just to inspect state.",
            "Do not mutate task state when inspection is enough.",
        ),
        syntax=("[LIST_TASKS]",),
        listed_in_domain=True,
    ),
    ToolSpec(
        name="LIST_EVENTS",
        domain="TASK_EVENT_WORK",
        requires_arg=False,
        runtime_handler="exec_list_events",
        description="List upcoming events.",
        rules=(
            "Use when the user explicitly asks what events exist, or when you need to inspect event state.",
            "Prefer this over repeating ADD_EVENT or REMOVE_EVENT just to inspect state.",
            "Do not mutate event state when inspection is enough.",
        ),
        syntax=("[LIST_EVENTS]",),
        listed_in_domain=True,
    ),
    ToolSpec(
        name="INSTALL_PACKAGE",
        domain="FILE_WORK",
        requires_arg=True,
        runtime_handler="exec_install_package",
        description="Install a Python package into the current environment.",
        rules=(
            "Use only after an explicit module import error or a system hint that a third-party dependency is missing.",
            "Do not use for standard-library modules.",
            "Install one package at a time, then retry the original action.",
        ),
        syntax=("[INSTALL_PACKAGE: duckduckgo-search]",),
        listed_in_domain=False,
    ),
)

_TOOL_MAP: Dict[str, ToolSpec] = {spec.name: spec for spec in _TOOL_SPECS}

LEGACY_TAGS: Tuple[str, ...] = (
    "ANSWER",
    "REASON",
    "READ",
    "WRITE",
    "APPEND",
    "LIST_FILES",
    "DELETE_FILE",
    "COMPLETE_TASK",
    "COMPLETE_EVENT",
    "ROUTER",
    "SAVE",
    "LOAD",
    "CONTROLLER",
)


_FAILURE_MARKERS: Tuple[str, ...] = (
    "error",
    "failed",
    "traceback",
    "exception",
    "denied",
    "blocked",
    "not found",
    "invalid format",
)


def get_tool_spec(name: str) -> Optional[ToolSpec]:
    if not name:
        return None
    return _TOOL_MAP.get(name.upper())


def iter_tool_specs(*, domain: Optional[str] = None, listed_only: bool = False) -> Iterable[ToolSpec]:
    for spec in _TOOL_SPECS:
        if domain and spec.domain != domain:
            continue
        if listed_only and not spec.listed_in_domain:
            continue
        yield spec


def resolve_domain_tools(domain_type: str) -> List[str]:
    return [spec.name for spec in iter_tool_specs(domain=domain_type, listed_only=True)]


def get_registered_tool_names(*, include_legacy: bool = False) -> List[str]:
    names = [spec.name for spec in _TOOL_SPECS]
    if include_legacy:
        names.extend(LEGACY_TAGS)
    return names


def tool_result_is_success(spec: Optional[ToolSpec], result) -> bool:
    if spec is None:
        return False
    if isinstance(result, dict):
        status = str(result.get("status", "")).upper()
        if status in {"FAILED", "BLOCKED"}:
            return False
        text = str(result.get("summary", "")).strip()
        if spec.name in {"RUN_CODE", "FILE_OP"}:
            return status == "EXECUTED"
    else:
        text = (result or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(marker in lower for marker in _FAILURE_MARKERS):
        return False
    if spec.success_prefixes:
        return any(text.startswith(prefix) for prefix in spec.success_prefixes)
    return True


def render_stage_guide(domain_type: str, allowed_tools: Iterable[str] | None = None) -> str:
    allowed = {str(name).upper() for name in (allowed_tools or []) if str(name).strip()}
    specs = list(iter_tool_specs(domain=domain_type, listed_only=True))
    if allowed:
        seen = {spec.name for spec in specs}
        for name in allowed:
            spec = get_tool_spec(name)
            if spec and spec.name not in seen:
                specs.append(spec)
                seen.add(spec.name)
    return _render_specs(domain_type, specs)


def _render_specs(domain_type: str, specs: List[ToolSpec]) -> str:
    if not specs:
        return f"No documentation found for domain: {domain_type}"

    lines: List[str] = [f"## DOMAIN: {domain_type}", "", "### TOOLS", ""]
    lines.extend(spec.name for spec in specs)
    lines.extend(["", "### DESCRIPTION", ""])

    unique_descriptions: List[str] = []
    for spec in specs:
        if spec.description not in unique_descriptions:
            unique_descriptions.append(spec.description)
    lines.extend(unique_descriptions)

    all_rules: List[str] = []
    for spec in specs:
        for rule in spec.rules:
            if rule not in all_rules:
                all_rules.append(rule)
    if all_rules:
        lines.extend(["", "### RULES", ""])
        lines.extend(f"- {rule}" for rule in all_rules)

    examples: List[str] = []
    for spec in specs:
        for item in spec.syntax:
            if item not in examples:
                examples.append(item)
    if examples:
        lines.extend(["", "### SYNTAX", ""])
        lines.extend(examples)

    return "\n".join(lines).strip()
