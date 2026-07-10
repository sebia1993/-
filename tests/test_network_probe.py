from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid

import pytest

import network_probe.service as service_module
from network_measurement import NetworkMeasurementGate
from network_probe.models import PROBE_PROTOCOL_VERSION, ProbeConfig
from network_probe.agent import ProbeClientError, normalize_server_url
from network_probe.protocol import recv_frame, send_frame
from network_probe.self_check import run_probe_self_check
from network_probe.service import ProbeService, ProbeServiceError
from network_probe.tcp_engine import aggregate_stream_results, run_receiver_stream, run_sender_stream
from network_probe.windows_tcp_info import snapshot_tcp_info


def available_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def build_service(
    tmp_path,
    *,
    enabled: bool = True,
    attach_timeout: float = 10.0,
) -> tuple[ProbeService, NetworkMeasurementGate]:
    gate = NetworkMeasurementGate()
    service = ProbeService(
        config=ProbeConfig(
            enabled=enabled,
            host="127.0.0.1",
            port=available_port(),
            log_path=tmp_path / "data" / "network_probe_log.csv",
            results_root=tmp_path / "data" / "network_probe_results",
            warmup_seconds=0.05,
            long_poll_seconds=0.05,
            agent_ttl_seconds=10,
            stream_attach_timeout_seconds=attach_timeout,
        ),
        measurement_gate=gate,
        normalize_ip=lambda value: value or "",
    )
    return service, gate


def register(service: ProbeService) -> dict:
    return service.register_agent(
        {
            "agent_id": uuid.uuid4().hex,
            "hostname": "TEST-PC",
            "server_host": "127.0.0.1",
            "protocol_version": PROBE_PROTOCOL_VERSION,
        },
        "127.0.0.1",
    )


def run_client_phase(service: ProbeService, registration: dict, job: dict, phase: str) -> dict:
    sockets: dict[int, socket.socket] = {}
    try:
        for stream_id in range(int(job["stream_count"])):
            sock = socket.create_connection(("127.0.0.1", service.config.port), timeout=3)
            sock.settimeout(3)
            send_frame(
                sock,
                {
                    "type": "data_stream",
                    "protocol_version": PROBE_PROTOCOL_VERSION,
                    "session_id": job["session_id"],
                    "session_token": job["session_token"],
                    "phase": phase,
                    "stream_id": stream_id,
                },
            )
            assert recv_frame(sock)["type"] == "ready"
            sockets[stream_id] = sock
        for stream_id, sock in sockets.items():
            go = recv_frame(sock)
            assert go["type"] == "go"
            assert go["stream_id"] == stream_id

        role = "sender" if phase == "upload" else "receiver"
        cancel_event = threading.Event()
        results = []
        errors = []
        lock = threading.Lock()

        def worker(stream_id: int, sock: socket.socket) -> None:
            try:
                if role == "sender":
                    result = run_sender_stream(
                        sock,
                        stream_id=stream_id,
                        warmup_seconds=float(job["warmup_seconds"]),
                        duration_seconds=int(job["duration_seconds"]),
                        cancel_event=cancel_event,
                    )
                else:
                    result = run_receiver_stream(
                        sock,
                        stream_id=stream_id,
                        warmup_seconds=float(job["warmup_seconds"]),
                        duration_seconds=int(job["duration_seconds"]),
                        cancel_event=cancel_event,
                    )
                with lock:
                    results.append(result)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=item) for item in sockets.items()]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert not errors
        assert all(not thread.is_alive() for thread in threads)
        result = aggregate_stream_results(results, role=role, duration_seconds=int(job["duration_seconds"]))
        return service.complete_agent_phase(
            str(job["session_id"]),
            str(registration["agent_id"]),
            str(registration["agent_token"]),
            "127.0.0.1",
            {"phase": phase, "status": "success", "result": result},
        )
    finally:
        for sock in sockets.values():
            sock.close()


def test_probe_self_check_transfers_bytes():
    assert run_probe_self_check() == 0


def test_probe_client_rejects_invalid_server_port():
    with pytest.raises(ProbeClientError, match="포트"):
        normalize_server_url("127.0.0.1:not-a-port")


