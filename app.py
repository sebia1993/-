from __future__ import annotations

import csv
import argparse
import ipaddress
import os
import re
import socket
import sys
import threading
import time
import uuid
from configparser import ConfigParser
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote, urlparse

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, stream_with_context, url_for
from werkzeug.serving import make_server

from network_sustained import create_sustained_blueprint, ensure_sustained_storage
from network_measurement import NetworkMeasurementGate
from network_probe.agent import ProbeClientError, run_probe_client
from network_probe.models import ProbeConfig
from network_probe.routes import create_probe_blueprint
from network_probe.service import ProbeService, ensure_probe_storage
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
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_csv_lock = threading.Lock()
_network_check_csv_lock = threading.Lock()


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


def ensure_directories(config: AppConfig) -> None:
    config.storage_root.mkdir(parents=True, exist_ok=True)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_log_file(config.log_path)
    ensure_network_check_log_file(config.network_check_log_path)
    ensure_sustained_storage(config.network_check_session_log_path, config.network_check_results_root)
    ensure_probe_storage(config.network_probe_log_path, config.network_probe_results_root)


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


def generate_upload_id(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    return f"{current.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def reserve_upload_target(
    storage_dir: Path,
    original_filename: str,
    *,
    confirm_duplicate: bool,
) -> tuple[str, str, Path]:
    storage_dir.mkdir(parents=True, exist_ok=True)
    original_target = storage_dir / original_filename
    original_exists = original_target.exists()
    if original_exists and not confirm_duplicate:
        raise UploadConflictError

    upload_id = generate_upload_id()
    if not original_exists:
        try:
            original_target.touch(exist_ok=False)
        except FileExistsError:
            if not confirm_duplicate:
                raise UploadConflictError from None
        else:
            return upload_id, original_filename, original_target

    for _ in range(10):
        stored_filename = f"{upload_id}_{original_filename}"
        target_path = storage_dir / stored_filename
        try:
            target_path.touch(exist_ok=False)
        except FileExistsError:
            upload_id = generate_upload_id()
            continue
        return upload_id, stored_filename, target_path
    raise OSError("고유한 업로드 저장 경로를 예약할 수 없습니다.")


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
        ensure_log_file(config.log_path)
        _append_csv_row_with_rollback(config.log_path, CSV_FIELDS, row)


def read_upload_log(config: AppConfig, limit: int | None = None) -> list[dict[str, str]]:
    ensure_log_file(config.log_path)
    with config.log_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows = [row for row in rows if row.get("upload_id")]
    rows.reverse()
    return rows[:limit] if limit else rows


def find_upload(upload_id: str, config: AppConfig) -> dict[str, str] | None:
    for row in read_upload_log(config):
        if row.get("upload_id") == upload_id:
            return row
    return None


