# CONTRACT — llama.cpp Adapter
# - Accept argv from client/config: --context-size, --threads, --ngl, temperature, top_k, top_p.
# - Start/iterate/stop stable on Windows.
# - No UI imports; no persona/memory logic.

import os
import subprocess
import threading
import queue
from typing import Iterator, Optional, List

class LlamaCppProcess:
    def __init__(self, exe: str, model_path: str, ctx: int, threads: int, ngl: int,
                 temperature: float, top_k: int, top_p: float, extra_args: Optional[List[str]] = None):
        self.exe = exe or r"C:\Piper\llama.cpp\llama-run"
        self.model_path = model_path
        self.ctx = ctx
        self.threads = threads
        self.ngl = ngl
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.extra_args = extra_args or []

        self.proc: Optional[subprocess.Popen] = None
        self._stop_evt = threading.Event()
        self._q: "queue.Queue[str]" = queue.Queue()

    def start(self, prompt: str) -> None:
        args = [
            self.exe,
            "--model", self.model_path,
            "--ctx-size", str(self.ctx),
            "--threads", str(self.threads),
            "--ngl", str(self.ngl),
            "--temp", str(self.temperature),
            "--top-k", str(self.top_k),
            "--top-p", str(self.top_p),
            "--prompt-cache", "none",
            "--prompt", prompt,
            "--no-mmap",  # safer on Windows for some builds
            "--no-kv-offload"  # explicit; adjust if your build differs
        ] + self.extra_args

        # Use text mode for incremental reads
        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=1,
            universal_newlines=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        t = threading.Thread(target=self._pump_stdout, daemon=True)
        t.start()

    def _pump_stdout(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            if self._stop_evt.is_set():
                break
            # llama.cpp runners differ; most stream tokens or lines.
            chunk = line.rstrip("\r\n")
            if chunk:
                self._q.put(chunk)
        self._q.put(None)  # sentinel

    def stream(self) -> Iterator[str]:
        while True:
            item = self._q.get()
            if item is None:
                break
            yield item

    def stop(self):
        self._stop_evt.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
