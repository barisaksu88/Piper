"""web_ui.bridge.server

Standalone asyncio WebSocket bridge server for the Piper Web UI.

Responsibilities:
- Consume ui_queue tuples and broadcast adapted JSON frames to all connected clients.
- Parse incoming WebSocket action frames and enqueue (action_name, payload) on action_queue.
- Run in a daemon thread with an asyncio event loop.
- Bind to localhost only (127.0.0.1:8787 /ws by default).

Constraints:
- Does not import from ui/, core/, memory/, tools/, or app.py.
- Does not execute actions; only parses and enqueues them.
- Does not serve static files or images in Phase 2.
- Does not call sys.exit() on failures.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any

import websockets

from web_ui.bridge.adapter import parse_action_frame, ui_tuple_to_ws_frame
from web_ui.bridge.message_schema import ErrorFrame


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
        on_client_connect: Any | None = None,
    ) -> None:
        self._ui_queue = ui_queue
        self._action_queue = action_queue or queue.Queue()
        self._host = host
        self._port = port
        self._ws_path = ws_path
        self._static_dir = static_dir
        self._on_client_connect = on_client_connect

        self._clients: set[websockets.ServerConnection] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._startup_event = threading.Event()
        self._shutdown_event = threading.Event()
        self._ws_server: websockets.WebSocketServer | None = None

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
        Raises RuntimeError on startup timeout.
        """
        if self.is_running():
            return
        if self._thread is not None and self._thread.is_alive():
            self.stop()

        self._startup_event.clear()
        self._shutdown_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        if not self._startup_event.wait(timeout=5.0):
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
        except Exception:
            pass
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
            if getattr(request, "path", "") != self._ws_path:
                return connection.respond(404, "Not Found")
            return None

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
                except Exception:
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
