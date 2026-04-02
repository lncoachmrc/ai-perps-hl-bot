from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Dict

logger = logging.getLogger(__name__)


class _Handler(BaseHTTPRequestHandler):
    status_provider: Callable[[], Dict[str, object]] = lambda: {"status": "unknown"}

    def do_GET(self):  # noqa: N802
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(self.status_provider()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class HealthServer:
    def __init__(self, host: str, port: int, status_provider: Callable[[], Dict[str, object]]) -> None:
        self._server = HTTPServer((host, port), _Handler)
        _Handler.status_provider = status_provider
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        logger.info("Starting health server")
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
