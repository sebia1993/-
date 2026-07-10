import csv
import io
from pathlib import Path
from zipfile import ZipFile

import pytest

import app as app_module
from app import (
    NETWORK_CHECK_FIELDS,
    build_download_url,
    create_app,
    is_delete_allowed,
    is_loopback_url,
    load_config,
    read_network_check_log,
    read_upload_log,
    resolve_storage_path,
    run_smoke_check,
)
from network_sustained import SUSTAINED_LOG_FIELDS
from network_probe.service import PROBE_LOG_FIELDS
from tools.verify_release_zip import REQUIRED_FILES, verify_zip


def write_config(tmp_path: Path, *, base_url: str = "http://files.local:8000") -> Path:
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "\n".join(
            [
                "[app]",
                "HOST=0.0.0.0",
                "PORT=8000",
                f"BASE_URL={base_url}",
                "STORAGE_ROOT=uploads",
                "DELETE_ALLOWED_IPS=127.0.0.1,::1,10.10.10.5",
                "RECENT_LIMIT=50",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


@pytest.fixture()
def app_client(tmp_path):
    config_path = write_config(tmp_path)
    app = create_app(config_path)
    app.config.update(TESTING=True)
    return app.test_client(), load_config(config_path), tmp_path


def post_file(client, filename="장애로그.txt", content=b"hello", **fields):
    data = {
        "file": (io.BytesIO(content), filename),
        "storage_subdir": fields.pop("storage_subdir", ""),
        "memo": fields.pop("memo", ""),
    }
    data.update(fields)
    return client.post("/upload", data=data, content_type="multipart/form-data")


def test_base_url_download_link(tmp_path):
    config = load_config(write_config(tmp_path, base_url="http://10.10.10.25:8000"))
    assert build_download_url("abc123", config) == "http://10.10.10.25:8000/download/abc123"


def test_auto_ip_download_link(tmp_path):
    config = load_config(write_config(tmp_path, base_url=""))
    assert build_download_url("abc123", config, ip_address="10.10.10.25") == (
        "http://10.10.10.25:8000/download/abc123"
    )


def test_loopback_url_warning_detection():
    assert is_loopback_url("http://localhost:8000/download/a")
    assert is_loopback_url("http://127.0.0.1:8000/download/a")
    assert not is_loopback_url("http://10.10.10.25:8000/download/a")


def test_storage_path_rejects_outside_root(tmp_path):
    config = load_config(write_config(tmp_path))
    with pytest.raises(ValueError):
        resolve_storage_path("../outside", config)
    with pytest.raises(ValueError):
        resolve_storage_path("C:\\temp", config)


def test_upload_saves_file_and_csv(app_client):
    client, config, _ = app_client
    response = post_file(client, memo="장애 로그", storage_subdir="case-001")

    assert response.status_code == 200
    rows = read_upload_log(config)
    assert len(rows) == 1
    row = rows[0]
    assert row["original_filename"] == "장애로그.txt"
    assert row["storage_subdir"] == "case-001"
    assert row["memo"] == "장애 로그"
    assert Path(row["storage_path"]).read_bytes() == b"hello"


def test_upload_log_failure_removes_saved_file(app_client, monkeypatch):
    client, config, _ = app_client

    def fail_append_upload_log(row, active_config):
        raise OSError("log write failed")

    monkeypatch.setattr(app_module, "append_upload_log", fail_append_upload_log)

    with pytest.raises(OSError, match="log write failed"):
        post_file(client, filename="orphan.txt", content=b"orphan")

    assert not (config.storage_root / "orphan.txt").exists()


def test_memo_is_optional(app_client):
    client, config, _ = app_client
    response = post_file(client, filename="memo-optional.txt")

    assert response.status_code == 200
    assert read_upload_log(config)[0]["memo"] == ""


def test_duplicate_requires_confirmation_then_adds_id(app_client):
    client, config, _ = app_client
    assert post_file(client, filename="same.txt", content=b"one").status_code == 200

    conflict = post_file(client, filename="same.txt", content=b"two")
    assert conflict.status_code == 409
    assert "이미 존재".encode("utf-8") in conflict.data
    assert len(read_upload_log(config)) == 1

    confirmed = post_file(
        client,
        filename="same.txt",
        content=b"two",
        confirm_duplicate="1",
    )
    assert confirmed.status_code == 200
    rows = read_upload_log(config)
    assert len(rows) == 2
    assert rows[0]["stored_filename"].endswith("_same.txt")
    assert rows[0]["stored_filename"] != "same.txt"


def test_download_by_id(app_client):
    client, config, _ = app_client
    post_file(client, filename="download.txt", content=b"download me")
    upload_id = read_upload_log(config)[0]["upload_id"]

    response = client.get(f"/download/{upload_id}")

    assert response.status_code == 200
    assert response.data == b"download me"


def test_delete_requires_allowed_ip(app_client):
    client, config, _ = app_client
    post_file(client, filename="delete.txt", content=b"delete me")
    row = read_upload_log(config)[0]

    denied = client.post(
        f"/delete/{row['upload_id']}",
        environ_overrides={"REMOTE_ADDR": "10.10.10.6"},
    )
    assert denied.status_code == 403
    assert Path(row["storage_path"]).exists()
    assert len(read_upload_log(config)) == 1

    allowed = client.post(
        f"/delete/{row['upload_id']}",
        environ_overrides={"REMOTE_ADDR": "10.10.10.5"},
    )
    assert allowed.status_code == 302
    assert not Path(row["storage_path"]).exists()
    assert read_upload_log(config) == []


def test_delete_button_only_for_allowed_ip(app_client):
    client, config, _ = app_client
    post_file(client, filename="button.txt")

    allowed = client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    denied = client.get("/", environ_overrides={"REMOTE_ADDR": "10.10.10.6"})

    assert f"/delete/{read_upload_log(config)[0]['upload_id']}".encode() in allowed.data
    assert b"/delete/" not in denied.data


def test_delete_allowed_ip_normalization(tmp_path):
    config = load_config(write_config(tmp_path))
    assert is_delete_allowed("::ffff:127.0.0.1", config)
    assert not is_delete_allowed("10.10.10.6", config)


def test_network_check_tab_and_size_options(app_client):
    client, _, _ = app_client

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "네트워크 체크" in body
    assert "1024MB" in body
    assert "평균 속도" in body
    assert "구간 속도" in body
    assert "측정 취소" in body
    assert "지속 측정" in body
    assert "HTTP 응답시간" in body
    assert "data-sustained-action" in body
    assert "/network-check/upload" in body
    assert "/network-check/download" in body


def test_network_check_download_streams_and_logs(app_client, monkeypatch):
    client, config, _ = app_client
    monkeypatch.setattr(app_module, "MEGABYTE", 1024)

    response = client.get("/network-check/download?size_mb=10")

    assert response.status_code == 200
    assert len(response.data) == 10 * 1024
    rows = read_network_check_log(config)
    assert rows[0]["direction"] == "download"
    assert rows[0]["size_mb"] == "10"
    assert rows[0]["bytes_transferred"] == str(10 * 1024)
    assert rows[0]["status"] == "success"
    assert read_upload_log(config) == []


def test_network_check_upload_discards_body_and_logs(app_client, monkeypatch):
    client, config, _ = app_client
    monkeypatch.setattr(app_module, "MEGABYTE", 1024)

    started = client.post("/network-check/upload/start?size_mb=10")
    assert started.status_code == 200
    session_id = started.json["session_id"]

    first_chunk = client.post(
        f"/network-check/upload/chunk/{session_id}",
        data=b"x" * (6 * 1024),
        content_type="application/octet-stream",
    )
    assert first_chunk.status_code == 200
    assert first_chunk.json["bytes_received"] == 6 * 1024
    assert read_network_check_log(config) == []

    second_chunk = client.post(
        f"/network-check/upload/chunk/{session_id}",
        data=b"x" * (4 * 1024),
        content_type="application/octet-stream",
    )
    assert second_chunk.status_code == 200
    assert second_chunk.json["complete"]

    finished = client.post(f"/network-check/upload/finish/{session_id}")
    assert finished.status_code == 200
    assert finished.json["status"] == "success"
    rows = read_network_check_log(config)
    assert rows[0]["direction"] == "upload"
    assert rows[0]["bytes_transferred"] == str(10 * 1024)
    assert rows[0]["status"] == "success"
    assert read_upload_log(config) == []
    assert list(config.storage_root.rglob("*")) == []


def test_network_check_rejects_invalid_size(app_client):
    client, config, _ = app_client

    response = client.post("/network-check/upload/start?size_mb=11")

    assert response.status_code == 400
    assert read_network_check_log(config) == []


def test_network_check_upload_rejects_missing_session(app_client):
    client, config, _ = app_client

    chunk = client.post(
        "/network-check/upload/chunk/missing",
        data=b"x",
        content_type="application/octet-stream",
    )
    finished = client.post("/network-check/upload/finish/missing")

    assert chunk.status_code == 404
    assert finished.status_code == 404
    assert read_network_check_log(config) == []


def test_network_check_upload_logs_incomplete_body(app_client, monkeypatch):
    client, config, _ = app_client
    monkeypatch.setattr(app_module, "MEGABYTE", 1024)

    started = client.post("/network-check/upload/start?size_mb=10")
    session_id = started.json["session_id"]
    chunk = client.post(
        f"/network-check/upload/chunk/{session_id}",
        data=b"x" * (9 * 1024),
        content_type="application/octet-stream",
    )
    finished = client.post(f"/network-check/upload/finish/{session_id}")

    assert chunk.status_code == 200
    assert finished.status_code == 400
    assert finished.json["status"] == "failure"
    rows = read_network_check_log(config)
    assert rows[0]["direction"] == "upload"
    assert rows[0]["bytes_transferred"] == str(9 * 1024)
    assert rows[0]["status"] == "failure"


def test_network_check_upload_rejects_oversized_body(app_client, monkeypatch):
    client, config, _ = app_client
    monkeypatch.setattr(app_module, "MEGABYTE", 1024)

    started = client.post("/network-check/upload/start?size_mb=10")
    session_id = started.json["session_id"]
    response = client.post(
        f"/network-check/upload/chunk/{session_id}",
        data=b"x" * (11 * 1024),
        content_type="application/octet-stream",
    )

    assert response.status_code == 400
    assert response.json["status"] == "failure"
    rows = read_network_check_log(config)
    assert rows[0]["bytes_transferred"] == str(11 * 1024)
    assert rows[0]["status"] == "failure"


def test_network_check_js_avoids_request_stream_uploads():
    script = Path("static/network_check.js").read_text(encoding="utf-8")

    assert "ReadableStream" not in script
    assert "duplex" not in script


def test_network_check_js_has_speed_and_cancel_guards():
    script = Path("static/network_check.js").read_text(encoding="utf-8")

    assert "AbortController" in script
    assert "confirm" in script
    assert "1024MB" in script
    assert "MB/s" in script
    assert "data-average-speed" in script
    assert "data-interval-speed" in script
    assert "data-cancel-check" in script


def test_sustained_network_js_uses_regular_post_chunks():
    script = Path("static/network_sustained.js").read_text(encoding="utf-8")

    assert "duplex" not in script
    assert "new ReadableStream" not in script
    assert "AbortController" in script
    assert "data-sustained-action" in script
    assert "latency_samples_ms" in script
    assert "window.confirm" in script


def test_csv_header_is_utf8_sig(app_client):
    _, config, _ = app_client
    with config.log_path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle))
    assert header[0] == "upload_id"

    with config.network_check_log_path.open("r", encoding="utf-8-sig", newline="") as handle:
        network_header = next(csv.reader(handle))
    assert network_header == NETWORK_CHECK_FIELDS


