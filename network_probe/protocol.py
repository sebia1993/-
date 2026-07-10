from __future__ import annotations

import json
import socket
import struct
from typing import Any


MAX_FRAME_BYTES = 16 * 1024


class ProbeProtocolError(RuntimeError):
    pass


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray(size)
    view = memoryview(data)
    received = 0
    while received < size:
        try:
            count = sock.recv_into(view[received:])
        except socket.timeout as exc:
            raise ProbeProtocolError("TCP 제어 메시지 수신 시간이 초과되었습니다.") from exc
        if count == 0:
            raise ProbeProtocolError("TCP 연결이 제어 메시지 수신 중 종료되었습니다.")
        received += count
    return bytes(data)


def send_frame(sock: socket.socket, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME_BYTES:
        raise ProbeProtocolError("TCP 제어 메시지가 허용 크기를 초과했습니다.")
    sock.sendall(struct.pack("!I", len(encoded)) + encoded)


def recv_frame(sock: socket.socket) -> dict[str, Any]:
    (size,) = struct.unpack("!I", recv_exact(sock, 4))
    if size <= 0 or size > MAX_FRAME_BYTES:
        raise ProbeProtocolError("TCP 제어 메시지 크기가 올바르지 않습니다.")
    try:
        payload = json.loads(recv_exact(sock, size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeProtocolError("TCP 제어 메시지 형식이 올바르지 않습니다.") from exc
    if not isinstance(payload, dict):
        raise ProbeProtocolError("TCP 제어 메시지는 JSON 객체여야 합니다.")
    return payload
