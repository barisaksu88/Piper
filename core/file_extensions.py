"""core/file_extensions.py

Canonical code-file extension set shared across core modules.

Having a single definition site here prevents the extension set from
diverging when new types are added (e.g. .svelte, .vue, etc.).

No imports — this is a leaf module intentionally kept free of all
dependencies so it can be imported anywhere without circular-import risk.
"""

from __future__ import annotations

CODE_FILE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".bat",
        ".c",
        ".cpp",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".php",
        ".ps1",
        ".py",
        ".rb",
        ".rs",
        ".scss",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".xml",
        ".yaml",
        ".yml",
    }
)
