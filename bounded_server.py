from __future__ import annotations

import socket
import threading
from typing import Any

from werkzeug.serving import ThreadedWSGIServer, WSGIRequestHandler


WEB_MAX_REQUEST_THREADS = 32
WEB_REQUEST_TIMEOUT_SECONDS = 30.0


class BoundedWSGIRequestHandler(WSGIRequestHandler):
    def setup(self) -> None:
        self.timeout = float(
            getattr(self.server, "request_timeout_seconds", WEB_REQUEST_TIMEOUT_SECONDS)
        )
        super().setup()


class BoundedThreadedWSGIServer(ThreadedWSGIServer):
    def __init__(
        self,
        host: str,
        port: int,
        app: Any,
        *,
        max_request_threads: int = WEB_MAX_REQUEST_THREADS,
        request_timeout_seconds: float = WEB_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.max_request_threads = max(int(max_request_threads), 1)
        self.request_timeout_seconds = max(float(request_timeout_seconds), 0.1)
        self._request_slots = threading.BoundedSemaphore(self.max_request_threads)
        self._request_state_lock = threading.Lock()
        self._active_request_count = 0
        self._rejected_request_count = 0
        super().__init__(host, port, app, handler=BoundedWSGIRequestHandler)

    @property
    def active_request_count(self) -> int:
        with self._request_state_lock:
            return self._active_request_count

    @property
    def rejected_request_count(self) -> int:
        with self._request_state_lock:
            return self._rejected_request_count

    def process_request(self, request, client_address) -> None:
        if not self._request_slots.acquire(blocking=False):
            with self._request_state_lock:
                self._rejected_request_count += 1
            self._reject_over_capacity(request)
            return

        with self._request_state_lock:
            self._active_request_count += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release_request_slot()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_request_slot()

    def _release_request_slot(self) -> None:
        with self._request_state_lock:
            self._active_request_count = max(self._active_request_count - 1, 0)
        self._request_slots.release()

    def _reject_over_capacity(self, request: socket.socket) -> None:
        payload = b"Server is busy. Retry shortly.\n"
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Connection: close\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"Retry-After: 1\r\n"
            + f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
            + payload
        )
        try:
            request.settimeout(1.0)
            request.sendall(response)
        except OSError:
            pass
        finally:
            self.shutdown_request(request)


def make_bounded_server(
    host: str,
    port: int,
    app: Any,
    threaded: bool = True,
    *,
    max_request_threads: int = WEB_MAX_REQUEST_THREADS,
    request_timeout_seconds: float = WEB_REQUEST_TIMEOUT_SECONDS,
) -> BoundedThreadedWSGIServer:
    if not threaded:
        raise ValueError("bounded web server requires threaded=True")
    return BoundedThreadedWSGIServer(
        host,
        port,
        app,
        max_request_threads=max_request_threads,
        request_timeout_seconds=request_timeout_seconds,
    )