def test_disabled_probe_rejects_registration(tmp_path):
    service, _ = build_service(tmp_path, enabled=False)

    with pytest.raises(ProbeServiceError) as exc_info:
        register(service)

    assert exc_info.value.status_code == 503


@pytest.mark.parametrize("stream_count", [1, 4])
def test_full_probe_session_runs_both_directions_and_persists(tmp_path, monkeypatch, stream_count):
    monkeypatch.setattr(service_module, "PROBE_DURATIONS", (1,))
    service, gate = build_service(tmp_path)
    assert service.start() is True
    try:
        registration = register(service)
        created = service.create_session(
            agent_id=registration["agent_id"],
            direction="full",
            duration_seconds=1,
            stream_count=stream_count,
        )
        job_response = service.next_job(
            registration["agent_id"], registration["agent_token"], "127.0.0.1"
        )
        job = job_response["job"]
        assert job["session_id"] == created["session_id"]

        first = run_client_phase(service, registration, job, "upload")
        assert first["status"] == "attaching"
        completed = run_client_phase(service, registration, job, "download")
        assert completed["status"] == "completed"
        assert completed["results"]["upload"]["receiver"]["bytes"] > 0
        assert completed["results"]["download"]["receiver"]["bytes"] > 0
        assert gate.is_available() is True

        result_path = service.result_path_for(created["session_id"])
        saved = json.loads(result_path.read_text(encoding="utf-8"))
        assert saved["status"] == "completed"
        assert "session_token" not in result_path.read_text(encoding="utf-8")
        rows = service.config.log_path.read_text(encoding="utf-8-sig").splitlines()
        assert len(rows) == 3
    finally:
        service.stop()


def test_probe_cancel_releases_global_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "PROBE_DURATIONS", (1,))
    service, gate = build_service(tmp_path)
    assert service.start() is True
    try:
        registration = register(service)
        created = service.create_session(
            agent_id=registration["agent_id"],
            direction="upload",
            duration_seconds=1,
            stream_count=1,
        )

        cancelled = service.cancel_session(created["session_id"])

        assert cancelled["status"] == "cancelled"
        assert gate.is_available() is True
        assert service.result_path_for(created["session_id"]).exists()
    finally:
        service.stop()


def test_probe_stream_attach_timeout_fails_session_and_releases_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "PROBE_DURATIONS", (1,))
    service, gate = build_service(tmp_path, attach_timeout=0.05)
    assert service.start() is True
    try:
        registration = register(service)
        created = service.create_session(
            agent_id=registration["agent_id"],
            direction="upload",
            duration_seconds=1,
            stream_count=1,
        )
        service.next_job(registration["agent_id"], registration["agent_token"], "127.0.0.1")

        time.sleep(0.15)
        status = service.session_status(created["session_id"])

        assert status["status"] == "failed"
        assert "연결 시간이 초과" in status["error"]
        assert gate.is_available() is True
    finally:
        service.stop()


def test_probe_storage_failure_does_not_leave_measurement_gate_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "PROBE_DURATIONS", (1,))
    service, gate = build_service(tmp_path)
    assert service.start() is True
    try:
        registration = register(service)
        created = service.create_session(
            agent_id=registration["agent_id"],
            direction="upload",
            duration_seconds=1,
            stream_count=1,
        )

        def fail_persist(_session):
            raise OSError("disk full")

        monkeypatch.setattr(service, "_persist_result", fail_persist)

        result = service.cancel_session(created["session_id"])

        assert result["status"] == "failed"
        assert "결과 저장 실패" in result["error"]
        assert gate.is_available() is True
    finally:
        service.stop()


@pytest.mark.skipif(os.name != "nt", reason="Windows TCP_INFO 전용 검증")
def test_windows_tcp_info_returns_live_socket_statistics():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=3)
    server, _ = listener.accept()
    try:
        client.sendall(b"probe")
        assert server.recv(5) == b"probe"

        telemetry = snapshot_tcp_info(client)

        assert telemetry["available"] is True
        assert telemetry["rtt_us"] >= 0
        assert telemetry["cwnd_bytes"] > 0
        assert telemetry["bytes_out"] >= 5
    finally:
        client.close()
        server.close()
        listener.close()
