"""web_ui.bridge.test_server

Deterministic pytest suite for the standalone BridgeServer.

Requires ``websockets`` to be installed in the test environment.
All network tests use localhost with short timeouts.
"""

from __future__ import annotations

import asyncio
import json
import queue
import time
from typing import Any

import pytest

from web_ui.bridge.server import BridgeServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS_URI = "ws://127.0.0.1:8787/ws"
_DEFAULT_TIMEOUT = 3.0


def _wait_for_condition(condition: callable, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """Poll a callable until it returns a truthy value or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


async def _ws_connect_and_read(uri: str = _WS_URI, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Connect, read one text message, close, and return the message."""
    import websockets

    async with websockets.connect(uri) as ws:
        return await asyncio.wait_for(ws.recv(), timeout=timeout)


async def _ws_connect_and_send(message: str, uri: str = _WS_URI) -> None:
    """Connect, send a text message, and close."""
    import websockets

    async with websockets.connect(uri) as ws:
        await ws.send(message)


async def _ws_connect_send_and_read_response(
    message: str, uri: str = _WS_URI, timeout: float = _DEFAULT_TIMEOUT
) -> str:
    """Connect, send a message, read the response, and close."""
    import websockets

    async with websockets.connect(uri) as ws:
        await ws.send(message)
        return await asyncio.wait_for(ws.recv(), timeout=timeout)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_can_construct_with_mock_queues(self) -> None:
        ui_q: queue.Queue = queue.Queue()
        action_q: queue.Queue = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, action_queue=action_q)
        assert server is not None
        assert server.client_count() == 0
        assert not server.is_running()


class TestLifecycle:
    def test_start_stop_without_hanging(self) -> None:
        server = BridgeServer(ui_queue=queue.Queue())
        try:
            server.start()
            assert server.is_running()
        finally:
            server.stop()
        assert not server.is_running()

    def test_is_running_reflects_state(self) -> None:
        server = BridgeServer(ui_queue=queue.Queue())
        assert not server.is_running()
        server.start()
        try:
            assert server.is_running()
        finally:
            server.stop()
        assert not server.is_running()

    def test_client_count_starts_at_zero(self) -> None:
        server = BridgeServer(ui_queue=queue.Queue())
        server.start()
        try:
            assert server.client_count() == 0
        finally:
            server.stop()

    def test_stop_is_idempotent(self) -> None:
        server = BridgeServer(ui_queue=queue.Queue())
        server.start()
        server.stop()
        assert not server.is_running()
        server.stop()  # second call must not raise
        server.stop()  # third call must not raise
        assert not server.is_running()


class TestBroadcast:
    def test_known_ui_queue_event_reaches_client(self) -> None:
        ui_q: queue.Queue = queue.Queue()
        server = BridgeServer(ui_queue=ui_q)
        server.start()
        try:
            ui_q.put(("chat_append", {"role": "assistant", "content": "hello"}))

            raw = asyncio.run(_ws_connect_and_read())
            frame = json.loads(raw)
            assert frame["frame"] == "event"
            assert frame["kind"] == "chat.append"
            assert frame["sourceKind"] == "chat_append"
            assert frame["payload"]["role"] == "assistant"
            assert frame["payload"]["content"] == "hello"
        finally:
            server.stop()


class TestIncomingActions:
    def test_valid_action_frame_placed_on_action_queue(self) -> None:
        ui_q: queue.Queue = queue.Queue()
        action_q: queue.Queue = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, action_queue=action_q)
        server.start()
        try:
            msg = json.dumps({"frame": "action", "action": "send_message", "payload": {"text": "hi"}})
            asyncio.run(_ws_connect_and_send(msg))

            ok = _wait_for_condition(lambda: not action_q.empty(), timeout=2.0)
            assert ok, "action_queue should receive the parsed action"

            name, payload = action_q.get_nowait()
            assert name == "send_message"
            assert payload["text"] == "hi"
        finally:
            server.stop()

    def test_invalid_action_frame_does_not_crash_server(self) -> None:
        ui_q: queue.Queue = queue.Queue()
        action_q: queue.Queue = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, action_queue=action_q)
        server.start()
        try:
            # Missing "action" field -> adapter raises ValueError
            msg = json.dumps({"frame": "action", "payload": {}})
            raw = asyncio.run(_ws_connect_send_and_read_response(msg))

            frame = json.loads(raw)
            assert frame["frame"] == "error"
            assert "action" in frame["message"].lower() or "missing" in frame["message"].lower()

            # Server must still be alive
            assert server.is_running()
            assert action_q.empty()
        finally:
            server.stop()


