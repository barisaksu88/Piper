# Config Files — Piper Project

These files are **tooling/meta configs**, not part of Piper’s runtime logic.

- **.pre-commit-config.yaml**
  Runs hygiene checks before `git commit`.
  Example: auto-format code (black, isort), lint, strip whitespace.

- **.editorconfig**
  Editor/IDE hints. Ensures consistent tabs/spaces, line endings, final newline.
  Works across VS Code, PyCharm, etc.

- **pyproject.toml**
  Central Python project metadata/config (PEP 518).
  Defines dependencies, build system, and tool configs.
  Consolidates configs that would otherwise be scattered.

---

## Important Distinction

These dotfiles are **developer tooling**.
They **do not** replace runtime constants like `ui/layout_constants.py` (which control Piper’s GUI geometry/theme).

Think of it as layers:
- **Tooling (dotfiles)** → control development environment.
- **Runtime (layout_constants.py, etc.)** → control Piper’s behavior and look.

---

## Placement

- These files **must stay at repo root** (i.e., `C:\Piper\scripts\`).
- Tools like `pre-commit`, editors, and Python packaging systems only detect them at the top level.
- Don’t move them into `docs/` or any subfolder — they won’t work there.
