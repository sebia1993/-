import socket
import threading
import time

from bounded_server import make_bounded_server


def simple_app(_environ, start_response):
    payload = b"ok"
    start_response(
        "200 OK",
        [("Content-Type", "text/plain"), ("Content-Length", str(len(payload)))],
    )
    return [payload]


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def receive_all(connection):
    chunks = []
    while True:
        try:
            chunk = connection.recv(4096)
        except (ConnectionResetError, TimeoutError):
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def test_bounded_server_rejects_excess_slow_clients_and_recovers_capacity():
    server = make_bounded_server(
        "127.0.0.1",
        0,
        simple_app,
        max_request_threads=2,
        request_timeout_seconds=0.4,
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    address = server.server_address
    slow_connections = []
    try:
        for _ in range(2):
            connection = socket.create_connection(address, timeout=2)
            connection.settimeout(2)
            connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n")
            slow_connections.append(connection)

        assert wait_until(lambda: server.active_request_count == 2)

        rejected = socket.create_connection(address, timeout=2)
        rejected.settimeout(2)
        rejected.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        rejected_payload = receive_all(rejected)
        rejected.close()

        assert b"503 Service Unavailable" in rejected_payload
        assert server.rejected_request_count == 1
        assert server.active_request_count <= 2
        assert wait_until(lambda: server.active_request_count == 0, timeout=2)

        recovered = socket.create_connection(address, timeout=2)
        recovered.settimeout(2)
        recovered.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        recovered_payload = receive_all(recovered)
        recovered.close()

        assert b"200 OK" in recovered_payload
        assert recovered_payload.endswith(b"ok")
    finally:
        for connection in slow_connections:
            connection.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)

    assert not server_thread.is_alive()
