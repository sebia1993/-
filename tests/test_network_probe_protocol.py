import json
import socket
import struct

import pytest

from network_probe.protocol import MAX_FRAME_BYTES, ProbeProtocolError, recv_frame, send_frame


def test_probe_frame_round_trip():
    left, right = socket.socketpair()
    try:
        send_frame(left, {"type": "ready", "stream_id": 3})
        assert recv_frame(right) == {"type": "ready", "stream_id": 3}
    finally:
        left.close()
        right.close()


def test_probe_frame_rejects_oversized_payload():
    left, right = socket.socketpair()
    try:
        left.sendall(struct.pack("!I", MAX_FRAME_BYTES + 1))
        with pytest.raises(ProbeProtocolError, match="크기"):
            recv_frame(right)
    finally:
        left.close()
        right.close()


def test_probe_frame_rejects_non_object_json():
    left, right = socket.socketpair()
    encoded = json.dumps(["not", "an", "object"]).encode("utf-8")
    try:
        left.sendall(struct.pack("!I", len(encoded)) + encoded)
        with pytest.raises(ProbeProtocolError, match="JSON 객체"):
            recv_frame(right)
    finally:
        left.close()
        right.close()
