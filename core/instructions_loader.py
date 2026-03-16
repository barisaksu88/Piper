from __future__ import annotations

from pathlib import Path


class InstructionLoader:
    def __init__(self, path: Path, *, max_chars: int = 12000) -> None:
        self.path = Path(path)
        self.max_chars = int(max_chars)
        self._cache = ""
        self._cache_mtime: float | None = None

    def load(self) -> str:
        try:
            mtime = self.path.stat().st_mtime if self.path.exists() else None
        except Exception:
            mtime = None

        if self._cache_mtime == mtime:
            return self._cache

        text = ""
        try:
            if self.path.exists():
                text = self.path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            text = ""

        if self.max_chars > 0 and len(text) > self.max_chars:
            text = text[: self.max_chars].rstrip()

        self._cache = text
        self._cache_mtime = mtime
        return self._cache