def _write_upload_log_rows(log_path: Path, rows: list[dict[str, str]]) -> None:
    temporary_path = log_path.with_name(f".{log_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, log_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def delete_upload_log(upload_id: str, config: AppConfig) -> bool:
    with _csv_lock:
        ensure_log_file(config.log_path)
        with config.log_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        kept_rows = [row for row in rows if row.get("upload_id") != upload_id]
        deleted = len(kept_rows) != len(rows)
        if not deleted:
            return False

        deleted_row = next(row for row in rows if row.get("upload_id") == upload_id)
        file_path = record_file_path(deleted_row)
        should_delete_file = file_path.exists() and file_path.is_file()
        _write_upload_log_rows(config.log_path, kept_rows)
        if should_delete_file:
            try:
                file_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                _write_upload_log_rows(config.log_path, rows)
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


def record_file_path(row: dict[str, str]) -> Path:
    return Path(row.get("storage_path", "")).expanduser().resolve()


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


def read_network_check_log(config: AppConfig, limit: int | None = None) -> list[dict[str, str]]:
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
    probe_client_executable_path: str | os.PathLike[str] | None = None,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(RESOURCE_ROOT / "templates"),
        static_folder=str(RESOURCE_ROOT / "static"),
    )
    config = app_config or load_config(config_path)
    ensure_directories(config)
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
            client_executable_path=probe_client_executable_path,
        )
    )
    app.extensions["sustained_network_check"] = sustained_manager
    app.extensions["network_measurement_gate"] = active_gate
    app.extensions["network_probe"] = active_probe_service

    @app.get("/api/health")
    def health_check():
        response = jsonify({"app": APP_ID, "status": "ok", "port": config.port})
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
        except Exception as exc:
            print(f"네트워크 체크 업로드 세션 만료 기록 실패: {exc}", file=sys.stderr)

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

    @app.post("/upload")
    def upload():
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
        try:
            upload_id, stored_filename, target_path = reserve_upload_target(
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

        try:
            uploaded_file.save(target_path)

            download_url = build_download_url(upload_id, config)
            row = {
                "upload_id": upload_id,
                "uploaded_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
                "original_filename": original_filename,
                "stored_filename": stored_filename,
                "storage_subdir": normalized_subdir,
                "storage_path": str(target_path.resolve()),
                "memo": memo,
                "download_url": download_url,
            }
            append_upload_log(row, config)
        except Exception:
            cleanup_created_file(target_path, existed_before=False)
            raise

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
        file_path = record_file_path(row)
        if not file_path.exists() or not file_path.is_file():
            abort(404)
        return send_file(
            file_path,
            as_attachment=True,
            download_name=row.get("original_filename") or file_path.name,
        )

    @app.post("/delete/<upload_id>")
    def delete(upload_id: str):
        if not is_delete_allowed(request.remote_addr, config):
            abort(403)
        row = find_upload(upload_id, config)
        if not row:
            abort(404)
        if not delete_upload_log(upload_id, config):
            abort(404)
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
    ensure_directories(config)
    app = create_app(config_path)
    with app.test_client() as client:
        response = client.get("/")
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
        f"Windows 방화벽에서 TCP {port} 허용 규칙을 확인하지 못했습니다.",
        file=sys.stderr,
    )
    print("다른 PC에서 접속할 수 없다면 관리자 터미널에서 다음 명령을 실행하세요:")
    print(f"  {firewall_add_command(port)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="사내 업로드 서버 및 TCP 측정 클라이언트")
    parser.add_argument("--smoke-check", action="store_true")
    parser.add_argument("--probe-client", action="store_true")
    parser.add_argument("--server", default="")
    parser.add_argument("--probe-self-check", action="store_true")
    parser.add_argument("--config", default="")
    args = parser.parse_args(argv)

    config_path = args.config or None
    if args.smoke_check:
        return run_smoke_check(config_path)
    if args.probe_client:
        if not args.server:
            parser.error("--probe-client에는 --server 주소가 필요합니다.")
        try:
            return run_probe_client(args.server)
        except ProbeClientError as exc:
            print(f"TCP 측정 클라이언트 실행 실패: {exc}", file=sys.stderr)
            return 2
    if args.probe_self_check:
        from network_probe.client_package import runtime_client_executable
        from network_probe.self_check import run_probe_self_check

        return run_probe_self_check(runtime_client_executable())

    resolved_config_path = (
        Path(config_path).resolve() if config_path else APP_ROOT / "config.ini"
    )
    migration_required = config_requires_probe_enable_migration(resolved_config_path)
    migration_failed = False
    if migration_required:
        try:
            migration = migrate_config(resolved_config_path)
            if migration.probe_enabled_changed:
                print("기존 설정을 업데이트해 TCP 정밀 측정을 기본 활성화했습니다.")
        except OSError as exc:
            migration_failed = True
            print(
                f"설정 마이그레이션을 config.ini에 저장하지 못했습니다: {exc}",
                file=sys.stderr,
            )
            print("현재 실행에서는 TCP 정밀 측정을 활성화합니다.", file=sys.stderr)

    configured = load_config(config_path)
    if migration_required and migration_failed:
        configured = replace(configured, network_probe_enabled=True)
    ensure_directories(configured)
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
    probe_service = ProbeService(
        config=build_probe_config(active_config),
        measurement_gate=measurement_gate,
        normalize_ip=normalize_ip,
    )
    if probe_port_error:
        probe_service.start_error = probe_port_error
    web_server = None
    try:
        flask_app = create_app(
            config_path,
            app_config=active_config,
            probe_service=probe_service,
            measurement_gate=measurement_gate,
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
                print(f"TCP 정밀 측정 서버 시작 실패: {probe_port_error}", file=sys.stderr)
                print("파일 업로드 웹 서버는 계속 실행합니다.", file=sys.stderr)
            elif probe_service.start():
                print(f"TCP 정밀 측정 서버가 {active_config.network_probe_port} 포트에서 시작되었습니다.")
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
                print(f"TCP 정밀 측정 서버 시작 실패: {probe_service.start_error}", file=sys.stderr)
                print("파일 업로드 웹 서버는 계속 실행합니다.", file=sys.stderr)

        print_server_addresses(active_config)
        print("종료하려면 Ctrl+C를 누르세요.")
        try:
            web_server.serve_forever()
        except KeyboardInterrupt:
            print("사내 업로드 서버를 종료합니다.")
    finally:
        probe_service.stop()
        if web_server is not None:
            web_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
