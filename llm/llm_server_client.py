# core/llm_server_client.py

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
import urllib.request

from core.runtime_control import CancellationToken, OperationCancelled


class LLMClientError(Exception):
    pass


@dataclass
class LlamaServerConfig:
    base_url: str = "http://127.0.0.1:8080"
    model: str = "qwen"
    temperature: float = 0.7
    max_tokens: int = 512  # llama-server may ignore if not supported
    timeout_s: float = 300.0
    stream_read_timeout_s: float = 30.0

    # If set, we dump the *exact HTTP request payload* we send to llama-server,
    # plus a local rendering of the chat template (ChatML) for human inspection.
    debug_path: Optional[Path] = None

def _render_message_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")

    rendered: List[str] = []
    for part in content:
        if not isinstance(part, dict):
            rendered.append(str(part))
            continue

        part_type = str(part.get("type") or "").strip().lower()
        if part_type in {"text", "input_text"}:
            rendered.append(str(part.get("text") or ""))
            continue

        if part_type in {"image_url", "input_image"}:
            image_value = part.get("image_url")
            if isinstance(image_value, dict):
                image_url = str(image_value.get("url") or "")
            else:
                image_url = str(image_value or "")
            if image_url.startswith("data:"):
                rendered.append("[IMAGE: data-uri omitted]")
            elif image_url:
                rendered.append(f"[IMAGE: {image_url}]")
            else:
                rendered.append("[IMAGE]")
            continue

        if part_type == "input_file":
            rendered.append("[FILE]")
            continue

        rendered.append(f"[{part_type or 'content_part'}]")

    return "\n".join(part for part in rendered if part).strip()


def _render_chatml(messages: List[Dict[str, Any]]) -> str:
    out: List[str] = []

    for m in messages:
        role = (m.get("role") or "").strip().lower()
        if role not in ("system", "user", "assistant"):
            continue

        content = _render_message_content(m.get("content"))
        out.append(f"<|im_start|>{role}\n{content}<|im_end|>")

    # generation cue
    out.append("<|im_start|>assistant\n")

    return "\n".join(out)

