from __future__ import annotations

import csv
import argparse
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time
import uuid
from configparser import ConfigParser
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote, urlparse

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, send_file, stream_with_context, url_for

from app_version import APP_VERSION
from bounded_server import WEB_SHUTDOWN_DRAIN_SECONDS, make_bounded_server as make_server
from network_sustained import SUSTAINED_LOG_FIELDS, create_sustained_blueprint, ensure_sustained_storage
from network_measurement import NetworkMeasurementGate
from network_probe.models import PROBE_PROTOCOL_VERSION, ProbeConfig
from network_probe.routes import create_probe_blueprint
from network_probe.service import PROBE_LOG_FIELDS, ProbeService, ensure_probe_storage
from result_storage import prune_old_json_results
from runtime_stability import (
    LOW_FREE_SPACE_WARNING_BYTES,
    MEASUREMENT_CSV_MAX_ACTIVE_ROWS,
    CsvIntegrityError,
    CsvIntegrityResult,
    DataDirectoryLock,
    InsufficientStorageError,
    InstanceLockError,
    TimedSnapshotCache,
    UploadAdmissionController,
    UploadConcurrencyError,
    archive_csv_history,
    attach_diagnostic_handlers,
    check_storage_health,
    close_diagnostic_logger,
    configure_diagnostic_logger,
    detach_diagnostic_handlers,
    durable_replace,
    ensure_csv_integrity,
    ensure_storage_capacity,
    inspect_csv_integrity,
    is_process_running,
    is_storage_full_error,
    prune_recovery_backups,
)
from startup_ports import (
    APP_ID,
    FIREWALL_ALLOWED,
    FIREWALL_NOT_APPLICABLE,
    PortChangeDeclined,
    StartupPortError,
    check_windows_firewall_port,
    config_requires_probe_enable_migration,
    firewall_add_command,
    is_existing_instance,
    migrate_config,
    persist_port_change,
    persist_probe_port_change,
    resolve_probe_port,
    resolve_startup_port,
    rewrite_base_url_port,
)


def get_runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parent


APP_ROOT = get_runtime_root()
RESOURCE_ROOT = get_resource_root()
CONFIG_SECTION = "app"
NETWORK_PROBE_SECTION = "network_probe"
CSV_FIELDS = [
    "upload_id",
    "uploaded_at",
    "original_filename",
    "stored_filename",
    "storage_subdir",
    "storage_path",
    "memo",
    "download_url",
]
NETWORK_CHECK_FIELDS = [
    "checked_at",
    "client_ip",
    "direction",
    "size_mb",
    "bytes_transferred",
    "duration_seconds",
    "mbps",
    "status",
]
NETWORK_CHECK_SIZE_OPTIONS_MB = (10, 50, 100, 500, 1024)
MEGABYTE = 1024 * 1024
NETWORK_CHECK_CHUNK_SIZE = MEGABYTE
NETWORK_CHECK_CHUNK = bytes(index % 251 for index in range(NETWORK_CHECK_CHUNK_SIZE))
NETWORK_CHECK_UPLOAD_SESSION_TTL_SECONDS = 15 * 60
UPLOAD_ARTIFACT_PREFIX = ".internal-upload-"
UPLOAD_ARTIFACT_STALE_SECONDS = 24 * 60 * 60
UPLOAD_COPY_CHUNK_BYTES = 1024 * 1024
UPLOAD_SPACE_RECHECK_BYTES = 8 * 1024 * 1024
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
BLOCKED_UPLOAD_SUFFIXES = frozenset(
    {
        ".appinstaller",
        ".appref-ms",
        ".appx",
        ".appxbundle",
        ".application",
        ".apk",
        ".bash",
        ".bat",
        ".chm",
        ".cmd",
        ".com",
        ".cpl",
        ".csh",
        ".dll",
        ".doc",
        ".docb",
        ".docm",
        ".dot",
        ".dotm",
        ".dmg",
        ".drv",
        ".exe",
        ".fish",
        ".gadget",
        ".hta",
        ".img",
        ".inf",
        ".iso",
        ".jar",
        ".js",
        ".jse",
        ".ksh",
        ".lnk",
        ".lua",
        ".mdb",
        ".mde",
        ".msc",
        ".msi",
        ".msix",
        ".msixbundle",
        ".msp",
        ".msu",
        ".ocx",
        ".ova",
        ".ovf",
        ".php",
        ".pif",
        ".pl",
        ".pot",
        ".potm",
        ".ppam",
        ".pps",
        ".ppsm",
        ".ppt",
        ".pptm",
        ".ps1",
        ".psd1",
        ".psm1",
        ".py",
        ".pyw",
        ".qcow",
        ".qcow2",
        ".rb",
        ".reg",
        ".scf",
        ".scr",
        ".sct",
        ".sh",
        ".sldm",
        ".sys",
        ".tcl",
        ".url",
        ".vb",
        ".vbe",
        ".vbs",
        ".vhd",
        ".vhdx",
        ".vdi",
        ".vmdk",
        ".vsdm",
        ".vssm",
        ".vstm",
        ".ws",
        ".wsc",
        ".wsf",
        ".wsh",
        ".xla",
        ".xlam",
        ".xlm",
        ".xls",
        ".xlsb",
        ".xlsm",
        ".xlt",
        ".xltm",
        ".xbap",
        ".zsh",
        ".accdb",
        ".accde",
        ".accdr",
    }
)
_csv_lock = threading.Lock()
_network_check_csv_lock = threading.Lock()
_upload_log_cache: dict[Path, "UploadLogSnapshot"] = {}


@dataclass(frozen=True)
class AppConfig:
    app_root: Path
    host: str
    port: int
    base_url: str
    storage_root: Path
    delete_allowed_ips: tuple[str, ...]
    recent_limit: int
    log_path: Path
    network_check_log_path: Path
    network_check_session_log_path: Path
    network_check_results_root: Path
    network_probe_enabled: bool
    network_probe_port: int
    network_probe_log_path: Path
    network_probe_results_root: Path


@dataclass
class NetworkCheckUploadSession:
    session_id: str
    client_ip: str
    size_mb: int
    expected_bytes: int
    started_at: float
    bytes_received: int = 0
    expiry_timer: threading.Timer | None = None


@dataclass(frozen=True)
class UploadReservation:
    upload_id: str
    stored_filename: str
    target_path: Path
    temporary_path: Path
    lock_path: Path


@dataclass(frozen=True)
class UploadLogSnapshot:
    signature: tuple[int, int, int, int, int]
    rows: tuple[dict[str, str], ...]
    by_id: dict[str, dict[str, str]]


class UploadConflictError(RuntimeError):
    pass


