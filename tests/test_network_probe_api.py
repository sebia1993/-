from __future__ import annotations

import io
import socket
import uuid
from zipfile import ZipFile

from app import build_probe_config, create_app, load_config, normalize_ip
from network_measurement import NetworkMeasurementGate
from network_probe.models import PROBE_PROTOCOL_VERSION
from network_probe.service import ProbeService


def available_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def write_config(tmp_path, *, enabled: bool) -> str:
    path = tmp_path / "config.ini"
    path.write_text(
        "\n".join(
            [
                "[app]",
                "HOST=127.0.0.1",
                "PORT=8000",
                "BASE_URL=http://127.0.0.1:8000",
                "STORAGE_ROOT=uploads",
                "DELETE_ALLOWED_IPS=127.0.0.1",
                "RECENT_LIMIT=50",
                "",
                "[network_probe]",
                f"ENABLED={'true' if enabled else 'false'}",
                f"PORT={available_port()}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return str(path)


def test_disabled_probe_api_and_ui_are_available(tmp_path):
    config_path = write_config(tmp_path, enabled=False)
    app = create_app(config_path)
    client = app.test_client()

    status = client.get("/api/network-probe/status")
    registration = client.post(
        "/api/network-probe/agents/register",
        json={
            "agent_id": uuid.uuid4().hex,
            "hostname": "TEST-PC",
            "server_host": "127.0.0.1",
            "protocol_version": PROBE_PROTOCOL_VERSION,
        },
    )
    index = client.get("/")

    assert status.status_code == 200
    assert status.get_json()["enabled"] is False
    assert status.get_json()["client_package_available"] is False
    assert registration.status_code == 503
    package = client.get("/api/network-probe/client-package.zip")
    assert package.status_code == 503
    index_text = index.get_data(as_text=True)
    assert "TCP 전송 성능 측정" in index_text
    assert "Windows 클라이언트 ZIP 받기" in index_text
    assert 'data-probe-client-package-url="/api/network-probe/client-package.zip"' in index_text
    assert "network_probe.js" in index_text


def test_probe_api_registers_agent_and_shares_measurement_gate(tmp_path):
    config_path = write_config(tmp_path, enabled=True)
    app_config = load_config(config_path)
    gate = NetworkMeasurementGate()
    service = ProbeService(
        config=build_probe_config(app_config),
        measurement_gate=gate,
        normalize_ip=normalize_ip,
    )
    assert service.start() is True
    try:
        app = create_app(config_path, probe_service=service, measurement_gate=gate)
        client = app.test_client()
        agent_id = uuid.uuid4().hex
        registration = client.post(
            "/api/network-probe/agents/register",
            json={
                "agent_id": agent_id,
                "hostname": "TEST-PC",
                "server_host": "127.0.0.1",
                "protocol_version": PROBE_PROTOCOL_VERSION,
            },
        )
        assert registration.status_code == 200
        token = registration.get_json()["agent_token"]
        assert client.get("/api/network-probe/agents").get_json()["agents"][0]["hostname"] == "TEST-PC"
        status = client.get("/api/network-probe/status").get_json()
        assert status["client_package_available"] is False
        assert "Release EXE" in status["client_package_error"]

        created = client.post(
            "/api/network-probe/sessions",
            json={"agent_id": agent_id, "direction": "upload", "duration_seconds": 10, "stream_count": 1},
        )
        assert created.status_code == 202
        session_id = created.get_json()["session_id"]

        blocked = client.post(
            "/network-check/sustained/sessions",
            json={"direction": "download", "duration_seconds": 10, "stream_count": 1},
        )
        assert blocked.status_code == 409
        assert client.get("/network-check/download?size_mb=10").status_code == 409
        assert client.post("/network-check/upload/start?size_mb=10").status_code == 409

        unauthorized = client.get(
            f"/api/network-probe/sessions/{session_id}/control?agent_id={agent_id}",
            headers={"Authorization": "Bearer wrong"},
        )
        assert unauthorized.status_code == 401

        job = client.get(
            f"/api/network-probe/agents/{agent_id}/jobs/next",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert job.status_code == 200
        assert job.get_json()["job"]["session_id"] == session_id

        cancelled = client.post(f"/api/network-probe/sessions/{session_id}/cancel")
        assert cancelled.get_json()["status"] == "cancelled"
        assert gate.is_available() is True
    finally:
        service.stop()


def test_probe_client_package_download_embeds_current_server_address(tmp_path):
    config_path = write_config(tmp_path, enabled=True)
    app_config = load_config(config_path)
    gate = NetworkMeasurementGate()
    service = ProbeService(
        config=build_probe_config(app_config),
        measurement_gate=gate,
        normalize_ip=normalize_ip,
    )
    executable = tmp_path / "InternalUpload.exe"
    executable.write_bytes(b"MZ-api-client-test")
    assert service.start() is True
    try:
        app = create_app(
            config_path,
            probe_service=service,
            measurement_gate=gate,
            probe_client_executable_path=executable,
        )
        client = app.test_client()
        headers = {"Host": "SERVER-PC:8123"}

        status = client.get("/api/network-probe/status", headers=headers)
        package = client.get("/api/network-probe/client-package.zip", headers=headers)

        status_payload = status.get_json()
        assert status_payload["client_package_available"] is True
        assert status_payload["client_package_server_url"] == "http://server-pc:8123"
        assert status_payload["client_package_url"] == "/api/network-probe/client-package.zip"
        assert package.status_code == 200
        assert package.mimetype == "application/zip"
        assert package.headers["Cache-Control"] == "no-store"
        assert "internal-upload-client_server-pc.zip" in package.headers["Content-Disposition"]
        with ZipFile(io.BytesIO(package.data)) as archive:
            command = archive.read(
                "InternalUpload_Client_server-pc/start_tcp_probe_client.cmd"
            ).decode("utf-8-sig")
        assert '--server "http://server-pc:8123"' in command
        assert "set /p" not in command.lower()

        rejected = client.get(
            "/api/network-probe/client-package.zip",
            headers={"Host": "server-pc&calc:8000"},
        )
        assert rejected.status_code == 400
    finally:
        service.stop()


def test_probe_client_package_rejects_missing_executable(tmp_path):
    config_path = write_config(tmp_path, enabled=True)
    app_config = load_config(config_path)
    gate = NetworkMeasurementGate()
    service = ProbeService(
        config=build_probe_config(app_config),
        measurement_gate=gate,
        normalize_ip=normalize_ip,
    )
    assert service.start() is True
    try:
        app = create_app(
            config_path,
            probe_service=service,
            measurement_gate=gate,
            probe_client_executable_path=tmp_path / "missing.exe",
        )
        response = app.test_client().get(
            "/api/network-probe/client-package.zip",
            headers={"Host": "server-pc:8000"},
        )
        assert response.status_code == 503
        assert "실행 파일" in response.get_json()["error"]
    finally:
        service.stop()