def _append_debug_dump(
    *,
    debug_path: Path,
    url: str,
    payload: Dict[str, Any],
    rendered_prompt: str,
) -> None:
    try:
        debug_path.parent.mkdir(parents=True, exist_ok=True)

        with open(debug_path, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n\n" + ("=" * 88) + "\n")
            f.write("LLAMA-SERVER HTTP REQUEST\n")
            f.write(("=" * 88) + "\n")

            f.write(f"URL: {url}\n")

            f.write("\nPAYLOAD (JSON SENT TO SERVER):\n")
            f.write(json.dumps(payload, ensure_ascii=False, indent=2))
            f.write("\n")

            f.write(("-" * 88) + "\n")
            f.write("RENDERED_PROMPT (ChatML AS MODEL SEES IT):\n")
            f.write(rendered_prompt)
            if not rendered_prompt.endswith("\n"):
                f.write("\n")

            f.write(("=" * 88) + "\n")

    except Exception:
        # Debugging must never break generation
        pass

class LlamaServerClient:
    """OpenAI-compatible client for llama.cpp `llama-server`.

    Uses POST /v1/chat/completions.

    Streaming format is SSE:
      data: {json chunk with choices[0].delta.content}
      ...
      data: [DONE]
    """

    def __init__(self, cfg: LlamaServerConfig):
        self.cfg = cfg
        self._request_lock = threading.Lock()

    def _acquire_request_lock(self, cancel_token: CancellationToken | None = None) -> None:
        # llama.cpp shares KV/cache state across slots; overlapping Piper requests
        # can trip "Context size has been exceeded" even when each prompt is valid.
        while True:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            if self._request_lock.acquire(timeout=0.1):
                return

    @staticmethod
    def _messages_with_no_think_suffix(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        updated = [dict(item) for item in (messages or [])]
        for idx in range(len(updated) - 1, -1, -1):
            if str(updated[idx].get("role") or "").strip().lower() != "user":
                continue
            content = str(updated[idx].get("content") or "")
            if "/no_think" in content:
                return updated
            updated[idx]["content"] = content.rstrip() + " /no_think"
            return updated
        return updated

    @staticmethod
    def _set_read_timeout(resp, timeout_s: float) -> None:
        try:
            sock = getattr(getattr(resp, "fp", None), "raw", None)
            sock = getattr(sock, "_sock", None)
            if sock is not None:
                sock.settimeout(timeout_s)
        except Exception:
            pass

    def generate(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        cancel_token: CancellationToken | None = None,
    ) -> str:
        out = []
        for d in self.generate_stream(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            cancel_token=cancel_token,
        ):
            out.append(d)
        result = "".join(out).strip()
        if result:
            return result

        retry_messages = self._messages_with_no_think_suffix(messages)
        if retry_messages == list(messages or []):
            return result

        retry_out = []
        for d in self.generate_stream(
            retry_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            cancel_token=cancel_token,
        ):
            retry_out.append(d)
        return "".join(retry_out).strip()

    def generate_stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        cancel_token: CancellationToken | None = None,
    ) -> Iterator[str]:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()

        url = self.cfg.base_url.rstrip("/") + "/v1/chat/completions"

        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": float(self.cfg.temperature if temperature is None else temperature),
            "stream": True,
        }

        mt = self.cfg.max_tokens if max_tokens is None else max_tokens
        # llama-server supports many OpenAI-ish params; harmless if ignored.
        if mt is not None and int(mt) > 0:
            payload["max_tokens"] = int(mt)

        # Debug: dump the exact HTTP JSON we send + a local rendering of the chat template.
        if self.cfg.debug_path:
            _append_debug_dump(
                debug_path=Path(self.cfg.debug_path),
                url=url,
                payload=payload,
                rendered_prompt=_render_chatml(messages),
            )

        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        self._acquire_request_lock(cancel_token)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
                if self.cfg.stream_read_timeout_s and self.cfg.stream_read_timeout_s > 0:
                    self._set_read_timeout(resp, float(self.cfg.stream_read_timeout_s))
                last_progress_at = time.monotonic()
                # Expect SSE
                while True:
                    if cancel_token is not None:
                        cancel_token.raise_if_cancelled()
                    try:
                        line = resp.readline()
                    except socket.timeout:
                        idle_for = time.monotonic() - last_progress_at
                        raise LLMClientError(
                            f"llama-server stream stalled for {idle_for:.1f}s waiting for the next chunk"
                        )
                    if not line:
                        break

                    try:
                        s = line.decode("utf-8", errors="replace").strip()
                    except Exception:
                        continue

                    if not s or (not s.startswith("data:")):
                        continue

                    last_progress_at = time.monotonic()
                    chunk = s[5:].strip()

                    if chunk == "[DONE]":
                        break

                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue

                    choices = obj.get("choices") or []
                    if not choices:
                        continue

                    delta = (choices[0].get("delta") or {})
                    content = delta.get("content")
                    # reasoning_content carries thinking tokens on split-mode servers;
                    # skip it. For inline-mode servers (thinking=0) all tokens arrive
                    # in content — the consuming layer filters the <think>…</think>
                    # preamble so the generator is never blocked.
                    if content:
                        if cancel_token is not None:
                            cancel_token.raise_if_cancelled()
                        yield str(content)

        except OperationCancelled:
            raise
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace").strip()
            except Exception:
                body = ""
            detail = str(e)
            if body:
                detail += f" | body: {body[:500]}"
            raise LLMClientError(f"llama-server request failed: {detail}")
        except Exception as e:
            raise LLMClientError(f"llama-server request failed: {e}")
        finally:
            self._request_lock.release()
