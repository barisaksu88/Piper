"""services/input_bridge_cli.py — IB01 (stdin pipeline stub)

Runbook alignment:
- Attach mode is the source of truth.
- Embedded CLI is opt-in via env PIPER_UI_EMBED_CLI=1.

IB01 scope (no routing yet):
- Provide a tiny, safe bridge that can read stdin lines in a background thread
  and expose a .send() that writes to stdout when in attach mode.
- No GUI imports; stdlib only. Designed to be wired from UI in IB02.

Usage sketch (IB02 will do this):

    from services.input_bridge_cli import InputBridgeCLI

    bridge = InputBridgeCLI(on_line=lambda s: print(f"CLI> {s}"))
    bridge.start()
    bridge.send("hello from GUI")
    ...
    bridge.stop()

"""
from __future__ import annotations
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Union


@dataclass
class BridgeConfig:
    mode: str = field(default_factory=lambda: ("embedded" if os.environ.get("PIPER_UI_EMBED_CLI", "").strip() == "1" else "attach"))
    encoding: str = "utf-8"
    line_sep: str = "\n"


class InputBridgeCLI:
    """Minimal stdin/stdout bridge with **event-driven error reporting**.

    - In **attach mode**, we DO NOT read from sys.stdin (avoids stealing Dev Tools input).
      We only use `send()` to write to stdout for the external CLI/MC to consume.
      Errors are reported only if a write fails or via an explicit test flag.
    - In **embedded mode** (opt-in via `PIPER_UI_EMBED_CLI=1`), we spawn a reader
      thread that consumes stdin and invokes callbacks.

    Event-driven banner wiring: provide `on_error(exc)` to be called when the reader
    actually fails (embedded mode) or when a forced test-fail is set.
    """

    def __init__(self, on_line: Optional[Callable[[str], None]] = None, on_error: Optional[Callable[[BaseException], None]] = None, config: Optional[BridgeConfig] = None):
        self.config = config or BridgeConfig()
        self._on_line = on_line or (lambda _s: None)
        self._on_error = on_error or (lambda _e: None)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_error: Optional[BaseException] = None

    # ---------------- lifecycle ----------------
    def start(self) -> None:
        """Start the reader in embedded mode; attach stays send-only.
        Also supports `PIPER_BRIDGE_TEST_FAIL=1` to emit a synthetic error.
        """
        # Synthetic failure for deterministic banner tests
        try:
            if os.environ.get("PIPER_BRIDGE_TEST_FAIL", "").strip() == "1":
                exc = RuntimeError("bridge test fail (synthetic)")
                self._last_error = exc
                try:
                    self._on_error(exc)
                except Exception:
                    pass
                return
        except Exception:
            pass

        if self.config.mode != "embedded":
            # Attach mode: no reader; send-only
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, name="InputBridgeCLI", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout: float = 0.5) -> None:
        try:
            self._stop.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=join_timeout)
        except Exception as exc:
            self._last_error = exc
            try:
                self._on_error(exc)
            except Exception:
                pass

    # ---------------- I/O ----------------
    def send(self, text: str) -> bool:
        """Send a line to the CLI peer. Returns True if written/queued.
        In attach mode this writes to stdout; any error triggers `on_error`.
        """
        try:
            if self.config.mode == "attach":
                data = (text or "") + self.config.line_sep
                sys.stdout.write(data)
                sys.stdout.flush()
                return True
            # Embedded mode: no-op for now; IB02/IB03 may route internally
            return False
        except Exception as exc:
            self._last_error = exc
            try:
                self._on_error(exc)
            except Exception:
                pass
            return False

    # ---------------- state ----------------
    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    # ---------------- internals ----------------
    def _reader_loop(self) -> None:
        enc = self.config.encoding  # local copy to avoid attribute lookups in loop
        try:
            while not self._stop.is_set():
                line = sys.stdin.readline()
                if line == "":
                    # EOF or detached pipe; back off then continue until stopped
                    self._stop.wait(0.05)
                    continue
                try:
                    if isinstance(line, bytes):
                        line = line.decode(enc, errors="ignore")
                except Exception:
                    pass
                self._on_line(line.rstrip("\r\n"))
        except Exception as exc:
            self._last_error = exc
