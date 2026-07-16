from __future__ import annotations

import socket
import threading
import time
from typing import Any

from werkzeug.serving import ThreadedWSGIServer, WSGIRequestHandler


WEB_MAX_REQUEST_THREADS = 32
WEB_REQUEST_TIMEOUT_SECONDS = 30.0
WEB_SHUTDOWN_DRAIN_SECONDS = 30.0


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
        self._request_state_changed = threading.Condition(self._request_state_lock)
        self._active_request_count = 0
        self._rejected_request_count = 0
        self._draining = False
        super().__init__(host, port, app, handler=BoundedWSGIRequestHandler)

    @property
    def active_request_count(self) -> int:
        with self._request_state_lock:
            return self._active_request_count

    @property
    def rejected_request_count(self) -> int:
        with self._request_state_lock:
            return self._rejected_request_count

    @property
    def is_draining(self) -> bool:
        with self._request_state_lock:
            return self._draining

    def begin_shutdown(self) -> None:
        with self._request_state_changed:
            self._draining = True
            self._request_state_changed.notify_all()

    def wait_for_active_requests(
        self,
        timeout_seconds: float = WEB_SHUTDOWN_DRAIN_SECONDS,
    ) -> bool:
        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        with self._request_state_changed:
            while self._active_request_count > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._request_state_changed.wait(timeout=remaining)
            return True

    def process_request(self, request, client_address) -> None:
        reject_reason = ""
        with self._request_state_changed:
            if self._draining:
                self._rejected_request_count += 1
                reject_reason = "shutdown"
            elif not self._request_slots.acquire(blocking=False):
                self._rejected_request_count += 1
                reject_reason = "capacity"
            else:
                self._active_request_count += 1
        if reject_reason:
            self._reject_request(request, reason=reject_reason)
            return
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
        with self._request_state_changed:
            self._active_request_count = max(self._active_request_count - 1, 0)
            self._request_state_changed.notify_all()
        self._request_slots.release()

    def _reject_request(self, request: socket.socket, *, reason: str) -> None:
        if reason == "shutdown":
            payload = b"Server is shutting down. Retry later.\n"
            retry_after = 30
        else:
            payload = b"Server is busy. Retry shortly.\n"
            retry_after = 1
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Connection: close\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            + f"Retry-After: {retry_after}\r\n".encode("ascii")
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
