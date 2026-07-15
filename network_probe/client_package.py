from __future__ import annotations

import io
import ipaddress
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_STORED, ZipFile

from app_version import APP_VERSION


HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
NUMERIC_ADDRESS_RE = re.compile(r"^[0-9.]+$")


class ClientPackageError(ValueError):
    pass


@dataclass(frozen=True)
class ClientPackage:
    payload: bytes
    server_url: str
    download_name: str
    root_name: str


def runtime_client_executable() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).resolve()
    if path.suffix.lower() != ".exe":
        return None
    return path


def _normalize_host(value: str) -> tuple[str, ipaddress.IPv4Address | None]:
    if not value or value != value.strip():
        raise ClientPackageError("현재 접속 주소의 호스트 형식이 올바르지 않습니다.")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ClientPackageError("클라이언트 ZIP은 ASCII PC 이름 또는 IPv4 주소만 지원합니다.") from exc

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        address = None
    if address is not None:
        if not isinstance(address, ipaddress.IPv4Address):
            raise ClientPackageError("클라이언트 ZIP은 IPv6 주소를 지원하지 않습니다.")
        if address.is_unspecified or address.is_multicast:
            raise ClientPackageError("클라이언트가 접속할 수 있는 서버 IPv4 주소가 아닙니다.")
        return str(address), address

    if NUMERIC_ADDRESS_RE.fullmatch(value):
        raise ClientPackageError("서버 IPv4 주소 형식이 올바르지 않습니다.")
    if len(value) > 253:
        raise ClientPackageError("서버 PC 이름이 너무 깁니다.")
    labels = value.split(".")
    if not labels or any(not HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise ClientPackageError("서버 PC 이름에는 영문자, 숫자, 점과 하이픈만 사용할 수 있습니다.")
    return value.lower(), None


def _parse_host_port(value: str, default_port: int) -> tuple[str, int, ipaddress.IPv4Address | None]:
    raw = value.strip()
    if not raw or raw != value:
        raise ClientPackageError("현재 접속 주소의 형식이 올바르지 않습니다.")
    if raw.startswith("[") or raw.count(":") > 1:
        raise ClientPackageError("클라이언트 ZIP은 IPv6 주소를 지원하지 않습니다.")

    host_value = raw
    port = default_port
    if ":" in raw:
        host_value, port_value = raw.rsplit(":", 1)
        if not port_value.isascii() or not port_value.isdigit():
            raise ClientPackageError("서버 웹 포트 형식이 올바르지 않습니다.")
        port = int(port_value)
    if not 1 <= port <= 65535:
        raise ClientPackageError("서버 웹 포트는 1~65535 범위여야 합니다.")

    host, address = _normalize_host(host_value)
    return host, port, address


def resolve_client_server_url(
    request_host: str,
    *,
    fallback_host: str,
    fallback_port: int,
    scheme: str = "http",
) -> str:
    if scheme.lower() != "http":
        raise ClientPackageError("TCP 측정 클라이언트는 HTTP 서버 주소만 지원합니다.")
    host, port, address = _parse_host_port(request_host, 80)
    is_loopback = host == "localhost" or bool(address and address.is_loopback)
    if is_loopback:
        host, fallback_address = _normalize_host(fallback_host.strip())
        if host == "localhost" or (fallback_address and fallback_address.is_loopback):
            raise ClientPackageError("서버 PC의 사내 IPv4 주소를 자동 감지하지 못했습니다.")
        if not 1 <= fallback_port <= 65535:
            raise ClientPackageError("설정된 서버 웹 포트가 올바르지 않습니다.")
        port = fallback_port
    return f"http://{host}:{port}"


def _validated_server_host(server_url: str) -> str:
    parsed = urlsplit(server_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ClientPackageError("클라이언트 서버 URL 형식이 올바르지 않습니다.")
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ClientPackageError("클라이언트 서버 URL에는 주소와 포트만 사용할 수 있습니다.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ClientPackageError("클라이언트 서버 URL 포트가 올바르지 않습니다.") from exc
    if port is None:
        raise ClientPackageError("클라이언트 서버 URL에 웹 포트가 필요합니다.")
    host, _ = _normalize_host(parsed.hostname)
    return host


def _client_command(server_url: str) -> bytes:
    text = "\r\n".join(
        [
            "@echo off",
            "chcp 65001 >nul",
            'cd /d "%~dp0"',
            f"echo TCP 전송 성능 측정 클라이언트 {APP_VERSION}를 시작합니다.",
            f"echo 서버: {server_url}",
            "echo 이 창을 닫지 마세요. 종료하려면 Ctrl+C를 누르세요.",
            "echo.",
            f'InternalUpload.exe --probe-client --server "{server_url}"',
            'set "EXIT_CODE=%ERRORLEVEL%"',
            "echo.",
            'if not "%EXIT_CODE%"=="0" echo TCP 측정 클라이언트가 오류로 종료되었습니다.',
            "pause",
            "exit /b %EXIT_CODE%",
            "",
        ]
    )
    return text.encode("utf-8-sig")


def _client_readme(server_url: str) -> bytes:
    text = "\r\n".join(
        [
            "사내 업로드 TCP 전송 성능 측정 Windows 클라이언트",
            "",
            f"클라이언트 버전: {APP_VERSION}",
            f"자동 설정된 서버 주소: {server_url}",
            "",
            "1. ZIP 파일을 원하는 폴더에 완전히 압축 해제합니다.",
            "2. start_tcp_probe_client.cmd를 더블클릭합니다.",
            "3. 콘솔 창을 열어 둔 상태에서 서버 웹 화면의 TCP 전송 성능 측정을 실행합니다.",
            "4. 종료할 때는 콘솔 창에서 Ctrl+C를 누릅니다.",
            "",
            "별도 주소 입력이나 config.ini 설정은 필요하지 않습니다.",
            "서버 PC의 IP 또는 웹 포트가 바뀌면 서버 웹 화면에서 ZIP을 다시 받으세요.",
            "TCP 측정 포트는 서버가 자동으로 전달하므로 포트만 바뀐 경우에는 다시 받을 필요가 없습니다.",
            "클라이언트 PC의 인바운드 방화벽 포트는 열 필요가 없습니다.",
            "코드서명하지 않은 EXE이므로 Windows SmartScreen 경고가 표시될 수 있습니다.",
            "",
        ]
    )
    return text.encode("utf-8-sig")


def verify_client_package(payload: bytes, server_url: str) -> list[str]:
    errors: list[str] = []
    host = _validated_server_host(server_url)
    root_name = f"InternalUpload_Client_{host}"
    expected = {
        f"{root_name}/InternalUpload.exe",
        f"{root_name}/start_tcp_probe_client.cmd",
        f"{root_name}/README_CLIENT_KO.txt",
    }
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            if names != expected:
                errors.append("클라이언트 ZIP 파일 구성이 올바르지 않습니다.")
            command_name = f"{root_name}/start_tcp_probe_client.cmd"
            readme_name = f"{root_name}/README_CLIENT_KO.txt"
            if command_name in names:
                command = archive.read(command_name).decode("utf-8-sig")
                if server_url not in command or "--probe-client --server" not in command:
                    errors.append("클라이언트 실행 명령에 서버 주소가 없습니다.")
                if "set /p" in command.lower():
                    errors.append("클라이언트 실행 명령에 주소 입력 프롬프트가 남아 있습니다.")
            if readme_name in names and server_url not in archive.read(readme_name).decode("utf-8-sig"):
                errors.append("클라이언트 안내문에 서버 주소가 없습니다.")
            if any(name.lower().endswith("config.ini") for name in names):
                errors.append("클라이언트 ZIP에 config.ini가 포함되어 있습니다.")
    except (OSError, ValueError) as exc:
        errors.append(f"클라이언트 ZIP을 확인할 수 없습니다: {exc}")
    return errors


def build_client_package(executable_path: Path, server_url: str) -> ClientPackage:
    host = _validated_server_host(server_url)
    if not executable_path.is_file():
        raise ClientPackageError("Windows 클라이언트 실행 파일을 찾을 수 없습니다.")
    root_name = f"InternalUpload_Client_{host}"
    output = io.BytesIO()
    try:
        with ZipFile(output, mode="w", compression=ZIP_STORED, allowZip64=True) as archive:
            archive.write(executable_path, f"{root_name}/InternalUpload.exe")
            archive.writestr(f"{root_name}/start_tcp_probe_client.cmd", _client_command(server_url))
            archive.writestr(f"{root_name}/README_CLIENT_KO.txt", _client_readme(server_url))
    except OSError as exc:
        raise ClientPackageError(f"Windows 클라이언트 ZIP 생성에 실패했습니다: {exc}") from exc

    payload = output.getvalue()
    errors = verify_client_package(payload, server_url)
    if errors:
        raise ClientPackageError(errors[0])
    return ClientPackage(
        payload=payload,
        server_url=server_url,
        download_name=f"internal-upload-client_{host}.zip",
        root_name=root_name,
    )
