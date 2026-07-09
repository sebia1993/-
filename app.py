from __future__ import annotations

import csv
import ipaddress
import os
import re
import socket
import sys
import threading
import uuid
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote, urlparse

from flask import Flask, abort, redirect, render_template, request, send_file, url_for


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


def load_config(config_path: str | os.PathLike[str] | None = None) -> AppConfig:
    path = Path(config_path).resolve() if config_path else APP_ROOT / "config.ini"
    app_root = path.parent

    parser = ConfigParser()
    parser[CONFIG_SECTION] = {
        "HOST": "0.0.0.0",
        "PORT": "8000",
        "BASE_URL": "",
        "STORAGE_ROOT": "uploads",
        "DELETE_ALLOWED_IPS": "127.0.0.1,::1",
        "RECENT_LIMIT": "50",
    }
    if path.exists():
        parser.read(path, encoding="utf-8")

    section = parser[CONFIG_SECTION]
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
    )


def parse_csv_list(value: str) -> tuple[str, ...]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(items or ["127.0.0.1", "::1"])


def ensure_directories(config: AppConfig) -> None:
    config.storage_root.mkdir(parents=True, exist_ok=True)
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_log_file(config.log_path)


def ensure_log_file(log_path: Path) -> None:
    if log_path.exists() and log_path.stat().st_size > 0:
        return
    with log_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
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


def append_upload_log(row: dict[str, str], config: AppConfig) -> None:
    with _csv_lock:
        ensure_log_file(config.log_path)
        with config.log_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


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


def delete_upload_log(upload_id: str, config: AppConfig) -> bool:
    with _csv_lock:
        ensure_log_file(config.log_path)
        with config.log_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        kept_rows = [row for row in rows if row.get("upload_id") != upload_id]
        deleted = len(kept_rows) != len(rows)
        with config.log_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(kept_rows)
        return deleted


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


def create_app(config_path: str | os.PathLike[str] | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(RESOURCE_ROOT / "templates"),
        static_folder=str(RESOURCE_ROOT / "static"),
    )
    config = load_config(config_path)
    ensure_directories(config)

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
        original_target = storage_dir / original_filename
        if original_target.exists() and not confirm_duplicate:
            return render_index(
                status_code=409,
                conflict={
                    "filename": original_filename,
                    "storage_subdir": normalized_subdir or "(기본 저장폴더)",
                },
                storage_subdir=normalized_subdir,
                memo=memo,
            )

        upload_id = generate_upload_id()
        stored_filename = original_filename
        if original_target.exists():
            stored_filename = f"{upload_id}_{original_filename}"
        target_path = storage_dir / stored_filename
        storage_dir.mkdir(parents=True, exist_ok=True)
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
        file_path = record_file_path(row)
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
        delete_upload_log(upload_id, config)
        return redirect(url_for("index", deleted="1"))

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


if __name__ == "__main__":
    if "--smoke-check" in sys.argv:
        raise SystemExit(run_smoke_check())

    active_config = load_config()
    ensure_directories(active_config)
    create_app().run(
        host=active_config.host,
        port=active_config.port,
        debug=False,
    )