def test_smoke_check_returns_success(tmp_path):
    config_path = write_config(tmp_path)
    assert run_smoke_check(config_path) == 0


def test_release_zip_verifier_accepts_expected_structure(tmp_path):
    zip_path = tmp_path / "internal-upload_v0.1.0_windows.zip"
    csv_header = (
        "upload_id,uploaded_at,original_filename,stored_filename,storage_subdir,"
        "storage_path,memo,download_url\n"
    )
    network_csv_header = ",".join(NETWORK_CHECK_FIELDS) + "\n"
    session_csv_header = ",".join(SUSTAINED_LOG_FIELDS) + "\n"
    probe_csv_header = ",".join(PROBE_LOG_FIELDS) + "\n"
    with ZipFile(zip_path, "w") as archive:
        for name in sorted(
            REQUIRED_FILES
            - {
                "README_START_HERE_KO.txt",
                "start_tcp_probe_client.cmd",
                "data/upload_log.csv",
                "data/network_check_log.csv",
                "data/network_check_session_log.csv",
                "data/network_probe_log.csv",
            }
        ):
            archive.writestr(name, "sample")
        archive.writestr("README_START_HERE_KO.txt", "사내 업로드 v0.1.0 Windows 실행 ZIP")
        archive.writestr(
            "start_tcp_probe_client.cmd",
            'set /p "SERVER_URL=server: "\nInternalUpload.exe --probe-client --server "%SERVER_URL%"',
        )
        archive.writestr("data/upload_log.csv", csv_header)
        archive.writestr("data/network_check_log.csv", network_csv_header)
        archive.writestr("data/network_check_session_log.csv", session_csv_header)
        archive.writestr("data/network_probe_log.csv", probe_csv_header)

    assert verify_zip(str(zip_path), "v0.1.0") == []


