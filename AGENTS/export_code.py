import os
from pathlib import Path

# Configuration
ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = Path(__file__).resolve().parent / "PIPER_CODE_DUMP.txt"

# Folders to ignore completely
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

# Specific files to exclude
EXCLUDE_FILES = {
    # boot / infra
    "llm/boot.py",
    "llm/llm_server_client.py",

    # memory infrastructure
    "memory/storage.py",
    "memory/stores.py",
    "memory/codex_repair_store.py",

    # UI visual markup
    "ui/layout.py",
    "ui/windowing.py",

    # workspace tool internals
    "tools/workspace_extension_actions.py",
    "tools/workspace_extension_ops.py",
    "tools/workspace_file_actions.py",
    "tools/workspace_mutation_actions.py",
    "tools/workspace_query_actions.py",
    "tools/workspace_runtime.py",
    "tools/file_ops.py",

    # prompt files
    "memory/knowledge_prompts.py",
    "memory/world_model_prompts.py",
}

# Extensions to include
INCLUDE_EXTENSIONS = {".py", ".txt", ".style", ".json", ".jinja"}


def gather_files():
    print(f"Scanning {ROOT_DIR}...")
    collected_files = []

    for root, dirs, files in os.walk(ROOT_DIR):
        # Remove ignored directories
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for file in files:
            file_path = Path(root) / file

            # skip __init__.py
            if file == "__init__.py":
                continue

            if file_path.suffix not in INCLUDE_EXTENSIONS:
                continue

            relative = file_path.relative_to(ROOT_DIR).as_posix()

            # skip specific excluded files
            if relative in EXCLUDE_FILES:
                continue

            # skip output file itself
            if file_path.name == OUTPUT_FILE.name:
                continue

            collected_files.append(file_path)

    collected_files.sort(key=lambda x: (x.parent.name, x.name))
    return collected_files


def write_dump(files):
    print(f"Writing {len(files)} files to {OUTPUT_FILE}...")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# PIPER CORE CODEBASE DUMP\n")
        f.write("# Reduced dump for architecture / logic debugging\n\n")

        for file_path in files:
            relative_path = file_path.relative_to(ROOT_DIR)

            print(f"  - Adding: {relative_path}")

            f.write(f"{'=' * 60}\n")
            f.write(f"FILE: {relative_path}\n")
            f.write(f"{'=' * 60}\n")

            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as src:
                    content = src.read()
                    f.write(content)
                    if not content.endswith("\n"):
                        f.write("\n")
            except Exception as e:
                f.write(f"[ERROR READING FILE: {e}]\n")

            f.write("\n\n")

    print("\nDone! Copy PIPER_CODE_DUMP.txt into the new thread.")


if __name__ == "__main__":
    files = gather_files()
    write_dump(files)