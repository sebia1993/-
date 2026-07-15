import csv
import io
import os
import threading
import time
from pathlib import Path
from zipfile import ZipFile

import pytest

import app as app_module
from app_version import APP_VERSION
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
from network_measurement import NetworkMeasurementGate
from network_probe.models import PROBE_PROTOCOL_VERSION
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


def test_tcp_probe_is_enabled_by_default_when_setting_is_missing(tmp_path):
    config = load_config(write_config(tmp_path))

    assert config.network_probe_enabled is True
    assert config.network_probe_port == 5201


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


def test_upload_fsyncs_temporary_file_before_atomic_replace(app_client, monkeypatch):
    client, config, _ = app_client
    events = []
    original_fsync = os.fsync
    original_replace = os.replace

    def recording_fsync(file_descriptor):
        events.append("fsync")
        return original_fsync(file_descriptor)

    def recording_replace(source, destination):
        if Path(source).suffix == ".part":
            events.append("replace")
            assert "fsync" in events
        return original_replace(source, destination)

    monkeypatch.setattr(app_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(app_module.os, "replace", recording_replace)

    response = post_file(client, filename="atomic.txt", content=b"complete")

    assert response.status_code == 200
    assert events.index("fsync") < events.index("replace")
    assert (config.storage_root / "atomic.txt").read_bytes() == b"complete"
    assert list(config.storage_root.rglob(f"{app_module.UPLOAD_ARTIFACT_PREFIX}*")) == []


def test_upload_replace_failure_removes_temporary_artifacts(app_client, monkeypatch):
    client, config, _ = app_client
    original_replace = os.replace

    def fail_upload_replace(source, destination):
        if Path(source).suffix == ".part":
            raise OSError("replace failed")
        return original_replace(source, destination)

    monkeypatch.setattr(app_module.os, "replace", fail_upload_replace)

    with pytest.raises(OSError, match="replace failed"):
        post_file(client, filename="incomplete.txt", content=b"partial")

    assert not (config.storage_root / "incomplete.txt").exists()
    assert list(config.storage_root.rglob(f"{app_module.UPLOAD_ARTIFACT_PREFIX}*")) == []


def test_cleanup_stale_upload_artifacts_only_removes_old_project_files(tmp_path):
    storage_root = tmp_path / "uploads"
    nested = storage_root / "case-001"
    nested.mkdir(parents=True)
    old_part = nested / f"{app_module.UPLOAD_ARTIFACT_PREFIX}old.part"
    old_lock = nested / f"{app_module.UPLOAD_ARTIFACT_PREFIX}old.lock"
    recent_part = nested / f"{app_module.UPLOAD_ARTIFACT_PREFIX}recent.part"
    user_part = nested / "capture.part"
    for path in (old_part, old_lock, recent_part, user_part):
        path.write_bytes(b"data")
    os.utime(old_part, (100, 100))
    os.utime(old_lock, (100, 100))

    removed = app_module.cleanup_stale_upload_artifacts(
        storage_root,
        older_than_seconds=100,
        now=300,
    )

    assert removed == 2
    assert not old_part.exists()
    assert not old_lock.exists()
    assert recent_part.exists()
    assert user_part.exists()


def test_upload_log_reader_waits_for_in_progress_writer(app_client, monkeypatch):
    _, config, _ = app_client
    writer_started = threading.Event()
    allow_writer = threading.Event()
    reader_finished = threading.Event()
    errors = []
    observed_rows = []
    original_append = app_module._append_csv_row_with_rollback

    def blocking_append(log_path, fieldnames, row):
        writer_started.set()
        if not allow_writer.wait(timeout=3):
            raise TimeoutError("test writer was not released")
        return original_append(log_path, fieldnames, row)

    def write_log():
        try:
            app_module.append_upload_log(
                {
                    "upload_id": "concurrent-log-entry",
                    "uploaded_at": "2026-07-15 12:00:00 +0900",
                    "original_filename": "concurrent.txt",
                    "stored_filename": "concurrent.txt",
                    "storage_subdir": "",
                    "storage_path": str(config.storage_root / "concurrent.txt"),
                    "memo": "",
                    "download_url": "http://files.local:8000/download/concurrent-log-entry",
                },
                config,
            )
        except BaseException as exc:
            errors.append(exc)

    def read_log():
        try:
            observed_rows.extend(read_upload_log(config))
        except BaseException as exc:
            errors.append(exc)
        finally:
            reader_finished.set()

    monkeypatch.setattr(app_module, "_append_csv_row_with_rollback", blocking_append)
    writer = threading.Thread(target=write_log)
    reader = threading.Thread(target=read_log)
    writer.start()
    assert writer_started.wait(timeout=2)
    reader.start()

    assert reader_finished.wait(timeout=0.1) is False
    allow_writer.set()
    writer.join(timeout=3)
    reader.join(timeout=3)

    assert not writer.is_alive()
    assert not reader.is_alive()
    assert errors == []
    assert [row["upload_id"] for row in observed_rows] == ["concurrent-log-entry"]


def test_upload_log_failure_removes_saved_file(app_client, monkeypatch):
    client, config, _ = app_client

    def fail_append_upload_log(row, active_config):
        raise OSError("log write failed")

    monkeypatch.setattr(app_module, "append_upload_log", fail_append_upload_log)

    with pytest.raises(OSError, match="log write failed"):
        post_file(client, filename="orphan.txt", content=b"orphan")

    assert not (config.storage_root / "orphan.txt").exists()


def test_upload_partial_log_write_failure_rolls_back_csv_and_file(app_client, monkeypatch):
    client, config, _ = app_client
    original_log = config.log_path.read_bytes()
    original_writerow = csv.DictWriter.writerow

    def write_then_fail(writer, row):
        original_writerow(writer, row)
        raise OSError("partial log write")

    monkeypatch.setattr(csv.DictWriter, "writerow", write_then_fail)

    with pytest.raises(OSError, match="partial log write"):
        post_file(client, filename="partial.txt", content=b"partial")

    assert not (config.storage_root / "partial.txt").exists()
    assert config.log_path.read_bytes() == original_log


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


def test_concurrent_duplicate_upload_does_not_overwrite_without_confirmation(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    flask_app = create_app(config_path)
    flask_app.config.update(TESTING=True)
    config = load_config(config_path)
    barrier = threading.Barrier(2)
    original_generate_upload_id = app_module.generate_upload_id
    responses = []
    errors = []

    def synchronized_generate_upload_id(now=None):
        barrier.wait(timeout=3)
        return original_generate_upload_id(now)

    def upload(content):
        try:
            with flask_app.test_client() as client:
                response = post_file(client, filename="same.txt", content=content)
                responses.append(response.status_code)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(app_module, "generate_upload_id", synchronized_generate_upload_id)
    threads = [
        threading.Thread(target=upload, args=(content,))
        for content in (b"first", b"second")
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(responses) == [200, 409]
    rows = read_upload_log(config)
    assert len(rows) == 1
    assert len({row["storage_path"] for row in rows}) == 1
    assert Path(rows[0]["storage_path"]).read_bytes() in {b"first", b"second"}


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


def test_delete_log_write_failure_preserves_file_and_record(app_client, monkeypatch):
    client, config, _ = app_client
    post_file(client, filename="keep-on-failure.txt", content=b"keep me")
    row = read_upload_log(config)[0]

    def fail_writerows(_writer, _rows):
        raise OSError("log write failed")

    monkeypatch.setattr(csv.DictWriter, "writerows", fail_writerows)

    with pytest.raises(OSError, match="log write failed"):
        client.post(
            f"/delete/{row['upload_id']}",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

    assert Path(row["storage_path"]).read_bytes() == b"keep me"
    assert read_upload_log(config) == [row]


def test_delete_file_failure_restores_upload_record(app_client, monkeypatch):
    client, config, _ = app_client
    post_file(client, filename="locked.txt", content=b"locked")
    row = read_upload_log(config)[0]
    file_path = Path(row["storage_path"])
    original_unlink = Path.unlink

    def fail_target_unlink(path, *args, **kwargs):
        if path == file_path:
            raise PermissionError("file is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target_unlink)

    with pytest.raises(PermissionError, match="file is locked"):
        client.post(
            f"/delete/{row['upload_id']}",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

    assert file_path.read_bytes() == b"locked"
    assert read_upload_log(config) == [row]


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
    assert "최근 전송 속도" in body
    assert "측정 취소" in body
    assert "HTTP 전송 측정" in body
    assert "측정 종료 기준" in body
    assert "데이터량" in body
    assert "측정 시간" in body
    assert "서버 웹 응답시간" in body
    assert "data-sustained-action" in body
    assert "최근 3초 평균 속도" in body
    assert "data-sustained-completed" in body
    assert "data-sustained-technical-details" in body
    assert "Excel 결과 받기" in body
    assert "data-sustained-excel" in body
    assert "data-probe-excel" in body
    assert "data-probe-json" not in body
    assert "data-sustained-json" not in body
    assert "data-sustained-stream" not in body
    assert "1GB 예상 시간" in body
    assert "TCP 전송 성능 측정" in body
    assert "고급 비교 측정" in body
    assert "4개 스트림 비교 측정" in body
    assert "data-probe-four-stream" in body
    assert "data-probe-stream=" not in body
    assert "data-probe-chart-panel" in body
    assert 'data-sustained-chart="upload"' in body
    assert 'data-sustained-chart="download"' in body
    assert 'data-probe-chart="upload"' in body
    assert 'data-probe-chart="download"' in body
    assert "throughput_chart.js" in body
    assert body.count("기술 상세 보기") == 2
    assert "업로드 실제 수신 속도" not in body
    assert "다운로드 실제 수신 속도" not in body
    assert "왕복 지연시간(RTT)" not in body
    assert "data-probe-cwnd" not in body
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


def test_network_check_upload_session_automatically_expires_and_releases_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "NETWORK_CHECK_UPLOAD_SESSION_TTL_SECONDS", 0.05)
    config_path = write_config(tmp_path)
    gate = NetworkMeasurementGate()
    flask_app = create_app(config_path, measurement_gate=gate)
    flask_app.config.update(TESTING=True)
    config = load_config(config_path)

    with flask_app.test_client() as client:
        started = client.post("/network-check/upload/start?size_mb=10")
    assert started.status_code == 200

    deadline = time.perf_counter() + 1
    while not gate.is_available() and time.perf_counter() < deadline:
        time.sleep(0.01)

    assert gate.is_available() is True
    rows = read_network_check_log(config)
    assert len(rows) == 1
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
    assert "formatOneGigabyteEstimate" in script
    assert "전송한 데이터" in script


def test_sustained_network_js_uses_regular_post_chunks():
    script = Path("static/network_sustained.js").read_text(encoding="utf-8")

    assert "duplex" not in script
    assert "new ReadableStream" not in script
    assert "AbortController" in script
    assert "data-sustained-action" in script
    assert "latency_samples_ms" in script
    assert "window.confirm" in script
    assert "data-sustained-excel" in script
    assert "result.excel_url" in script
    assert "data-sustained-json" not in script
    assert "LATENCY_PROGRESS_PERCENT = 5" in script
    assert "MEASUREMENT_PROGRESS_PERCENT = 95" in script
    assert "MAX_IN_PROGRESS_PERCENT = 99.9" in script
    assert "createSustainedProgress" in script
    assert "requestAnimationFrame(tick)" in script
    assert "style.transform = `scaleX(" in script
    assert "Math.max(currentPercent" in script
    assert "result.status !== \"success\"" in script
    assert "progress.terminate(cancellationRequested" in script
    assert "function setPhase(" not in script
    assert "HTTP_STREAM_COUNT = 1" in script
    assert "stream_count: HTTP_STREAM_COUNT" in script
    assert "data-sustained-stream" not in script
    assert "selectedStreams" not in script
    assert "slice(-3)" in script
    assert "data-sustained-live-speed" in script
    assert "data-sustained-completed" in script
    assert "낮을수록 측정 중 속도가 일정함" in script
    assert "InternalUploadThroughputChart" in script
    assert "syncCharts" in script
    assert script.index("if (result.excel_url)") < script.index('if (result.status !== "success")')


def test_probe_network_js_uses_audience_friendly_summary():
    script = Path("static/network_probe.js").read_text(encoding="utf-8")

    assert "formatDirectionDifference" in script
    assert "formatRetransmission" in script
    assert "전체 송신량의" in script
    assert "운영체제에서 제공하지 않음" in script
    assert "측정 PC → 서버" in script
    assert "서버 → 측정 PC" in script
    assert "data-probe-four-stream" in script
    assert "fourStreamToggle.checked ? 4 : 1" in script
    assert "업로드·다운로드 평균 속도 차이" in script
    assert "TCP 왕복시간(RTT)" in script
    assert "1초 구간 최저 속도" in script
    assert "1초 구간 최고 속도" in script
    assert "data-probe-chart-panel" in script
    assert "data-probe-technical-details" in script
    assert "data-probe-cwnd" not in script
    assert "connectivity_status === \"ready\"" in script
    assert "TCP ${agent.probe_port} 연결 준비 완료" in script
    assert "약 20초 안에 자동 재점검" in script
    assert "최신 ZIP 사용 권장" in script
    assert "createProbeProgress" in script
    assert "Math.min(99.5" in script
    assert "animateTo(100, 300)" in script
    assert "style.transform = `scaleX(" in script


def test_shared_throughput_chart_has_readable_axes_and_interaction():
    script = Path("static/throughput_chart.js").read_text(encoding="utf-8")

    assert "niceMaximum" in script
    assert "평균 ${formatMbps(average)}" in script
    assert "최저 ${formatMbps(minimumValue)}" in script
    assert "최고 ${formatMbps(maximumValue)}" in script
    assert "pointermove" in script
    assert "ArrowLeft" in script
    assert "MB/s" in script
    assert "ResizeObserver" in script


def test_sustained_progress_uses_its_own_time_based_style():
    stylesheet = Path("static/style.css").read_text(encoding="utf-8")

    assert ".progress-bar[data-sustained-progress-bar]" in stylesheet
    assert ".progress-bar[data-probe-progress-bar]" in stylesheet
    assert "transform-origin: left center" in stylesheet
    assert "transition: none" in stylesheet
    assert ".chart-tooltip" in stylesheet


def test_windows_release_checksum_uses_portable_lf_line_ending():
    script = Path("tools/build_windows_release.ps1").read_text(encoding="utf-8")

    assert "[System.IO.File]::WriteAllText($ShaPath" in script
    assert '"$Hash  $PackageName.zip`n"' in script
    assert "ReadAllBytes($ShaPath) -contains 13" in script
    assert "Set-Content -Path $ShaPath" not in script


def test_windows_release_build_requires_source_version_match():
    script = Path("tools/build_windows_release.ps1").read_text(encoding="utf-8")

    assert 'from app_version import APP_VERSION' in script
    assert '$SourceVersion -ne $Version' in script
    assert 'does not match requested release' in script


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


def test_health_endpoint_identifies_app_and_active_port(app_client):
    client, _, _ = app_client

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json == {
        "app": "internal-upload",
        "status": "ok",
        "port": 8000,
        "version": APP_VERSION,
        "probe_protocol_version": PROBE_PROTOCOL_VERSION,
    }
    assert response.headers["Cache-Control"] == "no-store"


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
                "start_internal_upload.cmd",
                "start_tcp_probe_client.cmd",
                "config.ini",
                "data/upload_log.csv",
                "data/network_check_log.csv",
                "data/network_check_session_log.csv",
                "data/network_probe_log.csv",
            }
        ):
            archive.writestr(name, "sample")
        archive.writestr("README_START_HERE_KO.txt", "사내 업로드 v0.1.0 Windows 실행 ZIP")
        archive.writestr(
            "start_internal_upload.cmd",
            "실제 접속 주소를 표시하고 config.ini에 저장합니다.\nInternalUpload.exe",
        )
        archive.writestr(
            "start_tcp_probe_client.cmd",
            'set /p "SERVER_URL=server: "\nInternalUpload.exe --probe-client --server "%SERVER_URL%"',
        )
        archive.writestr(
            "config.ini",
            "[app]\nCONFIG_VERSION=2\n\n[network_probe]\nENABLED=true\nPORT=5201\n",
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
