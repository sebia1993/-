from __future__ import annotations

import csv
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

import app as app_module
from app_version import APP_VERSION
from network_measurement import NetworkMeasurementGate
from network_probe.models import PROBE_PROTOCOL_VERSION, ProbeConfig
from network_probe.service import ProbeService
from runtime_stability import DataDirectoryLock, InstanceLockError, ensure_csv_integrity


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def wait_for_path(path: Path, process: subprocess.Popen, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"fault worker exited early: stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.02)
    raise AssertionError(f"fault worker did not create ready marker: {path}")


def kill_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def start_worker(code: str, *args: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", code, *(str(arg) for arg in args)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_forced_process_exit_mid_upload_removes_dead_reservation_on_restart(tmp_path):
    storage_root = tmp_path / "uploads"
    ready_path = tmp_path / "upload-ready"
    code = """
import sys, time
from pathlib import Path
from app import reserve_upload_target
root, ready = map(Path, sys.argv[1:3])
reservation = reserve_upload_target(root, "fault.txt", confirm_duplicate=False)
reservation.temporary_path.write_bytes(b"partial-upload")
ready.write_text("ready", encoding="ascii")
time.sleep(60)
"""
    process = start_worker(code, storage_root, ready_path)
    try:
        wait_for_path(ready_path, process)
        assert len(list(storage_root.glob(f"{app_module.UPLOAD_ARTIFACT_PREFIX}*"))) == 2
        kill_process(process)

        removed = app_module.cleanup_stale_upload_artifacts(storage_root)

        assert removed == 2
        assert list(storage_root.glob(f"{app_module.UPLOAD_ARTIFACT_PREFIX}*")) == []
        assert not (storage_root / "fault.txt").exists()
    finally:
        kill_process(process)


def test_forced_process_exit_mid_csv_append_recovers_only_incomplete_tail(tmp_path):
    csv_path = tmp_path / "upload_log.csv"
    ready_path = tmp_path / "csv-ready"
    fieldnames = ["id", "memo"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerow(["complete", "preserve"])
    code = """
import os, sys, time
from pathlib import Path
path, ready = map(Path, sys.argv[1:3])
with path.open("ab") as handle:
    handle.write(b'broken,"unfinished')
    handle.flush()
    os.fsync(handle.fileno())
ready.write_text("ready", encoding="ascii")
time.sleep(60)
"""
    process = start_worker(code, csv_path, ready_path)
    try:
        wait_for_path(ready_path, process)
        kill_process(process)

        result = ensure_csv_integrity(csv_path, fieldnames)

        assert result.repaired is True
        assert result.backup_path is not None
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            assert list(csv.reader(handle)) == [fieldnames, ["complete", "preserve"]]
    finally:
        kill_process(process)


def test_data_directory_lock_recovers_after_owner_process_is_killed(tmp_path):
    lock_path = tmp_path / "data" / ".internal-upload.instance.lock"
    ready_path = tmp_path / "lock-ready"
    code = """
import sys, time
from pathlib import Path
from runtime_stability import DataDirectoryLock
lock_path, ready = map(Path, sys.argv[1:3])
lock = DataDirectoryLock(lock_path)
lock.acquire()
ready.write_text("ready", encoding="ascii")
time.sleep(60)
"""
    process = start_worker(code, lock_path, ready_path)
    try:
        wait_for_path(ready_path, process)
        with pytest.raises(InstanceLockError, match="이미 실행 중"):
            DataDirectoryLock(lock_path).acquire()

        kill_process(process)
        recovered = DataDirectoryLock(lock_path)
        recovered.acquire()
        recovered.release()
    finally:
        kill_process(process)


def test_active_tcp_session_shutdown_releases_port_and_measurement_gate(tmp_path):
    port = available_port()
    log_path = tmp_path / "data" / "network_probe_log.csv"
    results_root = tmp_path / "data" / "network_probe_results"
    config = ProbeConfig(
        enabled=True,
        host="127.0.0.1",
        port=port,
        log_path=log_path,
        results_root=results_root,
        long_poll_seconds=0.05,
    )
    gate = NetworkMeasurementGate()
    service = ProbeService(
        config=config,
        measurement_gate=gate,
        normalize_ip=lambda value: value or "",
    )
    assert service.start() is True
    registration = service.register_agent(
        {
            "agent_id": uuid.uuid4().hex,
            "hostname": "FAULT-CLIENT",
            "server_host": "127.0.0.1",
            "protocol_version": PROBE_PROTOCOL_VERSION,
            "client_version": APP_VERSION,
        },
        "127.0.0.1",
    )
    with service.condition:
        agent = service.agents[registration["agent_id"]]
        agent.connectivity_status = "ready"
        agent.connectivity_checked_at = service.clock()
    session = service.create_session(
        agent_id=registration["agent_id"],
        direction="full",
        duration_seconds=30,
        stream_count=4,
    )

    service.stop()

    assert gate.is_available() is True
    assert service.session_status(session["session_id"])["status"] == "cancelled"
    replacement = ProbeService(
        config=config,
        measurement_gate=NetworkMeasurementGate(),
        normalize_ip=lambda value: value or "",
    )
    try:
        assert replacement.start() is True
    finally:
        replacement.stop()