class TestOutgoingErrors:
    def test_unknown_outgoing_event_does_not_crash_server(self) -> None:
        ui_q: queue.Queue = queue.Queue()
        server = BridgeServer(ui_queue=ui_q)
        server.start()
        try:
            ui_q.put(("totally_unknown_event_kind", {"foo": "bar"}))

            raw = asyncio.run(_ws_connect_and_read())
            frame = json.loads(raw)
            assert frame["frame"] == "error"
            assert "adapter error" in frame["message"].lower()

            assert server.is_running()
        finally:
            server.stop()


class TestDefaults:
    def test_default_host_and_port(self) -> None:
        server = BridgeServer(ui_queue=queue.Queue())
        # The constructor stores defaults; we verify they are localhost-only.
        assert server._host == "127.0.0.1"
        assert server._port == 8787

        server.start()
        try:
            # A connection attempt proves the server is listening on the expected endpoint.
            asyncio.run(_ws_connect_and_read(timeout=0.5))
            pytest.fail("Expected timeout because no ui_queue event was queued")
        except (TimeoutError, asyncio.TimeoutError):
            pass  # Expected — server is alive but has nothing to send.
        finally:
            server.stop()


class TestWsPathEnforcement:
    def test_ws_path_accepted(self) -> None:
        server = BridgeServer(ui_queue=queue.Queue())
        server.start()
        try:
            # Connecting to the default /ws should succeed (then time out waiting for data).
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                asyncio.run(_ws_connect_and_read(uri=_WS_URI, timeout=0.5))
        finally:
            server.stop()

    def test_wrong_ws_path_rejected(self) -> None:
        server = BridgeServer(ui_queue=queue.Queue())
        server.start()
        try:
            import websockets

            with pytest.raises(websockets.InvalidStatus):
                asyncio.run(_ws_connect_and_read(uri="ws://127.0.0.1:8787/wrong", timeout=1.0))
        finally:
            server.stop()


class TestClientConnectCallback:
    def test_on_client_connect_sends_sync_frame(self) -> None:
        """A connect callback must send its frames to the new client before
        queued live events."""
        ui_q: queue.Queue = queue.Queue()
        server = BridgeServer(
            ui_queue=ui_q,
            on_client_connect=lambda: ['{"frame":"event","kind":"chat.sync","payload":{"messages":[]}}'],
        )
        server.start()
        try:
            raw = asyncio.run(_ws_connect_and_read(timeout=1.0))
            frame = json.loads(raw)
            assert frame["kind"] == "chat.sync"
            assert frame["payload"]["messages"] == []
        finally:
            server.stop()

    def test_on_client_connect_failure_does_not_crash(self) -> None:
        """A failing connect callback must not crash the server or the connection."""
        ui_q: queue.Queue = queue.Queue()
        server = BridgeServer(
            ui_queue=ui_q,
            on_client_connect=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        server.start()
        try:
            # If the callback fails, the connection should still be usable.
            # We queue an event after connect and verify it arrives.
            ui_q.put(("chat_append", {"role": "user", "content": "hi"}))
            raw = asyncio.run(_ws_connect_and_read(timeout=1.0))
            frame = json.loads(raw)
            assert frame["kind"] == "chat.append"
        finally:
            server.stop()

    def test_on_client_connect_per_client(self) -> None:
        """Each connecting client must receive the connect callback frames."""
        ui_q: queue.Queue = queue.Queue()
        call_count = [0]

        def _callback() -> list[str]:
            call_count[0] += 1
            return ['{"frame":"event","kind":"chat.sync","payload":{"messages":[]}}']

        server = BridgeServer(ui_queue=ui_q, on_client_connect=_callback)
        server.start()
        try:

            async def _two_clients() -> list[dict[str, Any]]:
                import websockets

                frames: list[dict[str, Any]] = []
                async with websockets.connect(_WS_URI) as ws1:
                    frames.append(json.loads(await asyncio.wait_for(ws1.recv(), timeout=1.0)))
                    async with websockets.connect(_WS_URI) as ws2:
                        frames.append(json.loads(await asyncio.wait_for(ws2.recv(), timeout=1.0)))
                return frames

            frames = asyncio.run(_two_clients())
            assert call_count[0] == 2
            assert all(f["kind"] == "chat.sync" for f in frames)
        finally:
            server.stop()
