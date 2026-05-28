"""web_ui.bridge.server

Standalone asyncio WebSocket bridge server for the Piper Web UI.

Responsibilities:
- Consume ui_queue tuples and broadcast adapted JSON frames to all connected clients.
- Parse incoming WebSocket action frames and enqueue (action_name, payload) on action_queue.
- Run in a daemon thread with an asyncio event loop.
- Bind to localhost only (127.0.0.1:8787 /ws by default).
- Optionally serve safe static files (images) from a configured directory.

Constraints:
- Does not import from ui/, core/, memory/, tools/, or app.py.
- Does not execute actions; only parses and enqueues them.
- Does not call sys.exit() on failures.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import queue
import threading
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosedOK

from web_ui.bridge.adapter import parse_action_frame, ui_tuple_to_ws_frame

# Suppress noisy "opening handshake failed" ERROR logs from the websockets
# library. These are usually caused by browsers refreshing or closing tabs
# before the WebSocket handshake completes, and they spam the terminal.
# We keep INFO-level "connection open/close" logs.
import logging as _logging

_LOG = _logging.getLogger("web_ui.bridge.server")


class _SuppressWebsocketHandshakeFilter(_logging.Filter):
    def filter(self, record: _logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "opening handshake failed" not in msg

_websockets_server_logger = _logging.getLogger("websockets.server")
_websockets_server_logger.addFilter(_SuppressWebsocketHandshakeFilter())
from web_ui.bridge.message_schema import ErrorFrame


# Safe image extensions for static file serving.
_SAFE_IMAGE_EXTENSIONS: set[str] = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
}


class BridgeServer:
    """Asyncio WebSocket bridge server running in a daemon thread."""

    def __init__(
        self,
        ui_queue: queue.Queue,
        action_queue: queue.Queue | None = None,
        host: str = "127.0.0.1",
        port: int = 8787,
        ws_path: str = "/ws",
        static_dir: str | None = None,
        frontend_dist_dir: str | None = None,
        on_client_connect: Any | None = None,
        max_message_size: int | None = None,
    ) -> None:
        self._ui_queue = ui_queue
        self._action_queue = action_queue or queue.Queue()
        self._host = host
        self._port = port
        self._ws_path = ws_path
        self._static_dir = static_dir
        self._frontend_dist_dir = frontend_dist_dir
        self._on_client_connect = on_client_connect
        self._max_message_size = max_message_size

        self._clients: set[websockets.ServerConnection] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._startup_event = threading.Event()
        self._shutdown_event = threading.Event()
        self._ws_server: websockets.WebSocketServer | None = None

    def _cors_origin(self, request: Any) -> str | None:
        """Return the allowed CORS origin for a request.

        Only localhost origins (127.0.0.1, localhost) are allowed.
        Returns the request origin if it is localhost, otherwise None.
        """
        origin = getattr(request, "headers", {})
        if hasattr(origin, "get"):
            origin = origin.get("Origin", "")
        else:
            origin = ""
        if origin:
            lower = origin.lower()
            if "localhost" in lower or "127.0.0.1" in lower:
                return origin
        # If no Origin header, allow if the server binds to localhost
        if self._host in ("127.0.0.1", "localhost", "::1"):
            return None  # No CORS header needed for same-origin
        return None

    # ------------------------------------------------------------------
    # Public lifecycle API
    # ------------------------------------------------------------------

    def client_count(self) -> int:
        """Return the number of currently connected WebSocket clients."""
        with self._lock:
            return len(self._clients)

    def is_running(self) -> bool:
        """Return True if the server thread is alive and listening."""
        if self._thread is None:
            return False
        return (
            self._thread.is_alive()
            and self._startup_event.is_set()
            and not self._shutdown_event.is_set()
        )

    def start(self) -> None:
        """Start the server in a daemon thread.

        Blocks until the server is listening or a startup failure is detected.
        Retries up to 3 times to handle Windows TIME_WAIT port reuse.
        Raises RuntimeError on startup timeout.
        """
        if self.is_running():
            return
        if self._thread is not None and self._thread.is_alive():
            self.stop()

        import time as _time

        for attempt in range(1, 4):
            self._startup_event.clear()
            self._shutdown_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

            if self._startup_event.wait(timeout=5.0):
                return

            # Startup failed — clean up the dead thread before retrying.
            self._shutdown_event.set()
            self._thread.join(timeout=2.0)
            if attempt < 3:
                _time.sleep(0.5)

        raise RuntimeError("Bridge server failed to start within timeout")

    def stop(self, timeout_s: float = 3.0) -> None:
        """Stop the server and clean up the daemon thread.

        Safe to call multiple times (idempotent).
        """
        if not self.is_running() and (
            self._thread is None or not self._thread.is_alive()
        ):
            return

        self._shutdown_event.set()

        # Close all client connections from the event loop thread.
        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._do_close_clients(), self._loop
            )
            try:
                future.result(timeout=timeout_s)
            except Exception:
                pass

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=max(0.5, timeout_s - 0.5))

        self._thread = None
        self._loop = None
        self._startup_event.clear()

    # ------------------------------------------------------------------
    # Internal asyncio loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Thread target: create and run the asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:
            import logging as _logging
            _logging.getLogger("web_ui.bridge.server").error(
                "Bridge server loop crashed: %s", exc, exc_info=True
            )
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass

    async def _serve(self) -> None:
        """Main coroutine: start the WebSocket server and queue consumer."""

        async def _process_request(
            connection: websockets.ServerConnection, request: Any
        ) -> Any:
            path = getattr(request, "path", "")
            method = getattr(request, "method", "GET")

            # WebSocket upgrade path — validate origin before allowing upgrade
            if path == self._ws_path:
                origin = getattr(request, "headers", {})
                if hasattr(origin, "get"):
                    origin = origin.get("Origin", "")
                else:
                    origin = ""
                if origin:
                    lower = origin.lower()
                    if "localhost" not in lower and "127.0.0.1" not in lower:
                        _LOG.warning("WebSocket connection rejected: invalid origin '%s'", origin)
                        return connection.respond(403, "Forbidden: invalid origin")
                return None

            # Image file serving (GET only, safe image extensions)
            if method == "GET" and path.startswith("/images/"):
                response = await self._serve_image_file(path, request)
                if response is not None:
                    return response
                return connection.respond(404, "Not Found")

            # Static file serving (GET only, safe image extensions)
            if method == "GET" and path.startswith("/workspace/"):
                response = await self._serve_static_file(path, request)
                if response is not None:
                    return response
                return connection.respond(404, "Not Found")

            # Frontend static file serving
            if method == "GET":
                response = await self._serve_frontend_file(path, request)
                if response is not None:
                    return response

            return connection.respond(404, "Not Found")

        async def _ws_handler(connection: websockets.ServerConnection) -> None:
            with self._lock:
                self._clients.add(connection)
            try:
                if self._on_client_connect is not None:
                    try:
                        frames = self._on_client_connect()
                        for frame in frames:
                            await connection.send(frame)
                    except Exception:
                        pass
                await self._handle_connection(connection)
            finally:
                with self._lock:
                    self._clients.discard(connection)

        self._ws_server = await websockets.serve(
            _ws_handler,
            self._host,
            self._port,
            process_request=_process_request,
            max_size=self._max_message_size,
        )
        self._startup_event.set()

        queue_task = asyncio.create_task(self._consume_queue())

        try:
            while not self._shutdown_event.is_set():
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass

        queue_task.cancel()
        try:
            await queue_task
        except asyncio.CancelledError:
            pass

        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None

    async def _serve_static_file(
        self, path: str, request: Any
    ) -> websockets.Response | None:
        """Serve a safe static file from ``self._static_dir``.

        Returns a Response on success, None to fall through to 404.
        Guards against directory traversal and unsafe extensions.
        """
        if not self._static_dir:
            return None

        # Strip prefix and reject empty or suspicious paths
        raw = path[len("/workspace/") :]
        if not raw or raw.startswith(".") or ".." in raw or "\\" in raw:
            return None

        # Only allow safe image extensions
        suffix = Path(raw).suffix.lower()
        if suffix not in _SAFE_IMAGE_EXTENSIONS:
            return None

        base_dir = Path(self._static_dir).resolve()
        try:
            target = (base_dir / raw).resolve()
        except (OSError, ValueError):
            return None

        # Containment check: target must be inside base_dir
        try:
            target.relative_to(base_dir)
        except ValueError:
            return None

        if not target.is_file():
            return None

        try:
            data = target.read_bytes()
        except (OSError, PermissionError):
            return None

        content_type, _ = mimetypes.guess_type(str(target))
        if not content_type:
            content_type = "application/octet-stream"

        headers = websockets.Headers()
        headers["Content-Type"] = content_type
        cors = self._cors_origin(request)
        if cors:
            headers["Access-Control-Allow-Origin"] = cors
        headers["Cache-Control"] = "no-cache"

        return websockets.Response(200, "OK", headers, body=data)

    async def _serve_image_file(
        self, path: str, request: Any
    ) -> websockets.Response | None:
        """Serve a safe image file from ``self._static_dir`` at ``/images/{filename}``.

        Returns a Response on success, None to fall through to 404.
        Guards against directory traversal and unsafe extensions.
        """
        if not self._static_dir:
            return None

        # Strip prefix and reject empty or suspicious paths
        raw = path[len("/images/") :]
        if not raw or raw.startswith(".") or ".." in raw or "\\" in raw or "/" in raw:
            return None

        # Only allow safe image extensions
        suffix = Path(raw).suffix.lower()
        if suffix not in _SAFE_IMAGE_EXTENSIONS:
            return None

        base_dir = Path(self._static_dir).resolve()
        try:
            target = (base_dir / raw).resolve()
        except (OSError, ValueError):
            return None

        # Containment check: target must be inside base_dir
        try:
            target.relative_to(base_dir)
        except ValueError:
            return None

        if not target.is_file():
            return None

        try:
            data = target.read_bytes()
        except (OSError, PermissionError):
            return None

        content_type, _ = mimetypes.guess_type(str(target))
        if not content_type:
            content_type = "application/octet-stream"

        headers = websockets.Headers()
        headers["Content-Type"] = content_type
        cors = self._cors_origin(request)
        if cors:
            headers["Access-Control-Allow-Origin"] = cors
        headers["Cache-Control"] = "no-cache"

        return websockets.Response(200, "OK", headers, body=data)

    async def _serve_frontend_file(
        self, path: str, request: Any
    ) -> websockets.Response | None:
        """Serve a safe static file from ``self._frontend_dist_dir``.

        Returns a Response on success, None to fall through to 404.
        Guards against directory traversal. Unknown paths fall back to
        ``index.html`` so that React Router works correctly.
        """
        if not self._frontend_dist_dir:
            return None

        base_dir = Path(self._frontend_dist_dir).resolve()
        if not base_dir.is_dir():
            return None

        if path == "/":
            target = base_dir / "index.html"
        else:
            raw = path.lstrip("/")
            if not raw or raw.startswith(".") or ".." in raw or "\\" in raw:
                return None
            try:
                target = (base_dir / raw).resolve()
            except (OSError, ValueError):
                return None

            # Containment check: target must be inside base_dir
            try:
                target.relative_to(base_dir)
            except ValueError:
                return None

            if not target.is_file():
                # Fallback to index.html for React Router
                target = base_dir / "index.html"

        if not target.is_file():
            return None

        try:
            data = target.read_bytes()
        except (OSError, PermissionError):
            return None

        content_type, _ = mimetypes.guess_type(str(target))
        if not content_type:
            content_type = "application/octet-stream"

        headers = websockets.Headers()
        headers["Content-Type"] = content_type
        cors = self._cors_origin(request)
        if cors:
            headers["Access-Control-Allow-Origin"] = cors
        headers["Cache-Control"] = "no-cache"

        return websockets.Response(200, "OK", headers, body=data)

    async def _do_close_clients(self) -> None:
        """Close all connected WebSocket clients."""
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for conn in clients:
            try:
                await conn.close()
            except Exception:
                pass

    async def _handle_connection(self, connection: websockets.ServerConnection) -> None:
        """Read messages from a single client, parse actions, and enqueue them."""
        try:
            while not self._shutdown_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        connection.recv(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    exc_type = type(exc).__name__
                    exc_msg = str(exc)
                    # Normal close codes (1000 = normal, 1001 = going away) are not errors.
                    close_code = getattr(getattr(exc, "rcvd", None), "code", 0)
                    if isinstance(exc, ConnectionClosedOK) and close_code in (1000, 1001, 1005):
                        _LOG.debug("Bridge connection closed normally: %s", exc_msg)
                    else:
                        hint = ""
                        if "payload" in exc_msg.lower() or "size" in exc_msg.lower() or "limit" in exc_msg.lower():
                            hint = " (hint: message may be too large)"
                        _LOG.warning("Bridge receive failed: %s: %s%s", exc_type, exc_msg, hint)
                    break

                try:
                    action_name, payload = parse_action_frame(message)
                    self._action_queue.put((action_name, payload))
                except ValueError as exc:
                    error_frame = ErrorFrame(
                        timestamp="",
                        kind="error",
                        message=str(exc),
                    )
                    try:
                        await connection.send(json.dumps(error_frame.to_dict()))
                    except Exception:
                        pass
        except Exception:
            pass

    async def _consume_queue(self) -> None:
        """Poll ui_queue and broadcast adapted frames to all connected clients."""
        while not self._shutdown_event.is_set():
            try:
                kind, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue

            try:
                frame = ui_tuple_to_ws_frame(kind, payload)
            except ValueError as exc:
                frame = json.dumps(
                    ErrorFrame(
                        timestamp="",
                        kind="error",
                        message=f"Adapter error: {exc}",
                    ).to_dict()
                )

            with self._lock:
                clients = set(self._clients)

            if clients:
                websockets.broadcast(clients, frame)
