from __future__ import annotations

import contextlib
import socket
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

from _bootstrap import ROOT_DIR


FIXTURE_ROOT = ROOT_DIR / "scripts" / "fixtures" / "computer_use"


class _ComputerUseFixtureRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        download_path = self.path.split("?", 1)[0]
        if download_path.endswith("/downloads/report.txt"):
            self.send_header("Content-Disposition", 'attachment; filename="report.txt"')
        elif download_path.endswith("/downloads/quarterly-report.pdf"):
            self.send_header("Content-Disposition", 'attachment; filename="quarterly-report.pdf"')
        elif download_path.endswith("/downloads/quarterly-report.sha256"):
            self.send_header("Content-Disposition", 'attachment; filename="quarterly-report.sha256"')
        elif download_path.endswith("/downloads/release-notes.txt"):
            self.send_header("Content-Disposition", 'attachment; filename="release-notes.txt"')
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - stdlib signature
        del format, args


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def running_fixture_server(root: Path | None = None) -> Iterator[str]:
    fixture_root = Path(root or FIXTURE_ROOT).resolve()
    handler = partial(_ComputerUseFixtureRequestHandler, directory=str(fixture_root))
    port = _pick_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)
