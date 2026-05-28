"""web_ui.bridge.test_server

Deterministic pytest suite for the standalone BridgeServer.

Requires ``websockets`` to be installed in the test environment.
All network tests use localhost with short timeouts.
"""

from __future__ import annotations

import asyncio
import json
import queue
import socket
import time
from typing import Any

import pytest

from web_ui.bridge.server import BridgeServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 3.0


def get_free_port() -> int:
    """Return an ephemeral localhost port that is free at call time."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ws_uri(port: int, ws_path: str = "/ws") -> str:
    return f"ws://127.0.0.1:{port}{ws_path}"


def _wait_for_condition(condition: callable, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """Poll a callable until it returns a truthy value or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


async def _ws_connect_and_read(uri: str, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Connect, read one text message, close, and return the message."""
    import websockets

    async with websockets.connect(uri) as ws:
        return await asyncio.wait_for(ws.recv(), timeout=timeout)


async def _ws_connect_and_send(message: str, uri: str) -> None:
    """Connect, send a text message, and close."""
    import websockets

    async with websockets.connect(uri) as ws:
        await ws.send(message)


async def _ws_connect_send_and_read_response(
    message: str, uri: str, timeout: float = _DEFAULT_TIMEOUT
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

    def test_max_message_size_stored(self) -> None:
        server = BridgeServer(ui_queue=queue.Queue(), max_message_size=42)
        assert server._max_message_size == 42

    def test_max_message_size_passed_to_websockets_serve(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port, max_message_size=12345)
        from unittest.mock import patch, AsyncMock, Mock

        mock_server = Mock()
        mock_server.close = Mock()
        mock_server.wait_closed = AsyncMock()

        with patch("web_ui.bridge.server.websockets.serve", new_callable=AsyncMock) as mock_serve:
            mock_serve.return_value = mock_server
            server.start()
            try:
                assert server.is_running()
                # Give the loop a moment to reach _serve
                time.sleep(0.3)
                mock_serve.assert_called_once()
                _, kwargs = mock_serve.call_args
                assert kwargs.get("max_size") == 12345
            finally:
                server.stop()


class TestLifecycle:
    def test_start_stop_without_hanging(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        try:
            server.start()
            assert server.is_running()
        finally:
            server.stop()
        assert not server.is_running()

    def test_is_running_reflects_state(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        assert not server.is_running()
        server.start()
        try:
            assert server.is_running()
        finally:
            server.stop()
        assert not server.is_running()

    def test_client_count_starts_at_zero(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        server.start()
        try:
            assert server.client_count() == 0
        finally:
            server.stop()

    def test_stop_is_idempotent(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        server.start()
        server.stop()
        assert not server.is_running()
        server.stop()  # second call must not raise
        server.stop()  # third call must not raise
        assert not server.is_running()


class TestBroadcast:
    def test_known_ui_queue_event_reaches_client(self) -> None:
        port = get_free_port()
        ui_q: queue.Queue = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, port=port)
        server.start()
        try:
            ui_q.put(("chat_append", {"role": "assistant", "content": "hello"}))

            raw = asyncio.run(_ws_connect_and_read(_ws_uri(port)))
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
        port = get_free_port()
        ui_q: queue.Queue = queue.Queue()
        action_q: queue.Queue = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, action_queue=action_q, port=port)
        server.start()
        try:
            msg = json.dumps({"frame": "action", "action": "send_message", "payload": {"text": "hi"}})
            asyncio.run(_ws_connect_and_send(msg, _ws_uri(port)))

            ok = _wait_for_condition(lambda: not action_q.empty(), timeout=2.0)
            assert ok, "action_queue should receive the parsed action"

            name, payload = action_q.get_nowait()
            assert name == "send_message"
            assert payload["text"] == "hi"
        finally:
            server.stop()

    def test_invalid_action_frame_does_not_crash_server(self) -> None:
        port = get_free_port()
        ui_q: queue.Queue = queue.Queue()
        action_q: queue.Queue = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, action_queue=action_q, port=port)
        server.start()
        try:
            # Missing "action" field -> adapter raises ValueError
            msg = json.dumps({"frame": "action", "payload": {}})
            raw = asyncio.run(_ws_connect_send_and_read_response(msg, _ws_uri(port)))

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
        port = get_free_port()
        ui_q: queue.Queue = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, port=port)
        server.start()
        try:
            ui_q.put(("totally_unknown_event_kind", {"foo": "bar"}))

            raw = asyncio.run(_ws_connect_and_read(_ws_uri(port)))
            frame = json.loads(raw)
            assert frame["frame"] == "error"
            assert "adapter error" in frame["message"].lower()

            assert server.is_running()
        finally:
            server.stop()


class TestDefaults:
    def test_default_host_and_port(self) -> None:
        """BridgeServer defaults must remain localhost-only and on the
        production port (8787).  We verify constructor defaults without
        starting the server so the test never binds a fixed port."""
        server = BridgeServer(ui_queue=queue.Queue())
        assert server._host == "127.0.0.1"
        assert server._port == 8787


class TestWsPathEnforcement:
    def test_ws_path_accepted(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        server.start()
        try:
            # Connecting to the default /ws should succeed (then time out waiting for data).
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                asyncio.run(_ws_connect_and_read(_ws_uri(port), timeout=0.5))
        finally:
            server.stop()

    def test_wrong_ws_path_rejected(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        server.start()
        try:
            import websockets

            with pytest.raises(websockets.InvalidStatus):
                asyncio.run(_ws_connect_and_read(f"ws://127.0.0.1:{port}/wrong", timeout=1.0))
        finally:
            server.stop()


class TestClientConnectCallback:
    def test_on_client_connect_sends_sync_frame(self) -> None:
        """A connect callback must send its frames to the new client before
        queued live events."""
        port = get_free_port()
        ui_q: queue.Queue = queue.Queue()
        server = BridgeServer(
            ui_queue=ui_q,
            port=port,
            on_client_connect=lambda: ['{"frame":"event","kind":"chat.sync","payload":{"messages":[]}}'],
        )
        server.start()
        try:
            raw = asyncio.run(_ws_connect_and_read(_ws_uri(port), timeout=1.0))
            frame = json.loads(raw)
            assert frame["kind"] == "chat.sync"
            assert frame["payload"]["messages"] == []
        finally:
            server.stop()

    def test_on_client_connect_failure_does_not_crash(self) -> None:
        """A failing connect callback must not crash the server or the connection."""
        port = get_free_port()
        ui_q: queue.Queue = queue.Queue()
        server = BridgeServer(
            ui_queue=ui_q,
            port=port,
            on_client_connect=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        server.start()
        try:
            # If the callback fails, the connection should still be usable.
            # We queue an event after connect and verify it arrives.
            ui_q.put(("chat_append", {"role": "user", "content": "hi"}))
            raw = asyncio.run(_ws_connect_and_read(_ws_uri(port), timeout=1.0))
            frame = json.loads(raw)
            assert frame["kind"] == "chat.append"
        finally:
            server.stop()

    def test_on_client_connect_per_client(self) -> None:
        """Each connecting client must receive the connect callback frames."""
        port = get_free_port()
        ui_q: queue.Queue = queue.Queue()
        call_count = [0]

        def _callback() -> list[str]:
            call_count[0] += 1
            return ['{"frame":"event","kind":"chat.sync","payload":{"messages":[]}}']

        server = BridgeServer(ui_queue=ui_q, port=port, on_client_connect=_callback)
        server.start()
        try:

            async def _two_clients() -> list[dict[str, Any]]:
                import websockets

                frames: list[dict[str, Any]] = []
                async with websockets.connect(_ws_uri(port)) as ws1:
                    frames.append(json.loads(await asyncio.wait_for(ws1.recv(), timeout=1.0)))
                    async with websockets.connect(_ws_uri(port)) as ws2:
                        frames.append(json.loads(await asyncio.wait_for(ws2.recv(), timeout=1.0)))
                return frames

            frames = asyncio.run(_two_clients())
            assert call_count[0] == 2
            assert all(f["kind"] == "chat.sync" for f in frames)
        finally:
            server.stop()


class TestStaticFileServing:
    """Safe static file serving from configured static_dir."""

    def test_serves_safe_image_file(self, tmp_path) -> None:
        port = get_free_port()
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        server = BridgeServer(ui_queue=queue.Queue(), port=port, static_dir=str(tmp_path))
        server.start()
        try:
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/workspace/test.png")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200
                assert resp.read() == img.read_bytes()
                assert resp.headers.get("Content-Type") == "image/png"
                # No Origin header was sent; CORS header is omitted for same-origin.
                assert resp.headers.get("Access-Control-Allow-Origin") is None
        finally:
            server.stop()

    def test_serves_safe_image_file_with_allowed_origin(self, tmp_path) -> None:
        port = get_free_port()
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        server = BridgeServer(ui_queue=queue.Queue(), port=port, static_dir=str(tmp_path))
        server.start()
        try:
            import urllib.request

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/workspace/test.png",
                headers={"Origin": "http://localhost:3000"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200
                assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"
        finally:
            server.stop()

    def test_rejects_path_traversal(self, tmp_path) -> None:
        port = get_free_port()
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "secret.png").write_bytes(b"secret")

        server = BridgeServer(ui_queue=queue.Queue(), port=port, static_dir=str(tmp_path))
        server.start()
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/workspace/../secret.png")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req, timeout=2.0)
            assert exc_info.value.code == 404
        finally:
            server.stop()

    def test_rejects_unsafe_extension(self, tmp_path) -> None:
        port = get_free_port()
        (tmp_path / "evil.exe").write_bytes(b"evil")

        server = BridgeServer(ui_queue=queue.Queue(), port=port, static_dir=str(tmp_path))
        server.start()
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/workspace/evil.exe")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req, timeout=2.0)
            assert exc_info.value.code == 404
        finally:
            server.stop()

    def test_rejects_file_outside_static_dir(self, tmp_path) -> None:
        port = get_free_port()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "image.png").write_bytes(b"outside")

        inner = tmp_path / "inner"
        inner.mkdir()
        server = BridgeServer(ui_queue=queue.Queue(), port=port, static_dir=str(inner))
        server.start()
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/workspace/../outside/image.png")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req, timeout=2.0)
            assert exc_info.value.code == 404
        finally:
            server.stop()

    def test_returns_404_when_no_static_dir(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        server.start()
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/workspace/anything.png")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req, timeout=2.0)
            assert exc_info.value.code == 404
        finally:
            server.stop()


class TestFrontendServing:
    """Built-in React frontend serving from configured frontend_dist_dir."""

    def test_stores_frontend_dist_dir(self, tmp_path) -> None:
        server = BridgeServer(ui_queue=queue.Queue(), frontend_dist_dir=str(tmp_path))
        assert server._frontend_dist_dir == str(tmp_path)

    def test_serves_index_html_at_root(self, tmp_path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>hello</html>")

        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port, frontend_dist_dir=str(dist))
        server.start()
        try:
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200
                assert resp.read().decode() == "<html>hello</html>"
                assert resp.headers.get("Content-Type") == "text/html"
        finally:
            server.stop()

    def test_serves_asset_file(self, tmp_path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        assets = dist / "assets"
        assets.mkdir()
        (assets / "app.js").write_text("console.log('piper')")

        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port, frontend_dist_dir=str(dist))
        server.start()
        try:
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/assets/app.js")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200
                assert resp.read().decode() == "console.log('piper')"
                assert resp.headers.get("Content-Type") == "text/javascript"
        finally:
            server.stop()

    def test_unknown_path_falls_back_to_index_html(self, tmp_path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>spa</html>")

        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port, frontend_dist_dir=str(dist))
        server.start()
        try:
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/settings/profile")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200
                assert resp.read().decode() == "<html>spa</html>"
        finally:
            server.stop()

    def test_rejects_frontend_path_traversal(self, tmp_path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>safe</html>")
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")

        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port, frontend_dist_dir=str(dist))
        server.start()
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/../secret.txt")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req, timeout=2.0)
            assert exc_info.value.code == 404
        finally:
            server.stop()

    def test_workspace_still_works_when_frontend_dir_configured(self, tmp_path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>spa</html>")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "test.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

        port = get_free_port()
        server = BridgeServer(
            ui_queue=queue.Queue(),
            port=port,
            static_dir=str(workspace),
            frontend_dist_dir=str(dist),
        )
        server.start()
        try:
            import urllib.request

            # Workspace image still serves
            req = urllib.request.Request(f"http://127.0.0.1:{port}/workspace/test.png")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200
                assert resp.read() == b"\x89PNG\r\n\x1a\nfake"

            # Frontend root still serves
            req = urllib.request.Request(f"http://127.0.0.1:{port}/")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                assert resp.status == 200
                assert resp.read().decode() == "<html>spa</html>"
        finally:
            server.stop()

    def test_returns_404_when_no_frontend_dist_dir(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        server.start()
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req, timeout=2.0)
            assert exc_info.value.code == 404
        finally:
            server.stop()

    def test_returns_404_when_frontend_dist_dir_missing(self, tmp_path) -> None:
        missing = tmp_path / "nonexistent"
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port, frontend_dist_dir=str(missing))
        server.start()
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req, timeout=2.0)
            assert exc_info.value.code == 404
        finally:
            server.stop()


class TestOriginValidation:
    def test_rejects_evil_origin(self) -> None:
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        server.start()
        try:
            import websockets

            with pytest.raises(websockets.InvalidStatus):
                asyncio.run(
                    _ws_connect_with_origin(_ws_uri(port), origin="http://evil.com")
                )
        finally:
            server.stop()

    def test_rejects_localhost_substring_spoof(self) -> None:
        """Substring matching is not enough; only exact hostnames are allowed."""
        port = get_free_port()
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        server.start()
        try:
            import websockets

            with pytest.raises(websockets.InvalidStatus):
                asyncio.run(
                    _ws_connect_with_origin(_ws_uri(port), origin="http://not-localhost.evil.com")
                )
        finally:
            server.stop()

    def test_accepts_localhost_origin(self) -> None:
        port = get_free_port()
        ui_q = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, port=port)
        server.start()
        try:
            ui_q.put(("chat_append", {"role": "assistant", "content": "hello"}))
            raw = asyncio.run(
                _ws_connect_with_origin(_ws_uri(port), origin="http://localhost:3000")
            )
            frame = json.loads(raw)
            assert frame["kind"] == "chat.append"
        finally:
            server.stop()

    def test_accepts_127_0_0_1_origin(self) -> None:
        port = get_free_port()
        ui_q = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, port=port)
        server.start()
        try:
            ui_q.put(("chat_append", {"role": "assistant", "content": "hello"}))
            raw = asyncio.run(
                _ws_connect_with_origin(_ws_uri(port), origin="http://127.0.0.1:3000")
            )
            frame = json.loads(raw)
            assert frame["kind"] == "chat.append"
        finally:
            server.stop()

    def test_allows_env_origin_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        port = get_free_port()
        monkeypatch.setenv("PIPER_WEB_UI_ALLOWED_ORIGINS", "myhost.local")
        ui_q = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, port=port)
        server.start()
        try:
            import websockets

            ui_q.put(("chat_append", {"role": "assistant", "content": "hello"}))
            # Default localhost should still work
            raw = asyncio.run(
                _ws_connect_with_origin(_ws_uri(port), origin="http://localhost:3000")
            )
            frame = json.loads(raw)
            assert frame["frame"] == "event"

            ui_q.put(("chat_append", {"role": "assistant", "content": "hello2"}))
            # Custom env host should also work (hostname-only entry matches any port)
            raw2 = asyncio.run(
                _ws_connect_with_origin(_ws_uri(port), origin="http://myhost.local")
            )
            frame2 = json.loads(raw2)
            assert frame2["frame"] == "event"

            # Evil host should still fail
            with pytest.raises(websockets.InvalidStatus):
                asyncio.run(
                    _ws_connect_with_origin(_ws_uri(port), origin="http://evil.com")
                )
        finally:
            server.stop()

    def test_rejects_env_origin_port_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        port = get_free_port()
        monkeypatch.setenv("PIPER_WEB_UI_ALLOWED_ORIGINS", "http://myhost.local:8080")
        server = BridgeServer(ui_queue=queue.Queue(), port=port)
        server.start()
        try:
            import websockets

            # Same host, wrong port → rejected
            with pytest.raises(websockets.InvalidStatus):
                asyncio.run(
                    _ws_connect_with_origin(_ws_uri(port), origin="http://myhost.local:9090")
                )
        finally:
            server.stop()

    def test_accepts_env_origin_with_exact_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        port = get_free_port()
        monkeypatch.setenv("PIPER_WEB_UI_ALLOWED_ORIGINS", "http://myhost.local:8080")
        ui_q = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, port=port)
        server.start()
        try:
            ui_q.put(("chat_append", {"role": "assistant", "content": "hello"}))
            raw = asyncio.run(
                _ws_connect_with_origin(_ws_uri(port), origin="http://myhost.local:8080")
            )
            frame = json.loads(raw)
            assert frame["kind"] == "chat.append"
        finally:
            server.stop()

    def test_accepts_https_origin_default_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        port = get_free_port()
        monkeypatch.setenv("PIPER_WEB_UI_ALLOWED_ORIGINS", "https://myhost.local")
        ui_q = queue.Queue()
        server = BridgeServer(ui_queue=ui_q, port=port)
        server.start()
        try:
            ui_q.put(("chat_append", {"role": "assistant", "content": "hello"}))
            raw = asyncio.run(
                _ws_connect_with_origin(_ws_uri(port), origin="https://myhost.local")
            )
            frame = json.loads(raw)
            assert frame["kind"] == "chat.append"
        finally:
            server.stop()


async def _ws_connect_with_origin(uri: str, origin: str) -> str:
    import websockets

    async with websockets.connect(uri, origin=origin) as ws:
        return await asyncio.wait_for(ws.recv(), timeout=_DEFAULT_TIMEOUT)


class TestHandleConnectionErrors:
    """WebSocket receive error classification."""

    @pytest.mark.asyncio
    async def test_normal_close_logged_at_debug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from websockets.frames import Close
        from websockets.exceptions import ConnectionClosedOK
        from web_ui.bridge.server import BridgeServer

        debug_calls: list[str] = []
        warning_calls: list[str] = []

        monkeypatch.setattr("web_ui.bridge.server._LOG.debug", lambda msg, *a: debug_calls.append(msg % a if a else msg))
        monkeypatch.setattr("web_ui.bridge.server._LOG.warning", lambda msg, *a: warning_calls.append(msg % a if a else msg))

        server = BridgeServer(ui_queue=queue.Queue())

        class FakeConn:
            async def recv(self) -> None:
                raise ConnectionClosedOK(Close(1000, ""), None, None)

            async def close(self) -> None:
                pass

        await server._handle_connection(FakeConn())  # type: ignore[arg-type]
        assert len(debug_calls) == 1
        assert "closed normally" in debug_calls[0]
        assert len(warning_calls) == 0

    @pytest.mark.asyncio
    async def test_unexpected_receive_failure_logged_at_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from web_ui.bridge.server import BridgeServer

        debug_calls: list[str] = []
        warning_calls: list[str] = []

        monkeypatch.setattr("web_ui.bridge.server._LOG.debug", lambda msg, *a: debug_calls.append(msg % a if a else msg))
        monkeypatch.setattr("web_ui.bridge.server._LOG.warning", lambda msg, *a: warning_calls.append(msg % a if a else msg))

        server = BridgeServer(ui_queue=queue.Queue())

        class FakeConn:
            async def recv(self) -> None:
                raise RuntimeError("network broken")

            async def close(self) -> None:
                pass

        await server._handle_connection(FakeConn())  # type: ignore[arg-type]
        assert len(debug_calls) == 0
        assert len(warning_calls) == 1
        assert "Bridge receive failed" in warning_calls[0]
