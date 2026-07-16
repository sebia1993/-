from __future__ import annotations

import argparse
import csv
import http.client
import json
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DURATION_MINUTES = 45.0
HEALTH_TIMEOUT_SECONDS = 20.0
PROCESS_STOP_TIMEOUT_SECONDS = 10.0
SOAK_UPLOAD_BYTES = 256 * 1024


@dataclass(frozen=True)
class SoakSummary:
    status: str
    duration_seconds: float
    completed_cycles: int
    uploaded_bytes: int
    tcp_self_checks: int


def available_port(*, excluded: set[int] | None = None) -> int:
    blocked = excluded or set()
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        if port not in blocked:
            return port
    raise RuntimeError("서로 다른 로컬 시험 포트를 확보하지 못했습니다.")


def write_soak_config(root: Path, web_port: int, probe_port: int) -> Path:
    config_path = root / "config.ini"
    config_path.write_text(
        "\n".join(
            [
                "[app]",
                "CONFIG_VERSION=2",
                "HOST=127.0.0.1",
                f"PORT={web_port}",
                f"BASE_URL=http://127.0.0.1:{web_port}",
                "STORAGE_ROOT=uploads",
                "DELETE_ALLOWED_IPS=127.0.0.1,::1",
                "RECENT_LIMIT=50",
                "",
                "[network_probe]",
                "ENABLED=true",
                f"PORT={probe_port}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def start_server(config_path: Path, log_path: Path) -> subprocess.Popen[bytes]:
    with log_path.open("ab") as output:
        return subprocess.Popen(
            [sys.executable, "app.py", "--config", str(config_path)],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )


def process_log_tail(log_path: Path, *, limit: int = 8_000) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def wait_for_health(
    web_port: int,
    process: subprocess.Popen[bytes],
    log_path: Path,
    *,
    timeout_seconds: float = HEALTH_TIMEOUT_SECONDS,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "시험 서버가 상태 확인 전에 종료되었습니다.\n"
                + process_log_tail(log_path)
            )
        connection = http.client.HTTPConnection("127.0.0.1", web_port, timeout=2)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            probe = payload.get("checks", {}).get("tcp_probe", {})
            if (
                response.status == 200
                and payload.get("app") == "internal-upload"
                and probe.get("enabled") is True
                and probe.get("available") is True
            ):
                return payload
            last_error = f"HTTP {response.status}, TCP probe={probe!r}"
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        finally:
            connection.close()
        time.sleep(0.1)
    raise RuntimeError(
        f"시험 서버 상태 확인 시간이 초과되었습니다: {last_error}\n"
        + process_log_tail(log_path)
    )


def stop_server(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)


def wait_for_port_release(port: int, *, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                time.sleep(0.05)
                continue
            return
    raise RuntimeError(f"TCP {port} 포트가 서버 종료 후 해제되지 않았습니다.")


def build_multipart_upload(filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"internal-upload-soak-{uuid.uuid4().hex}"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="storage_subdir"\r\n\r\n'
        "soak\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="memo"\r\n\r\n'
        "windows stability soak\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode("ascii")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    return prefix + content + suffix, boundary


def upload_file(web_port: int, filename: str, content: bytes) -> None:
    body, boundary = build_multipart_upload(filename, content)
    connection = http.client.HTTPConnection("127.0.0.1", web_port, timeout=30)
    try:
        connection.request(
            "POST",
            "/upload",
            body=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        response_body = response.read()
        if response.status != 200:
            raise RuntimeError(
                f"시험 업로드 실패: HTTP {response.status}, body={response_body[-500:]!r}"
            )
    finally:
        connection.close()


def find_download_path(data_root: Path, filename: str) -> str:
    log_path = data_root / "upload_log.csv"
    with log_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in reversed(rows):
        if row.get("original_filename") == filename:
            parsed = urlsplit(row.get("download_url", ""))
            if parsed.path.startswith("/download/"):
                return parsed.path
    raise RuntimeError(f"업로드 기록에서 {filename} 다운로드 경로를 찾지 못했습니다.")


def download_file(web_port: int, path: str) -> bytes:
    connection = http.client.HTTPConnection("127.0.0.1", web_port, timeout=30)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        content = response.read()
        if response.status != 200:
            raise RuntimeError(f"시험 다운로드 실패: HTTP {response.status}")
        return content
    finally:
        connection.close()


def run_tcp_self_check() -> None:
    completed = subprocess.run(
        [sys.executable, "app.py", "--probe-self-check"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "TCP 자체 시험 실패:\n"
            + completed.stdout[-4_000:]
            + completed.stderr[-4_000:]
        )


def run_cycle(root: Path, cycle: int) -> None:
    web_port = available_port()
    probe_port = available_port(excluded={web_port})
    config_path = write_soak_config(root, web_port, probe_port)
    filename = f"soak-{cycle:06d}.txt"
    marker = f"cycle={cycle};".encode("ascii")
    content = (marker * ((SOAK_UPLOAD_BYTES // len(marker)) + 1))[:SOAK_UPLOAD_BYTES]
    first_log = root / f"server-{cycle:06d}-first.log"
    second_log = root / f"server-{cycle:06d}-restart.log"

    first = start_server(config_path, first_log)
    try:
        wait_for_health(web_port, first, first_log)
        upload_file(web_port, filename, content)
        download_path = find_download_path(root / "data", filename)
    finally:
        stop_server(first)
    wait_for_port_release(web_port)
    wait_for_port_release(probe_port)

    restarted = start_server(config_path, second_log)
    try:
        wait_for_health(web_port, restarted, second_log)
        if download_file(web_port, download_path) != content:
            raise RuntimeError("서버 재시작 후 다운로드 내용이 업로드 원본과 다릅니다.")
    finally:
        stop_server(restarted)
    wait_for_port_release(web_port)
    wait_for_port_release(probe_port)
    run_tcp_self_check()


def run_soak(*, duration_minutes: float, max_cycles: int | None = None) -> SoakSummary:
    duration_seconds = max(float(duration_minutes), 0.01) * 60.0
    started = time.monotonic()
    deadline = started + duration_seconds
    completed_cycles = 0
    with tempfile.TemporaryDirectory(prefix="internal-upload-windows-soak-") as temporary_root:
        root = Path(temporary_root)
        while completed_cycles == 0 or time.monotonic() < deadline:
            if max_cycles is not None and completed_cycles >= max_cycles:
                break
            run_cycle(root, completed_cycles + 1)
            completed_cycles += 1
            print(
                f"soak cycle {completed_cycles} passed "
                f"({time.monotonic() - started:.1f}s elapsed)",
                flush=True,
            )
    return SoakSummary(
        status="success",
        duration_seconds=round(time.monotonic() - started, 3),
        completed_cycles=completed_cycles,
        uploaded_bytes=completed_cycles * SOAK_UPLOAD_BYTES,
        tcp_self_checks=completed_cycles,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows 장시간 안정성 반복 시험")
    parser.add_argument("--duration-minutes", type=float, default=DEFAULT_DURATION_MINUTES)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--summary-path", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 0.01 <= args.duration_minutes <= 60:
        raise SystemExit("--duration-minutes는 0.01~60 범위여야 합니다.")
    if args.max_cycles is not None and args.max_cycles < 1:
        raise SystemExit("--max-cycles는 1 이상이어야 합니다.")
    summary = run_soak(
        duration_minutes=args.duration_minutes,
        max_cycles=args.max_cycles,
    )
    payload = json.dumps(asdict(summary), ensure_ascii=False, indent=2)
    print(payload)
    if args.summary_path:
        Path(args.summary_path).write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
