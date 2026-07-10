from __future__ import annotations

import ctypes
import os
import socket
from typing import Any


SIO_TCP_INFO = 0x40047427
SOCKET_ERROR = -1


class _TcpInfoV1(ctypes.Structure):
    _fields_ = [
        ("State", ctypes.c_int32),
        ("Mss", ctypes.c_uint32),
        ("ConnectionTimeMs", ctypes.c_uint64),
        ("TimestampsEnabled", ctypes.c_ubyte),
        ("RttUs", ctypes.c_uint32),
        ("MinRttUs", ctypes.c_uint32),
        ("BytesInFlight", ctypes.c_uint32),
        ("Cwnd", ctypes.c_uint32),
        ("SndWnd", ctypes.c_uint32),
        ("RcvWnd", ctypes.c_uint32),
        ("RcvBuf", ctypes.c_uint32),
        ("BytesOut", ctypes.c_uint64),
        ("BytesIn", ctypes.c_uint64),
        ("BytesReordered", ctypes.c_uint32),
        ("BytesRetrans", ctypes.c_uint32),
        ("FastRetrans", ctypes.c_uint32),
        ("DupAcksIn", ctypes.c_uint32),
        ("TimeoutEpisodes", ctypes.c_uint32),
        ("SynRetrans", ctypes.c_ubyte),
        ("SndLimTransRwin", ctypes.c_uint32),
        ("SndLimTimeRwin", ctypes.c_uint32),
        ("SndLimBytesRwin", ctypes.c_uint64),
        ("SndLimTransCwnd", ctypes.c_uint32),
        ("SndLimTimeCwnd", ctypes.c_uint32),
        ("SndLimBytesCwnd", ctypes.c_uint64),
        ("SndLimTransSnd", ctypes.c_uint32),
        ("SndLimTimeSnd", ctypes.c_uint32),
        ("SndLimBytesSnd", ctypes.c_uint64),
    ]


def snapshot_tcp_info(sock: socket.socket) -> dict[str, Any]:
    if os.name != "nt":
        return {"available": False, "error": "Windows TCP_INFO를 지원하지 않는 환경입니다."}

    ws2_32 = ctypes.WinDLL("Ws2_32.dll", use_last_error=True)
    version = ctypes.c_uint32(1)
    info = _TcpInfoV1()
    returned = ctypes.c_uint32()
    socket_type = ctypes.c_size_t
    ws2_32.WSAIoctl.argtypes = [
        socket_type,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    ws2_32.WSAIoctl.restype = ctypes.c_int
    result = ws2_32.WSAIoctl(
        socket_type(sock.fileno()),
        SIO_TCP_INFO,
        ctypes.byref(version),
        ctypes.sizeof(version),
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(returned),
        None,
        None,
    )
    if result == SOCKET_ERROR:
        error_code = ws2_32.WSAGetLastError()
        return {"available": False, "error": f"SIO_TCP_INFO 조회 실패: {error_code}"}
    return {
        "available": True,
        "rtt_us": int(info.RttUs),
        "min_rtt_us": int(info.MinRttUs),
        "cwnd_bytes": int(info.Cwnd),
        "bytes_retrans": int(info.BytesRetrans),
        "bytes_out": int(info.BytesOut),
        "bytes_in": int(info.BytesIn),
        "fast_retransmits": int(info.FastRetrans),
        "duplicate_acks": int(info.DupAcksIn),
        "timeout_episodes": int(info.TimeoutEpisodes),
    }


def telemetry_delta(start: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    if not start.get("available") or not end.get("available"):
        return {
            "available": False,
            "error": str(end.get("error") or start.get("error") or "TCP 통계를 조회할 수 없습니다."),
        }
    delta_fields = ("bytes_retrans", "fast_retransmits", "duplicate_acks", "timeout_episodes")
    result: dict[str, Any] = {
        "available": True,
        "rtt_us": end.get("rtt_us"),
        "min_rtt_us": end.get("min_rtt_us"),
        "cwnd_bytes": end.get("cwnd_bytes"),
    }
    for field in delta_fields:
        result[field] = max(0, int(end.get(field, 0)) - int(start.get(field, 0)))
    return result
