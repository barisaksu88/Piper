# CONTRACT — Provider Cancellation (I01)
# - Expose stop(handle) to terminate the underlying process.
# - Ensure cleanup of child processes/handles.
# - Must be safe to call multiple times; idempotent.

"""llamacpp provider — REAL shell adapter for LLM06.2 (llama-run positional args)
Adapts to your binary's usage:
  Usage: llama-run [options] model [prompt]
We pass: <exe> <model> <prompt> and only supported flags (--context-size, --threads).
Timeouts handled in services.llm_client.
"""
from __future__ import annotations
import os
import subprocess
from typing import List, Iterator, Optional
from dataclasses import dataclass
import threading, time, signal

class LlamaRunConfigError(RuntimeError):
    pass


def _env(name: str, *, required: bool = False, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if required and (v is None or not str(v).strip()):
        raise LlamaRunConfigError(f"missing env: {name}")
    return "" if v is None else str(v)


def _build_argv(prompt: str) -> List[str]:
    exe = _env("PIPER_LLAMA_EXE", required=True)  # e.g., C:\Piper\llama.cpp\llama-run.exe
    model = _env("PIPER_LLAMA_MODEL", required=True)  # e.g., C:\Piper\llama.cpp\Meta-Llama-3-8B-Instruct.Q5_K_M.gguf
    ctx = int(_env("PIPER_LLAMA_CTX", default="2048"))
    threads = _env("PIPER_LLAMA_THREADS", default="auto").strip()

    argv: List[str] = [exe, model]
    if prompt:
        argv.append(prompt)
    # Supported flags for your binary
    if ctx:
        argv += ["--context-size", str(ctx)]
    if threads and threads.lower() != "auto":
        argv += ["--threads", threads]
    return argv


def generate(user_text: str, persona: str | None = None) -> str:
    prompt = user_text or ""
    argv = _build_argv(prompt)
    try:
        proc = subprocess.run(
            argv,  # no shell=True
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"llama.cpp exec not found: {argv[0]}") from e
    except LlamaRunConfigError:
        raise
    except Exception as e:
        raise RuntimeError(f"llama.cpp failed to start: {e}") from e

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"llama.cpp exited {proc.returncode}: {err[:400]}")

    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("llama.cpp produced no output")
    return out

# ---------------- I01.2: streaming handle + stop ------------------------------

@dataclass
class LlamaHandle:
    proc: subprocess.Popen
    started_ms: int
    _stopped: bool = False


def start_stream(prompt: str) -> LlamaHandle:
    argv = _build_argv(prompt)
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            universal_newlines=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"llama.cpp exec not found: {argv[0]}") from e
    except LlamaRunConfigError:
        raise
    except Exception as e:
        raise RuntimeError(f"llama.cpp failed to start: {e}") from e
    return LlamaHandle(proc=proc, started_ms=int(time.time()*1000))


def iter_chunks(handle: LlamaHandle, *, chunk_bytes: int = 64) -> Iterator[str]:
    """Yield small text chunks from llama.cpp stdout while running.
    Uses a byte-size boundary to keep latency low.
    """
    if handle.proc.stdout is None:
        return
    buf = []
    while True:
        ch = handle.proc.stdout.read(1)
        if ch == "" and handle.proc.poll() is not None:
            if buf:
                yield "".join(buf)
            return
        if not ch:
            time.sleep(0.01)
            continue
        buf.append(ch)
        if len("".join(buf)) >= chunk_bytes or ch.isspace():
            out = "".join(buf)
            buf = []
            if out:
                yield out


def stop(handle: Optional[LlamaHandle]) -> None:
    """Terminate the underlying process idempotently."""
    if handle is None or handle._stopped:
        return
    handle._stopped = True
    proc = handle.proc
    if proc.poll() is not None:
        return
    try:
        if os.name == 'nt':
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=0.8)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.kill()
        try:
            proc.wait(timeout=0.7)
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        pass
