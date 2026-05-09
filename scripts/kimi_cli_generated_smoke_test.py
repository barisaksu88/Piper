#!/usr/bin/env python3
"""Self-contained read-only smoke test for repo root, AGENTS.md, and scripts/ directory.

Exits 0 only when repo root exists and AGENTS.md and scripts/ exist.
"""

import json
import os
import sys


def main():
    # Determine repo root relative to this script (../../ from scripts/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(script_dir, ".."))

    cwd_exists = os.path.isdir(repo_root)
    agents_path = os.path.join(repo_root, "AGENTS.md")
    scripts_path = os.path.join(repo_root, "scripts")

    agents_exists = os.path.isfile(agents_path)
    scripts_exists = os.path.isdir(scripts_path)

    success = cwd_exists and agents_exists and scripts_exists

    result = {
        "success": success,
        "cwd_exists": cwd_exists,
        "agents_exists": agents_exists,
        "scripts_exists": scripts_exists,
        "python_version": ".".join(map(str, sys.version_info[:3])),
    }

    print(json.dumps(result, indent=2))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
