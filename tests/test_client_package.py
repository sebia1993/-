from __future__ import annotations

import io
from zipfile import ZipFile

import pytest

from app_version import APP_VERSION
from network_probe.client_package import (
    ClientPackageError,
    build_client_package,
    resolve_client_server_url,
    verify_client_package,
)


@pytest.mark.parametrize(
    ("request_host", "expected"),
    [
        ("192.168.10.25:8123", "http://192.168.10.25:8123"),
        ("SERVER-PC:9000", "http://server-pc:9000"),
        ("fileserver.local", "http://fileserver.local:80"),
    ],
)
def test_resolve_client_server_url_uses_current_request_host(request_host, expected):
    assert (
        resolve_client_server_url(
            request_host,
            fallback_host="192.168.10.99",
            fallback_port=8000,
        )
        == expected
    )


@pytest.mark.parametrize("request_host", ["localhost:9999", "127.0.0.1:9999", "127.10.20.30"])
def test_resolve_client_server_url_replaces_loopback_with_lan_ipv4(request_host):
    assert (
        resolve_client_server_url(
            request_host,
            fallback_host="10.20.30.40",
            fallback_port=8123,
        )
        == "http://10.20.30.40:8123"
    )


@pytest.mark.parametrize(
    "request_host",
    [
        "",
        "user@server-pc:8000",
        "server-pc/path:8000",
        "server-pc%COMSPEC%:8000",
        "server-pc&calc:8000",
        "server-pc|calc:8000",
        "[::1]:8000",
        "999.999.999.999:8000",
        "server-pc:0",
        "server-pc:65536",
    ],
)
def test_resolve_client_server_url_rejects_unsafe_or_unsupported_hosts(request_host):
    with pytest.raises(ClientPackageError):
        resolve_client_server_url(
            request_host,
            fallback_host="10.20.30.40",
            fallback_port=8000,
        )


def test_resolve_client_server_url_rejects_loopback_fallback():
    with pytest.raises(ClientPackageError, match="사내 IPv4"):
        resolve_client_server_url(
            "localhost:8000",
            fallback_host="127.0.0.1",
            fallback_port=8000,
        )


def test_build_client_package_contains_only_autoconnect_client_files(tmp_path):
    executable = tmp_path / "InternalUpload.exe"
    executable.write_bytes(b"MZ-client-test")
    server_url = "http://server-pc:8000"

    package = build_client_package(executable, server_url)

    assert package.download_name == "internal-upload-client_server-pc.zip"
    assert package.root_name == "InternalUpload_Client_server-pc"
    assert verify_client_package(package.payload, server_url) == []
    with ZipFile(io.BytesIO(package.payload)) as archive:
        assert set(archive.namelist()) == {
            "InternalUpload_Client_server-pc/InternalUpload.exe",
            "InternalUpload_Client_server-pc/start_tcp_probe_client.cmd",
            "InternalUpload_Client_server-pc/README_CLIENT_KO.txt",
        }
        assert archive.read("InternalUpload_Client_server-pc/InternalUpload.exe") == b"MZ-client-test"
        command = archive.read(
            "InternalUpload_Client_server-pc/start_tcp_probe_client.cmd"
        ).decode("utf-8-sig")
        readme = archive.read("InternalUpload_Client_server-pc/README_CLIENT_KO.txt").decode(
            "utf-8-sig"
        )
    assert f'--probe-client --server "{server_url}"' in command
    assert APP_VERSION in command
    assert "set /p" not in command.lower()
    assert server_url in readme
    assert APP_VERSION in readme
    assert "TCP 전송 성능 측정" in readme
    assert "config.ini" in readme
    assert "TCP 측정 포트는 서버가 자동으로 전달" in readme


def test_build_client_package_rejects_missing_executable(tmp_path):
    with pytest.raises(ClientPackageError, match="실행 파일"):
        build_client_package(tmp_path / "missing.exe", "http://server-pc:8000")
