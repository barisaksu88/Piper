import os
from pathlib import Path
import fnmatch

ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = ROOT_DIR / "PIPER_CODE_DUMP.txt"

# ---------- DIRECTORY EXCLUDES ----------
IGNORE_DIRS = {
    "venv",
    ".venv",
    ".vscode",
    "__pycache__",
    ".git",
    "data",
    "models",
    "harness",
    "runtime",
    "scripts",
    "versions",
}

# ---------- FILE NAME EXCLUDES ----------
EXCLUDE_FILENAMES = {
    "AGENTS.md",
    "app.py",
    "config.py",
    "export_code.py",
    "mirror_watcher.py",
    "mirror_watcher.log",
    "PIPER_CODE_DUMP.txt",
    "requirements.txt",
    "runtime_control.py",
    "start_piper.bat",
}

# ---------- PATH EXCLUDES ----------
EXCLUDE_PATHS = {
    "llm/boot.py",
    "llm/llm_server_client.py",

    "memory/storage.py",
    "memory/stores.py",
    "memory/codex_repair_store.py",

    "ui/layout.py",
    "ui/windowing.py",

    "tools/workspace_extension_actions.py",
    "tools/workspace_extension_ops.py",
    "tools/workspace_file_actions.py",
    "tools/workspace_mutation_actions.py",
    "tools/workspace_query_actions.py",
    "tools/workspace_runtime.py",
    "tools/file_ops.py",

    "memory/knowledge_prompts.py",
    "memory/world_model_prompts.py",

    # Additional exclusions requested
    "memory/brain.py",
    "memory/chat_state.py",
    "memory/documents.py",
    "memory/state_owner.py",
    "memory/transient_state.py",
    "memory/vision_session.py",
    "memory/world_model.py",

    "tools/image_gen.py",
    "tools/interpreter.py",
    "tools/live_screen.py",
    "tools/registry.py",
    "tools/screen_capture.py",
    "tools/search.py",
    "tools/stt.py",
    "tools/tts.py",
    "tools/vision.py",

    "core/skills/selector.py",

    "ui/event_speech.py",
    "ui/vision_commentary.py",
}

# ---------- PATTERN EXCLUDES ----------
EXCLUDE_PATTERNS = [
    "ui/controller*.py",
    "memory/knowledge_*.py",
]

# ---------- FILE TYPES TO KEEP ----------
INCLUDE_EXTENSIONS = {".py", ".txt", ".style", ".json", ".jinja"}


def should_exclude(file_path: Path):

    # skip __init__
    if file_path.name == "__init__.py":
        return True

    if file_path.name in EXCLUDE_FILENAMES:
        return True

    rel = file_path.relative_to(ROOT_DIR).as_posix()

    if rel in EXCLUDE_PATHS:
        return True

    for pattern in EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(rel, pattern):
            return True

    return False


def gather_files():
    print(f"Scanning {ROOT_DIR}")
    collected = []

    for root, dirs, files in os.walk(ROOT_DIR):

        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for file in files:

            file_path = Path(root) / file

            if file_path.suffix not in INCLUDE_EXTENSIONS:
                continue

            if should_exclude(file_path):
                continue

            if file_path.name == OUTPUT_FILE.name:
                continue

            collected.append(file_path)

    collected.sort(key=lambda x: (x.parent.name, x.name))
    return collected


def write_dump(files):

    print(f"Writing {len(files)} files to {OUTPUT_FILE}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:

        f.write("# PIPER LOGIC LAYER DUMP\n")
        f.write("# Reduced architecture export for LLM analysis\n\n")

        for file_path in files:

            rel = file_path.relative_to(ROOT_DIR)

            print("  +", rel)

            f.write("=" * 60 + "\n")
            f.write(f"FILE: {rel}\n")
            f.write("=" * 60 + "\n")

            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as src:
                    content = src.read()
                    f.write(content)
                    if not content.endswith("\n"):
                        f.write("\n")
            except Exception as e:
                f.write(f"[ERROR READING FILE: {e}]\n")

            f.write("\n\n")

    print("\nDone.")


if __name__ == "__main__":
    files = gather_files()
    write_dump(files)