def test_release_zip_verifier_rejects_dev_artifacts(tmp_path):
    zip_path = tmp_path / "bad.zip"
    with ZipFile(zip_path, "w") as archive:
        for name in REQUIRED_FILES:
            archive.writestr(name, "v0.1.0")
        archive.writestr(".venv/Lib/site-packages/example.txt", "bad")

    errors = verify_zip(str(zip_path), "v0.1.0")
    assert any(".venv" in error for error in errors)


def test_release_zip_verifier_rejects_operational_network_result(tmp_path):
    zip_path = tmp_path / "bad-result.zip"
    with ZipFile(zip_path, "w") as archive:
        for name in REQUIRED_FILES:
            archive.writestr(name, "v0.3.0")
        archive.writestr("data/network_check_results/private-session.json", "{}")

    errors = verify_zip(str(zip_path), "v0.3.0")
    assert any("operational result" in error for error in errors)


def test_release_zip_verifier_rejects_operational_probe_result(tmp_path):
    zip_path = tmp_path / "bad-probe-result.zip"
    with ZipFile(zip_path, "w") as archive:
        for name in REQUIRED_FILES:
            archive.writestr(name, "v0.4.0-rc.1")
        archive.writestr("data/network_probe_results/private-session.json", "{}")

    errors = verify_zip(str(zip_path), "v0.4.0-rc.1")
    assert any("operational probe result" in error for error in errors)
