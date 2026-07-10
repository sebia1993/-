import csv
import json
from pathlib import Path

import pytest

from app import create_app
from network_sustained import (
    SUSTAINED_LOG_FIELDS,
    SustainedCheckError,
    SustainedCheckManager,
    SustainedCheckSettings,
    summarize_intervals,
)
from tests.test_app import write_config


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def create_manager(tmp_path: Path, clock: FakeClock, *, ttl: float = 300) -> SustainedCheckManager:
    return SustainedCheckManager(
        log_path=tmp_path / "data" / "network_check_session_log.csv",
        results_root=tmp_path / "data" / "network_check_results",
        settings=SustainedCheckSettings(session_ttl_seconds=ttl, download_chunk_bytes=1024),
        clock=clock,
    )


def test_summarize_intervals_calculates_stable_statistics():
    summary = summarize_intervals(
        byte_count=3_000_000,
        duration_seconds=3,
        interval_bytes=[1_000_000, 1_000_000, 1_000_000],
    )

    assert summary["average_mbps"] == 8.0
    assert summary["median_mbps"] == 8.0
    assert summary["min_mbps"] == 8.0
    assert summary["max_mbps"] == 8.0
    assert summary["variability_percent"] == 0.0


def test_manager_excludes_warmup_and_persists_result(tmp_path):
    clock = FakeClock()
    manager = create_manager(tmp_path, clock)
    session = manager.start_session(
        client_ip="10.0.0.10",
        direction="upload",
        duration_seconds=10,
        stream_count=1,
    )

    manager.begin_phase(session.session_id, session.client_ip, "upload", "warmup")
    manager.record_bytes(session.session_id, session.client_ip, "upload", 500_000)
    clock.advance(3.1)
    manager.begin_phase(session.session_id, session.client_ip, "upload", "measure")
    manager.record_bytes(session.session_id, session.client_ip, "upload", 1_000_000)
    clock.advance(1.1)
    manager.record_bytes(session.session_id, session.client_ip, "upload", 2_000_000)
    clock.advance(9)

    result = manager.complete(
        session.session_id,
        session.client_ip,
        {"latency_samples_ms": [2.0, 3.0, 4.0]},
    )

    assert result["directions"]["upload"]["bytes_transferred"] == 3_000_000
    assert result["http_latency"]["median_ms"] == 3.0
    result_path = tmp_path / "data" / "network_check_results" / f"{session.session_id}.json"
    assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "success"
    with (tmp_path / "data" / "network_check_session_log.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["bytes_transferred"] == "3000000"
    assert rows[0]["direction"] == "upload"


def test_manager_rejects_parallel_session_and_wrong_ip(tmp_path):
    clock = FakeClock()
    manager = create_manager(tmp_path, clock)
    session = manager.start_session(
        client_ip="10.0.0.10",
        direction="download",
        duration_seconds=10,
        stream_count=4,
    )

    with pytest.raises(SustainedCheckError) as conflict:
        manager.start_session(
            client_ip="10.0.0.11",
            direction="upload",
            duration_seconds=10,
            stream_count=1,
        )
    assert conflict.value.status_code == 409

    with pytest.raises(SustainedCheckError) as forbidden:
        manager.status(session.session_id, "10.0.0.11")
    assert forbidden.value.status_code == 403


def test_manager_expiry_releases_active_slot_and_records_failure(tmp_path):
    clock = FakeClock()
    manager = create_manager(tmp_path, clock, ttl=5)
    session = manager.start_session(
        client_ip="10.0.0.10",
        direction="upload",
        duration_seconds=10,
        stream_count=1,
    )
    clock.advance(6)

    replacement = manager.start_session(
        client_ip="10.0.0.11",
        direction="download",
        duration_seconds=10,
        stream_count=1,
    )

    assert replacement.session_id != session.session_id
    expired_path = tmp_path / "data" / "network_check_results" / f"{session.session_id}.json"
    assert json.loads(expired_path.read_text(encoding="utf-8"))["status"] == "failure"


@pytest.fixture()
def sustained_client(tmp_path):
    app = create_app(write_config(tmp_path))
    app.config.update(TESTING=True)
    return app.test_client(), tmp_path


def test_sustained_routes_validate_and_cancel(sustained_client):
    client, tmp_path = sustained_client
    invalid = client.post(
        "/network-check/sustained/sessions",
        json={"direction": "upload", "duration_seconds": 5, "stream_count": 1},
    )
    assert invalid.status_code == 400

    started = client.post(
        "/network-check/sustained/sessions",
        json={"direction": "upload", "duration_seconds": 10, "stream_count": 1},
    )
    assert started.status_code == 200
    session_id = started.json["session_id"]

    conflict = client.post(
        "/network-check/sustained/sessions",
        json={"direction": "download", "duration_seconds": 10, "stream_count": 1},
    )
    assert conflict.status_code == 409

    phase = client.post(
        f"/network-check/sustained/sessions/{session_id}/phase",
        json={"direction": "upload", "phase": "warmup"},
    )
    assert phase.status_code == 200
    uploaded = client.post(
        f"/network-check/sustained/sessions/{session_id}/upload/0",
        data=b"x" * 1024,
        content_type="application/octet-stream",
    )
    assert uploaded.status_code == 200

    cancelled = client.post(
        f"/network-check/sustained/sessions/{session_id}/cancel",
        json={"latency_samples_ms": [1.2, 1.4, 1.3]},
    )
    assert cancelled.status_code == 200
    assert cancelled.json["status"] == "cancelled"
    assert (tmp_path / "data" / "network_check_results" / f"{session_id}.json").exists()


def test_sustained_result_download_is_limited_to_origin_ip(sustained_client):
    client, _ = sustained_client
    started = client.post(
        "/network-check/sustained/sessions",
        json={"direction": "upload", "duration_seconds": 10, "stream_count": 1},
        environ_overrides={"REMOTE_ADDR": "10.0.0.10"},
    )
    session_id = started.json["session_id"]
    client.post(
        f"/network-check/sustained/sessions/{session_id}/cancel",
        json={},
        environ_overrides={"REMOTE_ADDR": "10.0.0.10"},
    )

    allowed = client.get(
        f"/network-check/sustained/results/{session_id}.json",
        environ_overrides={"REMOTE_ADDR": "10.0.0.10"},
    )
    denied = client.get(
        f"/network-check/sustained/results/{session_id}.json",
        environ_overrides={"REMOTE_ADDR": "10.0.0.11"},
    )

    assert allowed.status_code == 200
    assert allowed.json["session_id"] == session_id
    assert denied.status_code == 403


def test_sustained_csv_header_is_stable(sustained_client):
    _, tmp_path = sustained_client
    with (tmp_path / "data" / "network_check_session_log.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        assert next(csv.reader(handle)) == SUSTAINED_LOG_FIELDS