def load_config(config_path: str | os.PathLike[str] | None = None) -> AppConfig:
    path = Path(config_path).resolve() if config_path else APP_ROOT / "config.ini"
    app_root = path.parent

    parser = ConfigParser()
    parser[CONFIG_SECTION] = {
        "CONFIG_VERSION": "2",
        "HOST": "0.0.0.0",
        "PORT": "8000",
        "BASE_URL": "",
        "STORAGE_ROOT": "uploads",
        "DELETE_ALLOWED_IPS": "127.0.0.1,::1",
        "RECENT_LIMIT": "50",
    }
    parser[NETWORK_PROBE_SECTION] = {
        "ENABLED": "true",
        "PORT": "5201",
    }
    if path.exists():
        parser.read(path, encoding="utf-8")

    section = parser[CONFIG_SECTION]
    probe_section = parser[NETWORK_PROBE_SECTION]
    storage_root = Path(section.get("STORAGE_ROOT", "uploads")).expanduser()
    if not storage_root.is_absolute():
        storage_root = app_root / storage_root

    return AppConfig(
        app_root=app_root,
        host=section.get("HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=max(1, min(65535, section.getint("PORT", fallback=8000))),
        base_url=section.get("BASE_URL", "").strip().rstrip("/"),
        storage_root=storage_root.resolve(),
        delete_allowed_ips=parse_csv_list(section.get("DELETE_ALLOWED_IPS", "")),
        recent_limit=max(1, section.getint("RECENT_LIMIT", fallback=50)),
        log_path=app_root / "data" / "upload_log.csv",
        network_check_log_path=app_root / "data" / "network_check_log.csv",
        network_check_session_log_path=app_root / "data" / "network_check_session_log.csv",
        network_check_results_root=app_root / "data" / "network_check_results",
        network_probe_enabled=probe_section.getboolean("ENABLED", fallback=True),
        network_probe_port=max(1, min(65535, probe_section.getint("PORT", fallback=5201))),
        network_probe_log_path=app_root / "data" / "network_probe_log.csv",
        network_probe_results_root=app_root / "data" / "network_probe_results",
    )


def build_probe_config(config: AppConfig) -> ProbeConfig:
    return ProbeConfig(
        enabled=config.network_probe_enabled,
        host=config.host,
        port=config.network_probe_port,
        log_path=config.network_probe_log_path,
        results_root=config.network_probe_results_root,
    )


def parse_csv_list(value: str) -> tuple[str, ...]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(items or ["127.0.0.1", "::1"])


def ensure_directories(config: AppConfig) -> list[CsvIntegrityResult]:
    config.storage_root.mkdir(parents=True, exist_ok=True)
    cleanup_stale_upload_artifacts(config.storage_root)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_log_file(config.log_path)
    ensure_network_check_log_file(config.network_check_log_path)
    ensure_sustained_storage(config.network_check_session_log_path, config.network_check_results_root)
    ensure_probe_storage(config.network_probe_log_path, config.network_probe_results_root)
    integrity_results = [
        ensure_csv_integrity(config.log_path, CSV_FIELDS),
        ensure_csv_integrity(config.network_check_log_path, NETWORK_CHECK_FIELDS),
        ensure_csv_integrity(config.network_check_session_log_path, SUSTAINED_LOG_FIELDS),
        ensure_csv_integrity(config.network_probe_log_path, PROBE_LOG_FIELDS),
    ]
    for log_path in (
        config.log_path,
        config.network_check_log_path,
        config.network_check_session_log_path,
        config.network_probe_log_path,
    ):
        prune_recovery_backups(log_path)
    prune_old_json_results(config.network_check_results_root)
    prune_old_json_results(config.network_probe_results_root)
    with _csv_lock:
        _upload_log_cache.pop(config.log_path.resolve(), None)
    measurement_logs = (
        (integrity_results[1], config.network_check_log_path, NETWORK_CHECK_FIELDS),
        (
            integrity_results[2],
            config.network_check_session_log_path,
            SUSTAINED_LOG_FIELDS,
        ),
        (integrity_results[3], config.network_probe_log_path, PROBE_LOG_FIELDS),
    )
    for result, path, fieldnames in measurement_logs:
        if result.row_count > MEASUREMENT_CSV_MAX_ACTIVE_ROWS:
            archive_csv_history(path, fieldnames, min_size_bytes=0)
    return integrity_results


def ensure_log_file(log_path: Path) -> None:
    ensure_csv_file(log_path, CSV_FIELDS)


def ensure_network_check_log_file(log_path: Path) -> None:
    ensure_csv_file(log_path, NETWORK_CHECK_FIELDS)


def ensure_csv_file(log_path: Path, fieldnames: list[str]) -> None:
    if log_path.exists() and log_path.stat().st_size > 0:
        return
    with log_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()


def normalize_storage_subdir(storage_subdir: str | None) -> str:
    raw = (storage_subdir or "").strip().replace("\\", "/")
    if not raw:
        return ""

    windows_path = PureWindowsPath(raw)
    if windows_path.drive or windows_path.is_absolute() or raw.startswith("/"):
        raise ValueError("저장 위치는 기준 폴더 아래의 상대 경로만 입력할 수 있습니다.")

    parts = []
    for part in PurePosixPath(raw).parts:
        cleaned = part.strip()
        if cleaned in {"", ".", ".."}:
            raise ValueError("저장 위치에 '.', '..' 경로는 사용할 수 없습니다.")
        if INVALID_FILENAME_CHARS.search(cleaned):
            raise ValueError("저장 위치에 Windows에서 사용할 수 없는 문자가 있습니다.")
        parts.append(cleaned)
    return "/".join(parts)


def resolve_storage_path(storage_subdir: str | None, config: AppConfig) -> Path:
    normalized = normalize_storage_subdir(storage_subdir)
    target = (config.storage_root / normalized).resolve()
    if target != config.storage_root and not target.is_relative_to(config.storage_root):
        raise ValueError("저장 위치는 기준 폴더 밖으로 나갈 수 없습니다.")
    return target


def safe_filename(filename: str | None) -> str:
    basename = (filename or "").replace("\\", "/").split("/")[-1].strip()
    safe = INVALID_FILENAME_CHARS.sub("_", basename).strip(" .")
    if not safe:
        safe = "uploaded_file"

    stem = Path(safe).stem.upper()
    if stem in WINDOWS_RESERVED_NAMES:
        safe = f"{safe}_"

    if len(safe) > 180:
        suffix = Path(safe).suffix
        stem = Path(safe).stem
        max_stem_len = max(1, 180 - len(suffix))
        safe = f"{stem[:max_stem_len]}{suffix}"
    return safe


def blocked_upload_reason(filename: str, uploaded_file) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix in BLOCKED_UPLOAD_SUFFIXES:
        return "보안상 실행파일, 스크립트, 매크로 문서와 디스크 이미지는 업로드할 수 없습니다."

    stream = getattr(uploaded_file, "stream", uploaded_file)
    try:
        position = stream.tell()
        header = stream.read(2)
        stream.seek(position)
    except (AttributeError, OSError):
        header = b""
    if header == b"MZ":
        return "파일 이름과 관계없이 Windows 실행 파일 내용은 업로드할 수 없습니다."
    return ""


def generate_upload_id(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    return f"{current.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def cleanup_stale_upload_artifacts(
    storage_root: Path,
    *,
    older_than_seconds: float = UPLOAD_ARTIFACT_STALE_SECONDS,
    now: float | None = None,
) -> int:
    current_time = time.time() if now is None else now
    removed = 0
    protected_paths: set[Path] = set()
    for lock_path in storage_root.rglob(f"{UPLOAD_ARTIFACT_PREFIX}*.lock"):
        part_path = None
        owner_running = None
        try:
            metadata = json.loads(lock_path.read_text(encoding="ascii"))
            part_name = str(metadata.get("part", ""))
            if (
                part_name == Path(part_name).name
                and part_name.startswith(UPLOAD_ARTIFACT_PREFIX)
                and part_name.endswith(".part")
            ):
                part_path = lock_path.parent / part_name
                owner_running = is_process_running(int(metadata.get("pid", 0)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

        if owner_running is True:
            protected_paths.add(lock_path)
            if part_path is not None:
                protected_paths.add(part_path)
            continue
        try:
            expired = current_time - lock_path.stat().st_mtime >= older_than_seconds
        except (FileNotFoundError, OSError):
            continue
        if owner_running is not False and not expired:
            continue
        for path in (part_path, lock_path):
            if path is None:
                continue
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except (FileNotFoundError, OSError):
                continue

    for path in storage_root.rglob(f"{UPLOAD_ARTIFACT_PREFIX}*"):
        if path in protected_paths:
            continue
        if path.suffix not in {".part", ".lock"}:
            continue
        try:
            if not path.is_file() or current_time - path.stat().st_mtime < older_than_seconds:
                continue
            path.unlink()
            removed += 1
        except (FileNotFoundError, OSError):
            continue
    return removed


def _upload_lock_path(storage_dir: Path, stored_filename: str) -> Path:
    digest = hashlib.sha256(stored_filename.casefold().encode("utf-8")).hexdigest()
    return storage_dir / f"{UPLOAD_ARTIFACT_PREFIX}{digest}.lock"


def _try_reserve_upload_target(
    storage_dir: Path,
    upload_id: str,
    stored_filename: str,
) -> UploadReservation | None:
    target_path = storage_dir / stored_filename
    lock_path = _upload_lock_path(storage_dir, stored_filename)
    temporary_path = storage_dir / f"{UPLOAD_ARTIFACT_PREFIX}{upload_id}.part"
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    try:
        metadata = json.dumps(
            {
                "pid": os.getpid(),
                "part": temporary_path.name,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("ascii")
        os.write(lock_descriptor, metadata)
        os.fsync(lock_descriptor)
        os.close(lock_descriptor)
    except Exception:
        try:
            os.close(lock_descriptor)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)
        raise

    temporary_created = False
    keep_reservation = False
    try:
        if target_path.exists():
            return None
        temporary_descriptor = os.open(
            temporary_path,
            os.O_CREAT | os.O_EXCL | os.O_RDWR,
            0o600,
        )
        temporary_created = True
        os.close(temporary_descriptor)
        keep_reservation = True
        return UploadReservation(
            upload_id=upload_id,
            stored_filename=stored_filename,
            target_path=target_path,
            temporary_path=temporary_path,
            lock_path=lock_path,
        )
    finally:
        if not keep_reservation:
            if temporary_created:
                temporary_path.unlink(missing_ok=True)
            lock_path.unlink(missing_ok=True)


def reserve_upload_target(
    storage_dir: Path,
    original_filename: str,
    *,
    confirm_duplicate: bool,
) -> UploadReservation:
    storage_dir.mkdir(parents=True, exist_ok=True)
    original_target = storage_dir / original_filename
    original_exists = original_target.exists()
    if original_exists and not confirm_duplicate:
        raise UploadConflictError

    upload_id = generate_upload_id()
    if not original_exists:
        reservation = _try_reserve_upload_target(storage_dir, upload_id, original_filename)
        if reservation is not None:
            return reservation
        if not confirm_duplicate:
            raise UploadConflictError

    for _ in range(10):
        stored_filename = f"{upload_id}_{original_filename}"
        reservation = _try_reserve_upload_target(storage_dir, upload_id, stored_filename)
        if reservation is not None:
            return reservation
        upload_id = generate_upload_id()
    raise OSError("고유한 업로드 저장 경로를 예약할 수 없습니다.")


def commit_uploaded_file(
    uploaded_file,
    reservation: UploadReservation,
    *,
    storage_root: Path | None = None,
    progress_callback=None,
) -> None:
    capacity_path = storage_root or reservation.temporary_path.parent
    source = getattr(uploaded_file, "stream", uploaded_file)
    bytes_since_space_check = UPLOAD_SPACE_RECHECK_BYTES
    try:
        with reservation.temporary_path.open("r+b") as handle:
            handle.seek(0)
            handle.truncate(0)
            while True:
                if bytes_since_space_check >= UPLOAD_SPACE_RECHECK_BYTES:
                    ensure_storage_capacity(
                        capacity_path,
                        required_bytes=UPLOAD_SPACE_RECHECK_BYTES,
                    )
                    bytes_since_space_check = 0
                chunk = source.read(UPLOAD_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                handle.write(chunk)
                if progress_callback is not None:
                    progress_callback(len(chunk))
                bytes_since_space_check += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        durable_replace(reservation.temporary_path, reservation.target_path)
    except OSError as exc:
        if is_storage_full_error(exc):
            raise InsufficientStorageError(
                "서버 저장 공간이 부족하여 업로드를 완료하지 못했습니다."
            ) from exc
        raise


def release_upload_reservation(reservation: UploadReservation) -> None:
    for path in (reservation.temporary_path, reservation.lock_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def detect_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def build_download_url(
    upload_id: str,
    config: AppConfig,
    ip_address: str | None = None,
) -> str:
    if config.base_url:
        base_url = config.base_url.rstrip("/")
    else:
        host = ip_address or detect_lan_ip()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = "" if config.port in {80, 443} else f":{config.port}"
        base_url = f"http://{host}{port}"
    return f"{base_url}/download/{quote(upload_id)}"


def is_loopback_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _append_csv_row_with_rollback(
    log_path: Path,
    fieldnames: list[str],
    row: dict[str, str],
) -> None:
    original_size = log_path.stat().st_size
    try:
        with log_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writerow({field: row.get(field, "") for field in fieldnames})
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with log_path.open("r+b") as handle:
            handle.truncate(original_size)
            handle.flush()
            os.fsync(handle.fileno())
        raise


def append_upload_log(row: dict[str, str], config: AppConfig) -> None:
    with _csv_lock:
        snapshot = _read_upload_log_snapshot_locked(config)
        normalized_row = {field: row.get(field, "") for field in CSV_FIELDS}
        _append_csv_row_with_rollback(config.log_path, CSV_FIELDS, normalized_row)
        _store_upload_log_snapshot_locked(
            config.log_path,
            [*snapshot.rows, normalized_row],
        )


def read_upload_log(config: AppConfig, limit: int | None = None) -> list[dict[str, str]]:
    with _csv_lock:
        snapshot = _read_upload_log_snapshot_locked(config)
        rows = [row for row in snapshot.rows if row.get("upload_id")]
        selected = rows[-limit:] if limit else rows
        return [dict(row) for row in reversed(selected)]


def find_upload(upload_id: str, config: AppConfig) -> dict[str, str] | None:
    with _csv_lock:
        row = _read_upload_log_snapshot_locked(config).by_id.get(upload_id)
        return dict(row) if row is not None else None


def _upload_log_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
    )


def _read_upload_log_snapshot_locked(config: AppConfig) -> UploadLogSnapshot:
    ensure_log_file(config.log_path)
    cache_key = config.log_path.resolve()
    signature = _upload_log_signature(config.log_path)
    cached = _upload_log_cache.get(cache_key)
    if cached is not None and cached.signature == signature:
        return cached
    with config.log_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return _store_upload_log_snapshot_locked(config.log_path, rows)


def _store_upload_log_snapshot_locked(
    path: Path,
    rows: list[dict[str, str]],
) -> UploadLogSnapshot:
    normalized_rows = tuple(
        {field: row.get(field, "") for field in CSV_FIELDS} for row in rows
    )
    snapshot = UploadLogSnapshot(
        signature=_upload_log_signature(path),
        rows=normalized_rows,
        by_id={
            row["upload_id"]: row for row in normalized_rows if row.get("upload_id")
        },
    )
    _upload_log_cache[path.resolve()] = snapshot
    return snapshot


def _write_upload_log_rows(log_path: Path, rows: list[dict[str, str]]) -> None:
    temporary_path = log_path.with_name(f".{log_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        durable_replace(temporary_path, log_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def delete_upload_log(upload_id: str, config: AppConfig) -> bool:
    with _csv_lock:
        snapshot = _read_upload_log_snapshot_locked(config)
        rows = [dict(row) for row in snapshot.rows]
        kept_rows = [row for row in rows if row.get("upload_id") != upload_id]
        deleted = len(kept_rows) != len(rows)
        if not deleted:
            return False

        deleted_row = next(row for row in rows if row.get("upload_id") == upload_id)
        file_path = record_file_path(deleted_row, config)
        should_delete_file = file_path.exists() and file_path.is_file()
        _write_upload_log_rows(config.log_path, kept_rows)
        _store_upload_log_snapshot_locked(config.log_path, kept_rows)
        if should_delete_file:
            try:
                file_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                _write_upload_log_rows(config.log_path, rows)
                _store_upload_log_snapshot_locked(config.log_path, rows)
                raise
        return True


def normalize_ip(value: str | None) -> str:
    raw = (value or "").split(",", 1)[0].strip()
    try:
        parsed = ipaddress.ip_address(raw)
        if getattr(parsed, "ipv4_mapped", None):
            parsed = parsed.ipv4_mapped
        return str(parsed)
    except ValueError:
        return raw


def is_delete_allowed(request_ip: str | None, config: AppConfig) -> bool:
    normalized_request_ip = normalize_ip(request_ip)
    allowed = {normalize_ip(item) for item in config.delete_allowed_ips}
    return normalized_request_ip in allowed


def record_file_path(row: dict[str, str], config: AppConfig) -> Path:
    raw_path = row.get("storage_path", "").strip()
    if not raw_path:
        raise ValueError("업로드 이력의 저장 경로가 비어 있습니다.")
    try:
        file_path = Path(raw_path).expanduser().resolve()
        storage_root = config.storage_root.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("업로드 이력의 저장 경로를 확인할 수 없습니다.") from exc
    if file_path == storage_root or not file_path.is_relative_to(storage_root):
        raise ValueError("업로드 이력의 저장 경로가 기준 폴더 밖을 가리킵니다.")
    return file_path


def cleanup_created_file(file_path: Path, existed_before: bool) -> None:
    if existed_before:
        return
    try:
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
    except OSError:
        return


def parse_network_check_size(size_value: str | None) -> int:
    try:
        size_mb = int(size_value or "")
    except ValueError as exc:
        raise ValueError("허용되지 않는 네트워크 체크 크기입니다.") from exc
    if size_mb not in NETWORK_CHECK_SIZE_OPTIONS_MB:
        raise ValueError("허용되지 않는 네트워크 체크 크기입니다.")
    return size_mb


def network_check_total_bytes(size_mb: int) -> int:
    return size_mb * MEGABYTE


def calculate_mbps(byte_count: int, duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    return (byte_count * 8) / duration_seconds / 1_000_000


def build_network_check_log_row(
    *,
    client_ip: str,
    direction: str,
    size_mb: int,
    bytes_transferred: int,
    duration_seconds: float,
    status: str,
) -> dict[str, str]:
    return {
        "checked_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "client_ip": client_ip,
        "direction": direction,
        "size_mb": str(size_mb),
        "bytes_transferred": str(bytes_transferred),
        "duration_seconds": f"{duration_seconds:.3f}",
        "mbps": f"{calculate_mbps(bytes_transferred, duration_seconds):.2f}",
        "status": status,
    }


def build_network_check_response_payload(
    *,
    direction: str,
    size_mb: int,
    bytes_transferred: int,
    duration_seconds: float,
    status: str,
    error: str = "",
) -> dict[str, str | int | float]:
    payload: dict[str, str | int | float] = {
        "direction": direction,
        "size_mb": size_mb,
        "bytes_transferred": bytes_transferred,
        "duration_seconds": float(f"{duration_seconds:.3f}"),
        "mbps": float(f"{calculate_mbps(bytes_transferred, duration_seconds):.2f}"),
        "status": status,
    }
    if error:
        payload["error"] = error
    return payload


def append_network_check_log(row: dict[str, str], config: AppConfig) -> None:
    with _network_check_csv_lock:
        ensure_network_check_log_file(config.network_check_log_path)
        _append_csv_row_with_rollback(config.network_check_log_path, NETWORK_CHECK_FIELDS, row)
        try:
            archive_csv_history(config.network_check_log_path, NETWORK_CHECK_FIELDS)
        except (OSError, CsvIntegrityError):
            logging.getLogger(__name__).warning(
                "measurement_csv_archive_failed",
                exc_info=True,
            )


def read_network_check_log(config: AppConfig, limit: int | None = None) -> list[dict[str, str]]:
    with _network_check_csv_lock:
        ensure_network_check_log_file(config.network_check_log_path)
        with config.network_check_log_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    rows = [row for row in rows if row.get("checked_at")]
    rows.reverse()
    return rows[:limit] if limit else rows


def create_app(
    config_path: str | os.PathLike[str] | None = None,
    *,
    app_config: AppConfig | None = None,
    probe_service: ProbeService | None = None,
    measurement_gate: NetworkMeasurementGate | None = None,
    probe_client_bundle_path: str | os.PathLike[str] | None = None,
    diagnostic_logger: logging.Logger | None = None,
    health_check_cache: TimedSnapshotCache | None = None,
    upload_admission_controller: UploadAdmissionController | None = None,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(RESOURCE_ROOT / "templates"),
        static_folder=str(RESOURCE_ROOT / "static"),
    )
    config = app_config or load_config(config_path)
    active_logger = diagnostic_logger or configure_diagnostic_logger(config.log_path.parent)
    attach_diagnostic_handlers(app.logger, active_logger)
    try:
        integrity_results = ensure_directories(config)
    except Exception:
        active_logger.exception("startup_storage_validation_failed")
        raise
    for result in integrity_results:
        if result.repaired:
            active_logger.warning(
                "csv_tail_recovered file=%s backup=%s",
                result.path.name,
                result.backup_path.name if result.backup_path else "",
            )
    active_gate = measurement_gate or NetworkMeasurementGate()
    active_probe_service = probe_service or ProbeService(
        config=build_probe_config(config),
        measurement_gate=active_gate,
        normalize_ip=normalize_ip,
    )
    upload_sessions: dict[str, NetworkCheckUploadSession] = {}
    upload_sessions_lock = threading.Lock()
    sustained_blueprint, sustained_manager = create_sustained_blueprint(
        log_path=config.network_check_session_log_path,
        results_root=config.network_check_results_root,
        normalize_ip=normalize_ip,
        measurement_gate=active_gate,
    )
    app.register_blueprint(sustained_blueprint)
    app.register_blueprint(
        create_probe_blueprint(
            active_probe_service,
            web_port=config.port,
            lan_ip_resolver=detect_lan_ip,
            client_bundle_path=probe_client_bundle_path,
        )
    )
    app.extensions["sustained_network_check"] = sustained_manager
    app.extensions["network_measurement_gate"] = active_gate
    app.extensions["network_probe"] = active_probe_service
    app.extensions["diagnostic_logger"] = active_logger
    active_upload_admission = upload_admission_controller or UploadAdmissionController(
        config.storage_root,
        capacity_check=lambda path, **kwargs: ensure_storage_capacity(path, **kwargs),
    )
    app.extensions["upload_admission"] = active_upload_admission
    active_health_cache = health_check_cache or TimedSnapshotCache()
    app.extensions["health_check_cache"] = active_health_cache
    active_logger.info(
        "application_initialized version=%s web_port=%s probe_enabled=%s probe_port=%s",
        APP_VERSION,
        config.port,
        config.network_probe_enabled,
        config.network_probe_port,
    )

    @app.get("/api/health")
    def health_check():
        def expensive_checks():
            upload_storage = check_storage_health(config.storage_root)
            metadata_storage = check_storage_health(config.log_path.parent)
            csv_specs = (
                ("upload_log", config.log_path, CSV_FIELDS, _csv_lock),
                (
                    "network_check_log",
                    config.network_check_log_path,
                    NETWORK_CHECK_FIELDS,
                    _network_check_csv_lock,
                ),
                (
                    "network_check_session_log",
                    config.network_check_session_log_path,
                    SUSTAINED_LOG_FIELDS,
                    sustained_manager.storage_lock,
                ),
                (
                    "network_probe_log",
                    config.network_probe_log_path,
                    PROBE_LOG_FIELDS,
                    active_probe_service.storage_lock,
                ),
            )
            csv_files = {}
            for name, path, fieldnames, lock in csv_specs:
                with lock:
                    result = inspect_csv_integrity(path, fieldnames)
                csv_files[name] = "ok" if result.valid else result.issue
            return {
                "upload_storage": upload_storage,
                "metadata_storage": metadata_storage,
                "csv_files": csv_files,
            }

        cached_checks = active_health_cache.get(expensive_checks)
        upload_storage = cached_checks["upload_storage"]
        metadata_storage = cached_checks["metadata_storage"]
        csv_files = cached_checks["csv_files"]
        csv_ok = all(value == "ok" for value in csv_files.values())

        try:
            probe = active_probe_service.status_payload()
        except Exception:
            active_logger.exception("health_probe_status_failed")
            probe = {
                "enabled": config.network_probe_enabled,
                "available": False,
                "port": config.network_probe_port,
                "error": "TCP 측정 서버 상태를 확인할 수 없습니다.",
            }
        probe_ok = not probe["enabled"] or probe["available"]
        measurement = active_gate.status()
        upload_admission = active_upload_admission.status()
        storage_ok = all(
            item["writable"] and item["free_bytes"] >= 0 and not item["low_space"]
            for item in (upload_storage, metadata_storage)
        )
        ready = storage_ok and csv_ok and probe_ok and not measurement["long_running"]
        response = jsonify(
            {
                "app": APP_ID,
                "status": "ok" if ready else "degraded",
                "port": config.port,
                "version": APP_VERSION,
                "probe_protocol_version": PROBE_PROTOCOL_VERSION,
                "checks": {
                    "storage": {
                        "status": "ok" if storage_ok else "degraded",
                        "upload_writable": upload_storage["writable"],
                        "metadata_writable": metadata_storage["writable"],
                        "upload_free_bytes": upload_storage["free_bytes"],
                        "metadata_free_bytes": metadata_storage["free_bytes"],
                        "warning_below_bytes": LOW_FREE_SPACE_WARNING_BYTES,
                        "low_space": bool(
                            upload_storage["low_space"] or metadata_storage["low_space"]
                        ),
                    },
                    "csv": {
                        "status": "ok" if csv_ok else "degraded",
                        "files": csv_files,
                    },
                    "tcp_probe": {
                        "status": "ok" if probe_ok else "degraded",
                        "enabled": probe["enabled"],
                        "available": probe["available"],
                        "port": probe["port"],
                        "error": probe["error"],
                    },
                    "measurement": {
                        "status": "warning" if measurement["long_running"] else "ok",
                        **measurement,
                    },
                    "file_uploads": {
                        "status": "busy" if upload_admission["at_capacity"] else "ok",
                        **upload_admission,
                    },
                },
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def finalize_upload_session(
        session: NetworkCheckUploadSession,
        *,
        status: str,
        error: str = "",
    ) -> dict[str, str | int | float]:
        if session.expiry_timer is not None:
            session.expiry_timer.cancel()
            session.expiry_timer = None
        duration = time.perf_counter() - session.started_at
        row = build_network_check_log_row(
            client_ip=session.client_ip,
            direction="upload",
            size_mb=session.size_mb,
            bytes_transferred=session.bytes_received,
            duration_seconds=duration,
            status=status,
        )
        try:
            append_network_check_log(row, config)
        finally:
            active_gate.release("http_quick", session.session_id)
        return build_network_check_response_payload(
            direction="upload",
            size_mb=session.size_mb,
            bytes_transferred=session.bytes_received,
            duration_seconds=duration,
            status=status,
            error=error,
        )

    def expire_upload_session(session_id: str) -> None:
        with upload_sessions_lock:
            session = upload_sessions.pop(session_id, None)
        if session is None:
            return
        try:
            finalize_upload_session(
                session,
                status="failure",
                error="네트워크 체크 업로드 세션이 만료되었습니다.",
            )
        except Exception:
            active_logger.exception("network_check_upload_expiry_record_failed")

    def cleanup_expired_upload_sessions() -> None:
        now = time.perf_counter()
        expired_sessions = []
        with upload_sessions_lock:
            for session_id, session in list(upload_sessions.items()):
                if now - session.started_at > NETWORK_CHECK_UPLOAD_SESSION_TTL_SECONDS:
                    expired_sessions.append(upload_sessions.pop(session_id))
        for session in expired_sessions:
            finalize_upload_session(
                session,
                status="failure",
                error="네트워크 체크 업로드 세션이 만료되었습니다.",
            )

    def render_index(
        *,
        status_code: int = 200,
        error: str | None = None,
        result: dict[str, str] | None = None,
        conflict: dict[str, str] | None = None,
        storage_subdir: str = "",
        memo: str = "",
    ):
        client_ip = normalize_ip(request.remote_addr)
        return (
            render_template(
                "index.html",
                config=config,
                network_check_size_options=NETWORK_CHECK_SIZE_OPTIONS_MB,
                records=read_upload_log(config, config.recent_limit),
                can_delete=is_delete_allowed(client_ip, config),
                client_ip=client_ip,
                error=error,
                result=result,
                conflict=conflict,
                storage_subdir=storage_subdir,
                memo=memo,
                deleted=request.args.get("deleted") == "1",
            ),
            status_code,
        )

    @app.get("/")
    def index():
        return render_index()

    @app.before_request
    def acquire_file_upload_capacity():
        if request.endpoint != "upload":
            return None
        try:
            g.file_upload_capacity = active_upload_admission.acquire(
                request.content_length or 0
            )
        except UploadConcurrencyError:
            active_logger.warning("upload_rejected_concurrency_limit")
            return render_index(
                status_code=503,
                error=(
                    "동시에 처리할 수 있는 파일 업로드 수를 초과했습니다. "
                    "잠시 후 다시 시도하세요."
                ),
            )
        except InsufficientStorageError:
            active_logger.warning("upload_rejected_aggregate_storage_reservation")
            return render_index(
                status_code=507,
                error=(
                    "진행 중인 업로드를 포함하면 서버 저장 공간이 부족합니다. "
                    "다른 업로드가 끝나거나 디스크 공간을 확보한 뒤 다시 시도하세요."
                ),
            )
        return None

    @app.teardown_request
    def release_file_upload_capacity(_error=None):
        reservation = getattr(g, "file_upload_capacity", None)
        if reservation is not None:
            reservation.release()

    @app.post("/upload")
    def upload():
        try:
            ensure_storage_capacity(
                config.storage_root,
                required_bytes=request.content_length or 0,
            )
            ensure_storage_capacity(config.log_path.parent)
        except InsufficientStorageError:
            active_logger.warning("upload_rejected_insufficient_storage stage=preflight")
            return render_index(
                status_code=507,
                error=(
                    "서버 저장 공간이 부족하여 업로드할 수 없습니다. "
                    "서버 PC의 디스크 공간을 확보한 뒤 다시 시도하세요."
                ),
            )

        uploaded_file = request.files.get("file")
        memo = request.form.get("memo", "").strip()
        storage_subdir_input = request.form.get("storage_subdir", "")
        confirm_duplicate = request.form.get("confirm_duplicate") == "1"

        if not uploaded_file or not uploaded_file.filename:
            return render_index(
                status_code=400,
                error="업로드할 파일을 선택하세요.",
                storage_subdir=storage_subdir_input,
                memo=memo,
            )

        try:
            normalized_subdir = normalize_storage_subdir(storage_subdir_input)
            storage_dir = resolve_storage_path(normalized_subdir, config)
        except ValueError as exc:
            return render_index(
                status_code=400,
                error=str(exc),
                storage_subdir=storage_subdir_input,
                memo=memo,
            )

        original_filename = safe_filename(uploaded_file.filename)
        blocked_reason = blocked_upload_reason(original_filename, uploaded_file)
        if blocked_reason:
            return render_index(
                status_code=400,
                error=blocked_reason,
                storage_subdir=normalized_subdir,
                memo=memo,
            )
        try:
            reservation = reserve_upload_target(
                storage_dir,
                original_filename,
                confirm_duplicate=confirm_duplicate,
            )
        except UploadConflictError:
            return render_index(
                status_code=409,
                conflict={
                    "filename": original_filename,
                    "storage_subdir": normalized_subdir or "(기본 저장폴더)",
                },
                storage_subdir=normalized_subdir,
                memo=memo,
            )

        target_committed = False
        try:
            commit_uploaded_file(
                uploaded_file,
                reservation,
                storage_root=config.storage_root,
                progress_callback=g.file_upload_capacity.record_written,
            )
            target_committed = True

            download_url = build_download_url(reservation.upload_id, config)
            row = {
                "upload_id": reservation.upload_id,
                "uploaded_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
                "original_filename": original_filename,
                "stored_filename": reservation.stored_filename,
                "storage_subdir": normalized_subdir,
                "storage_path": str(reservation.target_path.resolve()),
                "memo": memo,
                "download_url": download_url,
            }
            append_upload_log(row, config)
        except InsufficientStorageError:
            active_logger.warning("upload_rejected_insufficient_storage stage=write")
            if target_committed:
                cleanup_created_file(reservation.target_path, existed_before=False)
            return render_index(
                status_code=507,
                error=(
                    "업로드 중 서버 저장 공간이 부족해져 파일을 저장하지 않았습니다. "
                    "디스크 공간을 확보한 뒤 다시 시도하세요."
                ),
                storage_subdir=normalized_subdir,
                memo=memo,
            )
        except Exception as exc:
            active_logger.exception("upload_transaction_failed")
            if target_committed:
                cleanup_created_file(reservation.target_path, existed_before=False)
            if is_storage_full_error(exc):
                return render_index(
                    status_code=507,
                    error=(
                        "업로드 처리 중 서버 저장 공간이 부족해졌습니다. "
                        "디스크 공간을 확보한 뒤 다시 시도하세요."
                    ),
                    storage_subdir=normalized_subdir,
                    memo=memo,
                )
            raise
        finally:
            release_upload_reservation(reservation)

        return render_index(
            result={
                **row,
                "loopback_warning": "1" if is_loopback_url(download_url) else "",
            },
            storage_subdir=normalized_subdir,
        )

    @app.get("/download/<upload_id>")
    def download(upload_id: str):
        row = find_upload(upload_id, config)
        if not row:
            abort(404)
        try:
            file_path = record_file_path(row, config)
        except ValueError:
            abort(404)
        if not file_path.exists() or not file_path.is_file():
            abort(404)
        response = send_file(
            file_path,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=row.get("original_filename") or file_path.name,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/delete/<upload_id>")
    def delete(upload_id: str):
        if not is_delete_allowed(request.remote_addr, config):
            abort(403)
        row = find_upload(upload_id, config)
        if not row:
            abort(404)
        try:
            if not delete_upload_log(upload_id, config):
                abort(404)
        except ValueError as exc:
            abort(409, description=str(exc))
        return redirect(url_for("index", deleted="1"))

    @app.get("/network-check/download")
    def network_check_download():
        try:
            size_mb = parse_network_check_size(request.args.get("size_mb"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        owner_id = uuid.uuid4().hex
        if not active_gate.acquire("http_quick", owner_id):
            return jsonify({"error": "다른 네트워크 측정이 진행 중입니다."}), 409
        total_bytes = network_check_total_bytes(size_mb)
        client_ip = normalize_ip(request.remote_addr)
        started_at = time.perf_counter()
        bytes_sent = 0

        def generate():
            nonlocal bytes_sent
            status = "failure"
            try:
                remaining = total_bytes
                while remaining > 0:
                    chunk_size = min(NETWORK_CHECK_CHUNK_SIZE, remaining)
                    chunk = NETWORK_CHECK_CHUNK if chunk_size == NETWORK_CHECK_CHUNK_SIZE else NETWORK_CHECK_CHUNK[:chunk_size]
                    bytes_sent += len(chunk)
                    remaining -= len(chunk)
                    yield chunk
                status = "success"
            finally:
                duration = time.perf_counter() - started_at
                try:
                    append_network_check_log(
                        build_network_check_log_row(
                            client_ip=client_ip,
                            direction="download",
                            size_mb=size_mb,
                            bytes_transferred=bytes_sent,
                            duration_seconds=duration,
                            status=status,
                        ),
                        config,
                    )
                finally:
                    active_gate.release("http_quick", owner_id)

        return Response(
            stream_with_context(generate()),
            mimetype="application/octet-stream",
            headers={
                "Cache-Control": "no-store, no-cache, max-age=0",
                "Content-Length": str(total_bytes),
                "Content-Disposition": f'attachment; filename="network-check-{size_mb}mb.bin"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/network-check/upload")
    def network_check_upload():
        try:
            size_mb = parse_network_check_size(request.args.get("size_mb"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        owner_id = uuid.uuid4().hex
        if not active_gate.acquire("http_quick", owner_id):
            return jsonify({"error": "다른 네트워크 측정이 진행 중입니다."}), 409
        expected_bytes = network_check_total_bytes(size_mb)
        client_ip = normalize_ip(request.remote_addr)
        started_at = time.perf_counter()
        bytes_received = 0
        status = "failure"
        status_code = 200
        error_message = ""

        try:
            while True:
                chunk = request.stream.read(NETWORK_CHECK_CHUNK_SIZE)
                if not chunk:
                    break
                bytes_received += len(chunk)
                if bytes_received > expected_bytes:
                    error_message = "요청 크기가 선택한 측정 데이터량보다 큽니다."
                    status_code = 400
                    break

            if not error_message and bytes_received != expected_bytes:
                error_message = "전송된 테스트 데이터 크기가 선택한 크기와 다릅니다."
                status_code = 400
            if not error_message:
                status = "success"
        except Exception:
            error_message = "네트워크 체크 업로드 중 오류가 발생했습니다."
            status_code = 500

        duration = time.perf_counter() - started_at
        row = build_network_check_log_row(
            client_ip=client_ip,
            direction="upload",
            size_mb=size_mb,
            bytes_transferred=bytes_received,
            duration_seconds=duration,
            status=status,
        )
        try:
            append_network_check_log(row, config)
            payload = {
                "direction": "upload",
                "size_mb": size_mb,
                "bytes_transferred": bytes_received,
                "duration_seconds": float(row["duration_seconds"]),
                "mbps": float(row["mbps"]),
                "status": status,
            }
            if error_message:
                payload["error"] = error_message
        finally:
            active_gate.release("http_quick", owner_id)
        return jsonify(payload), status_code

    @app.post("/network-check/upload/start")
    def network_check_upload_start():
        cleanup_expired_upload_sessions()
        try:
            size_mb = parse_network_check_size(request.args.get("size_mb"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        session_id = uuid.uuid4().hex
        if not active_gate.acquire("http_quick", session_id):
            return jsonify({"error": "다른 네트워크 측정이 진행 중입니다."}), 409
        session = NetworkCheckUploadSession(
            session_id=session_id,
            client_ip=normalize_ip(request.remote_addr),
            size_mb=size_mb,
            expected_bytes=network_check_total_bytes(size_mb),
            started_at=time.perf_counter(),
        )
        session.expiry_timer = threading.Timer(
            NETWORK_CHECK_UPLOAD_SESSION_TTL_SECONDS,
            expire_upload_session,
            args=(session_id,),
        )
        session.expiry_timer.daemon = True
        with upload_sessions_lock:
            upload_sessions[session_id] = session
        session.expiry_timer.start()
        return jsonify(
            {
                "session_id": session_id,
                "size_mb": size_mb,
                "total_bytes": session.expected_bytes,
                "chunk_size": NETWORK_CHECK_CHUNK_SIZE,
            }
        )

    @app.post("/network-check/upload/chunk/<session_id>")
    def network_check_upload_chunk(session_id: str):
        cleanup_expired_upload_sessions()
        chunk_bytes = 0
        while True:
            chunk = request.stream.read(NETWORK_CHECK_CHUNK_SIZE)
            if not chunk:
                break
            chunk_bytes += len(chunk)

        if chunk_bytes <= 0:
            return jsonify({"error": "전송된 테스트 데이터가 없습니다."}), 400

        failed_session = None
        with upload_sessions_lock:
            session = upload_sessions.get(session_id)
            if not session:
                return jsonify({"error": "네트워크 체크 업로드 세션을 찾을 수 없습니다."}), 404
            if session.bytes_received + chunk_bytes > session.expected_bytes:
                session.bytes_received += chunk_bytes
                failed_session = upload_sessions.pop(session_id)
            else:
                session.bytes_received += chunk_bytes
                return jsonify(
                    {
                        "session_id": session.session_id,
                        "size_mb": session.size_mb,
                        "bytes_received": session.bytes_received,
                        "total_bytes": session.expected_bytes,
                        "complete": session.bytes_received == session.expected_bytes,
                    }
                )

        payload = finalize_upload_session(
            failed_session,
            status="failure",
            error="요청 크기가 선택한 측정 데이터량보다 큽니다.",
        )
        return jsonify(payload), 400

    @app.post("/network-check/upload/finish/<session_id>")
    def network_check_upload_finish(session_id: str):
        cleanup_expired_upload_sessions()
        with upload_sessions_lock:
            session = upload_sessions.pop(session_id, None)
        if not session:
            return jsonify({"error": "네트워크 체크 업로드 세션을 찾을 수 없습니다."}), 404

        if session.bytes_received != session.expected_bytes:
            payload = finalize_upload_session(
                session,
                status="failure",
                error="전송된 테스트 데이터 크기가 선택한 크기와 다릅니다.",
            )
            return jsonify(payload), 400

        payload = finalize_upload_session(session, status="success")
        return jsonify(payload)

    return app


def run_smoke_check(config_path: str | os.PathLike[str] | None = None) -> int:
    config = load_config(config_path)
    instance_lock = DataDirectoryLock(
        config.log_path.parent / ".internal-upload.instance.lock"
    )
    try:
        instance_lock.acquire()
    except InstanceLockError as exc:
        print(f"Smoke check failed: {exc}", file=sys.stderr)
        return 1
    try:
        ensure_directories(config)
        with tempfile.TemporaryDirectory(prefix="internal-upload-smoke-") as temporary_root:
            diagnostic_logger = configure_diagnostic_logger(Path(temporary_root))
            app = None
            try:
                app = create_app(config_path, diagnostic_logger=diagnostic_logger)
                with app.test_client() as client:
                    response = client.get("/")
            finally:
                if app is not None:
                    detach_diagnostic_handlers(app.logger, diagnostic_logger)
                close_diagnostic_logger(diagnostic_logger)
    except (CsvIntegrityError, OSError) as exc:
        print(f"Smoke check failed: {exc}", file=sys.stderr)
        return 1
    finally:
        instance_lock.release()
    if response.status_code != 200:
        print(f"Smoke check failed: GET / returned {response.status_code}", file=sys.stderr)
        return 1
    print("Smoke check passed")
    return 0


def print_server_addresses(config: AppConfig, *, existing: bool = False) -> None:
    label = "이미 실행 중인 서버" if existing else "사내 업로드 서버"
    print(f"{label} 주소:")
    if config.host in {"0.0.0.0", "127.0.0.1", "localhost"}:
        print(f"  http://127.0.0.1:{config.port}")
    if config.host == "0.0.0.0":
        lan_ip = detect_lan_ip()
        if lan_ip != "127.0.0.1":
            print(f"  http://{lan_ip}:{config.port}")
    elif config.host not in {"127.0.0.1", "localhost"}:
        print(f"  http://{config.host}:{config.port}")


def print_firewall_status(port: int) -> None:
    status = check_windows_firewall_port(port)
    if status == FIREWALL_NOT_APPLICABLE:
        return
    if status == FIREWALL_ALLOWED:
        print(f"Windows 방화벽에서 TCP {port} 인바운드 허용 규칙을 확인했습니다.")
        return
    print(
        f"Windows 방화벽은 자동 조회하지 않습니다. TCP {port} 외부 접속이 실패하면 허용 규칙을 확인하세요.",
        file=sys.stderr,
    )
    print("다른 PC에서 접속할 수 없다면 관리자 터미널에서 다음 명령을 실행하세요:")
    print(f"  {firewall_add_command(port)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="사내 업로드 및 TCP 전송 성능 측정 서버")
    parser.add_argument("--smoke-check", action="store_true")
    parser.add_argument("--probe-self-check", action="store_true")
    parser.add_argument("--config", default="")
    args = parser.parse_args(argv)

    config_path = args.config or None
    if args.smoke_check:
        return run_smoke_check(config_path)
    if args.probe_self_check:
        from network_probe.client_package import runtime_client_bundle
        from network_probe.self_check import run_probe_self_check

        return run_probe_self_check(runtime_client_bundle())

    resolved_config_path = (
        Path(config_path).resolve() if config_path else APP_ROOT / "config.ini"
    )
    migration_required = config_requires_probe_enable_migration(resolved_config_path)
    migration_failed = False
    if migration_required:
        try:
            migration = migrate_config(resolved_config_path)
            if migration.probe_enabled_changed:
                print("기존 설정을 업데이트해 TCP 전송 성능 측정을 기본 활성화했습니다.")
        except OSError as exc:
            migration_failed = True
            print(
                f"설정 마이그레이션을 config.ini에 저장하지 못했습니다: {exc}",
                file=sys.stderr,
            )
            print("현재 실행에서는 TCP 전송 성능 측정을 활성화합니다.", file=sys.stderr)

    configured = load_config(config_path)
    if migration_required and migration_failed:
        configured = replace(configured, network_probe_enabled=True)
    try:
        resolution = resolve_startup_port(
            configured.host,
            configured.port,
            excluded_ports={configured.network_probe_port},
            existing_instance_check=lambda port: is_existing_instance(
                port,
                host=configured.host,
            ),
        )
    except PortChangeDeclined as exc:
        print(exc)
        return 0
    except StartupPortError as exc:
        print(f"사내 업로드 서버 시작 실패: {exc}", file=sys.stderr)
        return 2

    if resolution.existing_instance:
        print_server_addresses(configured, existing=True)
        print("중복 서버를 시작하지 않고 종료합니다.")
        return 0

    instance_lock = DataDirectoryLock(
        configured.log_path.parent / ".internal-upload.instance.lock"
    )
    try:
        instance_lock.acquire()
    except InstanceLockError as exc:
        print(f"사내 업로드 서버 시작 실패: {exc}", file=sys.stderr)
        return 2
    try:
        diagnostic_logger = configure_diagnostic_logger(configured.log_path.parent)
    except OSError as exc:
        instance_lock.release()
        print(f"진단 로그를 준비할 수 없어 서버를 시작하지 않습니다: {exc}", file=sys.stderr)
        return 2

    runtime_base_url, _ = rewrite_base_url_port(
        configured.base_url,
        configured.port,
        resolution.selected_port,
    )
    active_config = replace(
        configured,
        port=resolution.selected_port,
        base_url=runtime_base_url.rstrip("/"),
    )
    probe_port_resolution = None
    probe_port_error = ""
    if active_config.network_probe_enabled:
        try:
            probe_port_resolution = resolve_probe_port(
                active_config.host,
                active_config.network_probe_port,
                excluded_ports={active_config.port},
            )
            active_config = replace(
                active_config,
                network_probe_port=probe_port_resolution.selected_port,
            )
        except (PortChangeDeclined, StartupPortError) as exc:
            probe_port_error = str(exc)

    measurement_gate = NetworkMeasurementGate()
    probe_service = None
    web_server = None
    flask_app = None
    try:
        probe_service = ProbeService(
            config=build_probe_config(active_config),
            measurement_gate=measurement_gate,
            normalize_ip=normalize_ip,
        )
        if probe_port_error:
            probe_service.start_error = probe_port_error
        flask_app = create_app(
            config_path,
            app_config=active_config,
            probe_service=probe_service,
            measurement_gate=measurement_gate,
            diagnostic_logger=diagnostic_logger,
        )
        try:
            web_server = make_server(
                active_config.host,
                active_config.port,
                flask_app,
                threaded=True,
            )
        except (OSError, SystemExit) as exc:
            print(
                f"사내 업로드 서버가 TCP {active_config.port} 포트를 열지 못했습니다: {exc}",
                file=sys.stderr,
            )
            return 2

        if resolution.changed:
            resolved_config_path = (
                Path(config_path).resolve() if config_path else APP_ROOT / "config.ini"
            )
            try:
                update_result = persist_port_change(
                    resolved_config_path,
                    resolution.configured_port,
                    resolution.selected_port,
                )
            except OSError as exc:
                print(f"변경된 웹 포트를 config.ini에 저장하지 못했습니다: {exc}", file=sys.stderr)
                return 2
            print(
                f"웹 포트를 {resolution.configured_port}에서 "
                f"{resolution.selected_port}(으)로 변경하고 config.ini에 저장했습니다."
            )
            if update_result.base_url_changed:
                print("BASE_URL의 웹 포트도 새 포트로 변경했습니다.")
            if update_result.warning:
                print(f"주의: {update_result.warning}", file=sys.stderr)
            print_firewall_status(active_config.port)

        if active_config.network_probe_enabled:
            if probe_port_error:
                print(f"TCP 전송 성능 측정 서버 시작 실패: {probe_port_error}", file=sys.stderr)
                print("파일 업로드 웹 서버는 계속 실행합니다.", file=sys.stderr)
                diagnostic_logger.error("probe_server_start_failed reason=port_resolution")
            elif probe_service.start():
                print(f"TCP 전송 성능 측정 서버가 {active_config.network_probe_port} 포트에서 시작되었습니다.")
                diagnostic_logger.info(
                    "probe_server_started port=%s",
                    active_config.network_probe_port,
                )
                if probe_port_resolution and probe_port_resolution.changed:
                    try:
                        persist_probe_port_change(
                            resolved_config_path,
                            active_config.network_probe_port,
                        )
                        print(
                            f"TCP 측정 포트를 {probe_port_resolution.configured_port}에서 "
                            f"{active_config.network_probe_port}(으)로 변경하고 config.ini에 저장했습니다."
                        )
                    except OSError as exc:
                        print(
                            f"변경된 TCP 측정 포트를 config.ini에 저장하지 못했습니다: {exc}",
                            file=sys.stderr,
                        )
                print_firewall_status(active_config.network_probe_port)
            else:
                print(f"TCP 전송 성능 측정 서버 시작 실패: {probe_service.start_error}", file=sys.stderr)
                print("파일 업로드 웹 서버는 계속 실행합니다.", file=sys.stderr)
                diagnostic_logger.error("probe_server_start_failed reason=bind")

        print_server_addresses(active_config)
        print("종료하려면 Ctrl+C를 누르세요.")
        diagnostic_logger.info("web_server_started port=%s", active_config.port)
        try:
            web_server.serve_forever()
        except KeyboardInterrupt:
            print("사내 업로드 서버를 종료합니다.")
    except CsvIntegrityError as exc:
        print(f"사내 업로드 서버 시작 실패: {exc}", file=sys.stderr)
        return 2
    finally:
        if web_server is not None:
            web_server.begin_shutdown()
            drained = web_server.wait_for_active_requests(
                timeout_seconds=WEB_SHUTDOWN_DRAIN_SECONDS
            )
            if not drained:
                print(
                    "진행 중인 웹 요청이 30초 안에 끝나지 않아 연결을 종료합니다.",
                    file=sys.stderr,
                )
                diagnostic_logger.warning(
                    "web_shutdown_drain_timeout active_requests=%s",
                    web_server.active_request_count,
                )
        if probe_service is not None:
            probe_service.stop()
        if web_server is not None:
            web_server.server_close()
        diagnostic_logger.info("application_stopped")
        if flask_app is not None:
            detach_diagnostic_handlers(flask_app.logger, diagnostic_logger)
        close_diagnostic_logger(diagnostic_logger)
        instance_lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
