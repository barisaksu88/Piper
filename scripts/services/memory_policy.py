# CONTRACT — Memory Policy v1
# - summarize_recent(turns) -> str
#   (token-bounded, deterministic summary of last N turns)
# - choose_write(summary) -> bool
#   (simple threshold gate; SAFE_MODE conservative)
# Forbidden:
# - Embeddings
# - External APIs or internet
# - Blocking calls into LLM providers

from __future__ import annotations
from typing import List, Dict
import os

# --- Deterministic knobs (env-overridable) ------------------------------------
# How many most-recent turns to consider
_SUM_TURNS = int(os.getenv("PIPER_MEM_SUMMARY_TURNS", "6") or 6)
# Max bytes to write (ASCII-bound) — keep tiny to ensure sync I/O stays fast
_SUM_MAX_CHARS = int(os.getenv("PIPER_MEM_SUMMARY_MAX", "200") or 200)
# Minimum length to consider writing an episode
_MIN_WRITE_LEN = int(os.getenv("PIPER_MEM_MINLEN", "50") or 50)
# Hard guard (0 disables writes)
_WRITE_ENABLE = os.getenv("PIPER_MEM_WRITE", "1").strip() not in {"0", "false", "no"}

# --- Helpers ------------------------------------------------------------------

def _coerce_turn(t: Dict) -> str:
    """Deterministically stringify a single turn dict (role + text only)."""
    role = str(t.get("role", "")).strip()
    text = str(t.get("text", "")).replace("\n", " ").replace("\r", " ")
    # Clamp each piece to keep total deterministic; leave head-bias
    if len(role) > 16:
        role = role[:16]
    if len(text) > 160:
        text = text[:160]
    return f"{role}: {text}".strip()

# --- Public API ---------------------------------------------------------------

def select_for_recall(episodes: List[Dict], budget_tokens: int) -> List[Dict]:
    """Recency-only selector within a token budget.

    - Packs the most recent episodes first (by `ts` descending if present),
      until an approximate token budget is reached.
    - If the budget is exceeded, we drop the *oldest* among the chosen set
      (so the newest facts are retained).
    - Deterministic, no embeddings or external calls."""
    if not episodes or budget_tokens <= 0:
        return []
    # Heuristic chars-per-token; deterministic and overrideable
    try:
        cpt = int(os.getenv("PIPER_MEM_AVG_CHARS_PER_TOKEN", "4") or 4)
    except Exception:
        cpt = 4
    max_chars = max(1, budget_tokens * max(1, cpt))

    # Sort by recency (most recent first) when `ts` exists; otherwise preserve order
    def _ts(e):
        try:
            return int(e.get("ts", 0))
        except Exception:
            return 0
    eps = sorted(list(episodes), key=_ts, reverse=True)

    chosen: List[Dict] = []
    total = 0
    for e in eps:
        s = str(e.get("summary", ""))
        if not s:
            continue
        # +1 for newline separator we'll add in the preamble
        add_len = len(s) + 1
        if not chosen:
            # always allow the newest episode in, even if it alone exceeds budget
            chosen.append(e)
            total += add_len
            continue
        if total + add_len <= max_chars:
            chosen.append(e)
            total += add_len
        else:
            # would exceed; stop packing (newest-first)
            break

    # If we somehow exceeded (e.g., first was already huge), trim oldest first
    while chosen and total > max_chars:
        oldest = chosen[-1]
        total -= (len(str(oldest.get("summary", ""))) + 1)
        chosen.pop()

    return chosen

def summarize_recent(turns: List[Dict]) -> str:
    """Return a deterministic ≤_SUM_MAX_CHARS summary of the last _SUM_TURNS.

    Policy: concatenate compact "role: text" lines for the last N turns, then
    truncate to the byte/char budget. No randomness, no model calls."""
    if not turns:
        return ""
    # Take last N deterministically
    tail = turns[-_SUM_TURNS:]
    parts = []
    for t in tail:
        parts.append(_coerce_turn(t))
    s = " | ".join(p for p in parts if p)
    if len(s) > _SUM_MAX_CHARS:
        s = s[:_SUM_MAX_CHARS]
    return s

def choose_write(summary: str) -> bool:
    """Gate writes conservatively in SAFE_MODE.

    - Disabled if PIPER_MEM_WRITE=0.
    - Otherwise write only if summary length ≥ _MIN_WRITE_LEN."""
    if not _WRITE_ENABLE:
        return False
    return bool(summary) and (len(summary) >= _MIN_WRITE_LEN)