import csv
import logging
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

import runtime_stability as stability_module
from runtime_stability import (
    CsvIntegrityError,
    DataDirectoryLock,
    InsufficientStorageError,
    InstanceLockError,
    TimedSnapshotCache,
    archive_csv_history,
    attach_diagnostic_handlers,
    check_storage_health,
    close_diagnostic_logger,
    configure_diagnostic_logger,
    detach_diagnostic_handlers,
    ensure_csv_integrity,
    ensure_storage_capacity,
    inspect_csv_integrity,
    is_process_running,
    is_storage_full_error,
    prune_recovery_backups,
)


FIELDS = ["id", "memo"]


def write_rows(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        writer.writerows(rows)


def test_csv_integrity_accepts_complete_rows_with_embedded_newlines(tmp_path):
    path = tmp_path / "log.csv"
    write_rows(path, [["one", "first line\nsecond line"]])

    result = inspect_csv_integrity(path, FIELDS)

    assert result.valid is True
    assert result.row_count == 1


def test_csv_integrity_backs_up_and_removes_only_incomplete_last_row(tmp_path):
    path = tmp_path / "log.csv"
    write_rows(path, [["one", "complete"]])
    valid_bytes = path.read_bytes()
    with path.open("ab") as handle:
        handle.write(b'two,"incomplete')
    damaged_bytes = path.read_bytes()

    result = ensure_csv_integrity(path, FIELDS)

    assert result.valid is True
    assert result.repaired is True
    assert result.row_count == 1
    assert result.backup_path is not None
    assert result.backup_path.read_bytes() == damaged_bytes
    assert path.read_bytes() == valid_bytes


def test_csv_integrity_repairs_last_row_with_wrong_column_count(tmp_path):
    path = tmp_path / "log.csv"
    write_rows(path, [["one", "complete"]])
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write("two\r\n")

    result = ensure_csv_integrity(path, FIELDS)

    assert result.repaired is True
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        assert list(csv.reader(handle)) == [FIELDS, ["one", "complete"]]


def test_csv_integrity_creates_durable_backup_before_replacing_source(tmp_path, monkeypatch):
    path = tmp_path / "log.csv"
    write_rows(path, [["one", "complete"]])
    with path.open("ab") as handle:
        handle.write(b"incomplete")
    original_replace = stability_module._replace_with_prefix
    observed_backups = []

    def replace_after_backup(target, byte_count):
        backups = list(tmp_path.glob("log.csv.recovery-*.bak"))
        assert len(backups) == 1
        observed_backups.extend(backups)
        return original_replace(target, byte_count)

    monkeypatch.setattr(stability_module, "_replace_with_prefix", replace_after_backup)

    ensure_csv_integrity(path, FIELDS)

    assert len(observed_backups) == 1


def test_csv_recovery_backups_keep_only_newest_bounded_set(tmp_path):
    path = tmp_path / "log.csv"
    write_rows(path, [["one", "complete"]])
    for index in range(7):
        backup = tmp_path / (
            f"log.csv.recovery-2026070{index + 1}-000000-0000000{index}.bak"
        )
        backup.write_bytes(str(index).encode("ascii"))
    unrelated = tmp_path / "other.csv.recovery-20260701-000000-00000000.bak"
    unrelated.write_bytes(b"keep")

    removed = prune_recovery_backups(path, keep_count=5)

    remaining = sorted(tmp_path.glob("log.csv.recovery-*.bak"))
    assert removed == 2
    assert len(remaining) == 5
    assert remaining[0].name.startswith("log.csv.recovery-20260703")
    assert unrelated.exists()


def test_csv_history_archives_old_rows_by_month_and_keeps_recent_rows(tmp_path):
    path = tmp_path / "network.csv"
    fieldnames = ["checked_at", "session_id", "status"]
    rows = [
        {
            "checked_at": f"2026-06-{index + 1:02d} 12:00:00 +0900",
            "session_id": f"old-{index}",
            "status": "success",
        }
        for index in range(3)
    ] + [
        {
            "checked_at": f"2026-07-{index + 1:02d} 12:00:00 +0900",
            "session_id": f"new-{index}",
            "status": "success",
        }
        for index in range(3)
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    archived = archive_csv_history(
        path,
        fieldnames,
        min_size_bytes=0,
        max_active_rows=4,
        keep_rows=2,
    )

    assert archived == 4
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        assert [row["session_id"] for row in csv.DictReader(handle)] == ["new-1", "new-2"]
    june_archive = tmp_path / "archives" / "network" / "2026-06.csv"
    july_archive = tmp_path / "archives" / "network" / "2026-07.csv"
    with june_archive.open("r", encoding="utf-8-sig", newline="") as handle:
        assert [row["session_id"] for row in csv.DictReader(handle)] == [
            "old-0",
            "old-1",
            "old-2",
        ]
    with july_archive.open("r", encoding="utf-8-sig", newline="") as handle:
        assert [row["session_id"] for row in csv.DictReader(handle)] == ["new-0"]


def test_csv_history_retry_deduplicates_rows_after_interrupted_compaction(tmp_path):
    path = tmp_path / "network.csv"
    fieldnames = ["checked_at", "session_id"]
    rows = [
        {"checked_at": "2026-07-01 12:00:00 +0900", "session_id": f"session-{index}"}
        for index in range(5)
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    with (archive_root / "2026-07.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows[:3])

    archive_csv_history(
        path,
        fieldnames,
        min_size_bytes=0,
        max_active_rows=4,
        keep_rows=2,
        archive_root=archive_root,
    )

    with (archive_root / "2026-07.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        archived_rows = list(csv.DictReader(handle))
    assert [row["session_id"] for row in archived_rows] == [
        "session-0",
        "session-1",
        "session-2",
    ]


def test_csv_history_retry_preserves_legitimate_identical_row_count(tmp_path):
    path = tmp_path / "network.csv"
    fieldnames = ["checked_at", "status"]
    duplicate = {"checked_at": "2026-07-01 12:00:00 +0900", "status": "success"}
    rows = [duplicate, duplicate, duplicate, duplicate]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    with (archive_root / "2026-07.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(duplicate)

    archive_csv_history(
        path,
        fieldnames,
        min_size_bytes=0,
        max_active_rows=3,
        keep_rows=1,
        archive_root=archive_root,
    )

    with (archive_root / "2026-07.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 3


def test_csv_integrity_does_not_repair_invalid_header(tmp_path):
    path = tmp_path / "log.csv"
    path.write_text("wrong,header\n", encoding="utf-8-sig")
    original = path.read_bytes()

    with pytest.raises(CsvIntegrityError, match="invalid_header"):
        ensure_csv_integrity(path, FIELDS)

    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.bak")) == []


def test_csv_integrity_does_not_repair_corruption_before_last_row(tmp_path):
    path = tmp_path / "log.csv"
    path.write_text(
        "id,memo\nbroken\ntwo,complete\n",
        encoding="utf-8-sig",
    )
    original = path.read_bytes()

    with pytest.raises(CsvIntegrityError, match="wrong_column_count"):
        ensure_csv_integrity(path, FIELDS)

    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.bak")) == []


def test_csv_integrity_reports_unreadable_file_without_raising(tmp_path, monkeypatch):
    path = tmp_path / "log.csv"
    write_rows(path, [])
    original_open = Path.open

    def fail_target_open(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("denied")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_target_open)

    result = inspect_csv_integrity(path, FIELDS)

    assert result.valid is False
    assert result.issue == "unreadable"


def test_data_directory_lock_rejects_second_process_owner_until_release(tmp_path):
    lock_path = tmp_path / "data" / ".internal-upload.instance.lock"
    first = DataDirectoryLock(lock_path)
    second = DataDirectoryLock(lock_path)

    first.acquire()
    try:
        with pytest.raises(InstanceLockError, match="이미 실행 중"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
    assert b'"pid"' in lock_path.read_bytes()


def test_data_directory_lock_reports_unwritable_lock_location(tmp_path):
    blocked_parent = tmp_path / "data"
    blocked_parent.write_text("not a directory", encoding="utf-8")

    with pytest.raises(InstanceLockError, match="준비할 수 없습니다"):
        DataDirectoryLock(blocked_parent / ".internal-upload.instance.lock").acquire()


def test_windows_data_directory_lock_uses_nonblocking_byte_range(tmp_path, monkeypatch):
    path = tmp_path / "lock"
    path.write_bytes(b"0")
    calls = []
    fake_msvcrt = SimpleNamespace(
        LK_NBLCK=1,
        LK_UNLCK=2,
        locking=lambda descriptor, mode, count: calls.append((descriptor, mode, count)),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(stability_module.os, "name", "nt")

    with path.open("r+b") as handle:
        stability_module._lock_file(handle)
        stability_module._unlock_file(handle)

    assert [call[1:] for call in calls] == [(fake_msvcrt.LK_NBLCK, 1), (fake_msvcrt.LK_UNLCK, 1)]


def test_diagnostic_logger_rotates_bounded_files(tmp_path):
    logger = configure_diagnostic_logger(tmp_path, max_bytes=200, backup_count=2)

    for index in range(30):
        logger.info("diagnostic-entry index=%s payload=%s", index, "x" * 40)
    for handler in logger.handlers:
        handler.flush()

    log_root = tmp_path / "diagnostics"
    assert (log_root / "internal-upload.log").exists()
    assert (log_root / "internal-upload.log.1").exists()
    assert len(list(log_root.glob("internal-upload.log*"))) <= 3


def test_diagnostic_handler_attachment_replaces_stale_app_handler(tmp_path):
    target = logging.getLogger(f"test-target-{tmp_path.name}")
    first = configure_diagnostic_logger(tmp_path / "first")
    second = configure_diagnostic_logger(tmp_path / "second")
    try:
        attach_diagnostic_handlers(target, first)
        attach_diagnostic_handlers(target, second)

        managed = [
            handler
            for handler in target.handlers
            if getattr(handler, "_internal_upload_log_path", "")
        ]
        assert len(managed) == 1
        assert "second" in managed[0]._internal_upload_log_path

        detach_diagnostic_handlers(target, second)
        assert managed[0] not in target.handlers
    finally:
        close_diagnostic_logger(first)
        close_diagnostic_logger(second)


def test_storage_health_reports_writable_space_and_cleans_probe(tmp_path):
    result = check_storage_health(tmp_path)

    assert result["writable"] is True
    assert result["free_bytes"] > 0
    assert list(tmp_path.glob(".internal-upload-health-*.tmp")) == []


def test_storage_health_reports_low_space_and_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        stability_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=100),
    )
    monkeypatch.setattr(
        stability_module.os,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )

    result = check_storage_health(tmp_path, low_space_warning_bytes=200)

    assert result == {
        "writable": False,
        "free_bytes": 100,
        "low_space": True,
        "issue": "not_writable",
    }


def test_storage_capacity_reserves_space_for_operating_system(tmp_path, monkeypatch):
    monkeypatch.setattr(
        stability_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=1_500),
    )

    assert ensure_storage_capacity(tmp_path, required_bytes=400, reserve_bytes=1_000) == 1_500
    with pytest.raises(InsufficientStorageError, match="저장 공간이 부족"):
        ensure_storage_capacity(tmp_path, required_bytes=501, reserve_bytes=1_000)


def test_storage_capacity_fails_closed_when_usage_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        stability_module.shutil,
        "disk_usage",
        lambda path: (_ for _ in ()).throw(OSError("unavailable")),
    )

    with pytest.raises(InsufficientStorageError, match="확인할 수 없습니다"):
        ensure_storage_capacity(tmp_path, reserve_bytes=0)


def test_storage_full_error_recognizes_enospc_only():
    assert is_storage_full_error(OSError(stability_module.errno.ENOSPC, "full")) is True
    assert is_storage_full_error(PermissionError("denied")) is False


def test_process_running_detects_current_and_missing_process(monkeypatch):
    assert is_process_running(stability_module.os.getpid()) is True
    monkeypatch.setattr(
        stability_module.os,
        "kill",
        lambda _pid, _signal: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert is_process_running(999_999) is False


def test_timed_snapshot_cache_reuses_value_until_ttl_expires():
    now = [10.0]
    cache = TimedSnapshotCache(ttl_seconds=5.0, clock=lambda: now[0])
    calls = []

    def build_value():
        calls.append(now[0])
        return {"sequence": len(calls)}

    assert cache.get(build_value) == {"sequence": 1}
    now[0] = 14.9
    assert cache.get(build_value) == {"sequence": 1}
    now[0] = 15.0
    assert cache.get(build_value) == {"sequence": 2}
    cache.invalidate()
    assert cache.get(build_value) == {"sequence": 3}
