"""core/style_sheet.py

External style sheets for Piper (character styles).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List, Dict

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class StyleSheet:
    name: str = "default"
    temperature: Optional[float] = None
    tts_voice: Optional[str] = None
    tts_speed: Optional[float] = None
    knowledge: bool = True
    overlay: str = ""
    bootstrap: Tuple[Dict[str, str], ...] = ()


def _split_sections(text: str) -> Tuple[str, str, str]:
    """Return (header, overlay, bootstrap_text)."""
    lines = text.splitlines()
    head: List[str] = []
    overlay: List[str] = []
    boot: List[str] = []

    mode = "head"
    for ln in lines:
        s = ln.strip()
        if mode == "head" and s == "---":
            mode = "overlay"
            continue
        if mode == "overlay" and s == "---BOOTSTRAP---":
            mode = "boot"
            continue

        if mode == "head":
            head.append(ln)
        elif mode == "overlay":
            overlay.append(ln)
        else:
            boot.append(ln)

    return "\n".join(head), "\n".join(overlay), "\n".join(boot)


def _parse_bootstrap(boot_text: str) -> Tuple[Dict[str, str], ...]:
    out: List[Dict[str, str]] = []
    if not boot_text:
        return ()

    for raw in boot_text.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        if ":" not in ln:
            continue
        role, content = ln.split(":", 1)
        role = role.strip().lower()
        content = content.strip()
        if role not in ("system", "user", "assistant"):
            continue
        if not content:
            continue
        out.append({"role": role, "content": content})

    return tuple(out)


def parse_style_sheet(text: str) -> StyleSheet:
    head, overlay_text, boot_text = _split_sections(text)

    name = "default"
    temperature: Optional[float] = None
    tts_voice: Optional[str] = None
    tts_speed: Optional[float] = None
    knowledge: bool = True

    for raw in head.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k = k.strip().lower()
        v = v.strip()

        if k == "name":
            if v:
                name = v
        elif k == "temperature":
            try:
                temperature = float(v)
            except Exception:
                pass
        elif k in ("tts_voice", "voice"):
            tts_voice = v or None
        elif k in ("tts_speed", "speed"):
            try:
                tts_speed = float(v)
            except Exception:
                pass
        elif k == "knowledge":
            if v.lower() in ("false", "0", "no", "off"):
                knowledge = False
            else:
                knowledge = True

    overlay = (overlay_text or "").strip()
    bootstrap = _parse_bootstrap(boot_text)

    return StyleSheet(
        name=name,
        temperature=temperature,
        tts_voice=tts_voice,
        tts_speed=tts_speed,
        knowledge=knowledge,
        overlay=overlay,
        bootstrap=bootstrap,
    )


_DEFAULT_TEMPLATE = """# StyleSheet v1
name = default
temperature = {temperature}
tts_voice = {tts_voice}
tts_speed = {tts_speed}
knowledge = true

---

Tone: Neutral and controlled.
Keep answers concise.

---BOOTSTRAP---

system: Stay in the selected style.
user: Continue.
assistant: Understood.
"""


class StyleManager:
    """Loads a single active style sheet with mtime-based caching AND persistence."""

    def __init__(self, styles_dir: Path, active_filename: str = "default.style"):
        self.styles_dir = Path(styles_dir)
        self._cache_path: Optional[Path] = None
        self._cache_mtime: Optional[float] = None
        self._cache_value: StyleSheet = StyleSheet()
        
        # --- Persistence Logic ---
        self._pref_file = self.styles_dir / "active_style.txt"
        
        # 1. Try to load saved preference
        saved = self._load_preference()
        if saved:
            self.active_filename = saved
        else:
            # 2. Fallback to constructor arg (default)
            self.active_filename = active_filename

    def _load_preference(self) -> Optional[str]:
        if self._pref_file.exists():
            try:
                content = self._pref_file.read_text(encoding="utf-8").strip()
                if content:
                    return content
            except Exception:
                pass
        return None

    def save_preference(self):
        try:
            self.styles_dir.mkdir(parents=True, exist_ok=True)
            self._pref_file.write_text(self.active_filename, encoding="utf-8")
            _LOG.debug("[Style] Saved preference: %s to %s", self.active_filename, self._pref_file)
        except Exception as e:
            _LOG.warning("[Style] Error saving preference: %s", e)

    @property
    def active_path(self) -> Path:
        return self.styles_dir / self.active_filename

    def ensure_default_exists(self, *, temperature: float, tts_voice: str, tts_speed: float) -> None:
        self.styles_dir.mkdir(parents=True, exist_ok=True)
        p = self.active_path
        if p.exists():
            return
        p.write_text(
            _DEFAULT_TEMPLATE.format(
                temperature=float(temperature),
                tts_voice=str(tts_voice),
                tts_speed=float(tts_speed),
            ),
            encoding="utf-8",
        )

    def load(self, default_temperature: float, default_tts_voice: str, default_tts_speed: float) -> StyleSheet:
        """Load active style. If missing/invalid, returns a safe default."""
        try:
            self.ensure_default_exists(
                temperature=default_temperature,
                tts_voice=default_tts_voice,
                tts_speed=default_tts_speed,
            )

            p = self.active_path
            mtime = p.stat().st_mtime

            if self._cache_path == p and self._cache_mtime == mtime:
                return self._cache_value

            txt = p.read_text(encoding="utf-8", errors="replace")
            ss = parse_style_sheet(txt)

            self._cache_path = p
            self._cache_mtime = mtime
            self._cache_value = ss
            return ss

        except Exception:
            return StyleSheet(
                name="default",
                temperature=default_temperature,
                tts_voice=default_tts_voice,
                tts_speed=default_tts_speed,
                knowledge=True,
                overlay="",
                bootstrap=(),
            )
