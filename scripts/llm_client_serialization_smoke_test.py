from __future__ import annotations

import json
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from llm.llm_server_client import LlamaServerClient, LlamaServerConfig


class _FakeResponse:
    def __init__(self, lines: list[bytes] | None = None, *, timeout_on_read: bool = False):
        self._lines = list(lines or [b"data: [DONE]\n\n"])
        self._timeout_on_read = timeout_on_read

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def readline(self) -> bytes:
        if self._timeout_on_read:
            raise socket.timeout("timed out")
        if self._lines:
            return self._lines.pop(0)
        return b""


def _content_chunk(text: str) -> bytes:
    return (
        "data: "
        + json.dumps(
            {
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": None,
                        "delta": {"content": text},
                    }
                ]
            }
        )
        + "\n\n"
    ).encode("utf-8")


def _reasoning_chunk(text: str) -> bytes:
    return (
        "data: "
        + json.dumps(
            {
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": None,
                        "delta": {"reasoning_content": text},
                    }
                ]
            }
        )
        + "\n\n"
    ).encode("utf-8")


def main() -> int:
    client = LlamaServerClient(LlamaServerConfig(base_url="http://example.invalid"))
    state = {
        "active": 0,
        "max_active": 0,
        "calls": 0,
        "payloads": [],
        "retry_ping_calls": 0,
    }
    state_lock = threading.Lock()

    @contextmanager
    def fake_urlopen(req, timeout):  # noqa: ANN001
        payload = json.loads(req.data.decode("utf-8"))
        last_user = str((payload.get("messages") or [{}])[-1].get("content") or "")
        with state_lock:
            state["active"] += 1
            state["calls"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            state["payloads"].append(payload)
            if "retry-ping" in last_user and "/no_think" not in last_user:
                state["retry_ping_calls"] += 1
        time.sleep(0.15)
        try:
            if "serial-ping" in last_user:
                yield _FakeResponse([_content_chunk('{"serial":true}'), b"data: [DONE]\n\n"])
            elif "retry-ping" in last_user and "/no_think" not in last_user:
                yield _FakeResponse([_reasoning_chunk("?"), b"data: [DONE]\n\n"])
            elif "retry-ping" in last_user and "/no_think" in last_user:
                yield _FakeResponse([_content_chunk('{"ok":true}'), b"data: [DONE]\n\n"])
            else:
                yield _FakeResponse(timeout_on_read=True)
        finally:
            with state_lock:
                state["active"] -= 1

    import urllib.request

    original_urlopen = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        barrier = threading.Barrier(2)
        errors: list[str] = []

        def run_call() -> None:
            try:
                barrier.wait(timeout=1.0)
                client.generate([{"role": "user", "content": "serial-ping"}], max_tokens=1)
            except Exception as exc:  # pragma: no cover - smoke assertion surface
                errors.append(str(exc))

        threads = [threading.Thread(target=run_call) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)

        empty_retry_result = client.generate(
            [{"role": "user", "content": "retry-ping"}],
            max_tokens=1,
        )

        timeout_error = ""
        try:
            list(client.generate_stream([{"role": "user", "content": "timeout-ping"}], max_tokens=1))
        except Exception as exc:  # pragma: no cover - smoke assertion surface
            timeout_error = str(exc)
    finally:
        urllib.request.urlopen = original_urlopen

    no_think_retry_ok = (
        empty_retry_result == '{"ok":true}'
        and state["retry_ping_calls"] == 1
        and any(str(payload["messages"][-1]["content"]).strip() == "retry-ping" for payload in state["payloads"])
        and any(str(payload["messages"][-1]["content"]).strip().endswith("retry-ping /no_think") for payload in state["payloads"])
    )
    timeout_guard_ok = "stream stalled" in timeout_error.lower()
    ok = (
        not errors
        and state["calls"] == 5
        and state["max_active"] == 1
        and state["active"] == 0
        and no_think_retry_ok
        and timeout_guard_ok
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "calls": state["calls"],
                "max_active": state["max_active"],
                "active": state["active"],
                "errors": errors,
                "no_think_retry_ok": no_think_retry_ok,
                "timeout_guard_ok": timeout_guard_ok,
            }
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
