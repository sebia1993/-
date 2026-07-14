from __future__ import annotations

import argparse
import csv
import sys
from pathlib import PurePosixPath
from zipfile import ZipFile


REQUIRED_FILES = {
    "InternalUpload.exe",
    "start_internal_upload.cmd",
    "start_tcp_probe_client.cmd",
    "config.ini",
    "README_START_HERE_KO.txt",
    "README.md",
    "RELEASE_NOTES.md",
    "CHANGELOG.md",
    "data/upload_log.csv",
    "data/network_check_log.csv",
    "data/network_check_session_log.csv",
    "data/network_check_results/README_RESULTS_KO.txt",
    "data/network_probe_log.csv",
    "data/network_probe_results/README_RESULTS_KO.txt",
    "uploads/README_UPLOADS_KO.txt",
}
FORBIDDEN_PARTS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "tests",
    "tools",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".spec"}


def normalized_names(zip_file: ZipFile) -> set[str]:
    names = set()
    for name in zip_file.namelist():
        normalized = name.replace("\\", "/").lstrip("/")
        if normalized:
            names.add(normalized)
    return names


def validate_no_forbidden_entries(names: set[str]) -> list[str]:
    errors = []
    for name in sorted(names):
        path = PurePosixPath(name)
        parts = set(path.parts)
        if parts & FORBIDDEN_PARTS:
            errors.append(f"forbidden path in ZIP: {name}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden file suffix in ZIP: {name}")
    return errors


def validate_csv_header(zip_file: ZipFile, name: str, expected_prefix: list[str]) -> list[str]:
    with zip_file.open(name) as handle:
        text = handle.read().decode("utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    if not rows or rows[0][: len(expected_prefix)] != expected_prefix:
        return [f"{name} has an unexpected header"]
    if len(rows) != 1:
        return [f"{name} must contain only the initial header row"]
    return []


def validate_manual_probe_launcher(zip_file: ZipFile) -> list[str]:
    command = zip_file.read("start_tcp_probe_client.cmd").decode("utf-8-sig")
    errors = []
    if "--probe-client --server" not in command:
        errors.append("start_tcp_probe_client.cmd does not start probe client mode")
    if "set /p" not in command.lower():
        errors.append("start_tcp_probe_client.cmd must remain the manual address fallback")
    return errors


def validate_server_launcher(zip_file: ZipFile) -> list[str]:
    command = zip_file.read("start_internal_upload.cmd").decode("utf-8-sig")
    errors = []
    if "InternalUpload.exe" not in command:
        errors.append("start_internal_upload.cmd does not start InternalUpload.exe")
    if "실제 접속 주소" not in command or "config.ini" not in command:
        errors.append("start_internal_upload.cmd does not explain automatic web port selection")
    return errors


def verify_zip(zip_path: str, version: str | None = None) -> list[str]:
    errors = []
    with ZipFile(zip_path) as archive:
        names = normalized_names(archive)
        missing = sorted(REQUIRED_FILES - names)
        errors.extend(f"missing required file: {name}" for name in missing)
        errors.extend(validate_no_forbidden_entries(names))
        operational_results = sorted(
            name
            for name in names
            if name.startswith("data/network_check_results/") and name.lower().endswith(".json")
        )
        errors.extend(f"operational result in ZIP: {name}" for name in operational_results)
        operational_probe_results = sorted(
            name
            for name in names
            if name.startswith("data/network_probe_results/") and name.lower().endswith(".json")
        )
        errors.extend(f"operational probe result in ZIP: {name}" for name in operational_probe_results)
        if "data/upload_log.csv" in names:
            errors.extend(
                validate_csv_header(
                    archive,
                    "data/upload_log.csv",
                    ["upload_id", "uploaded_at", "original_filename"],
                )
            )
        if "data/network_check_log.csv" in names:
            errors.extend(
                validate_csv_header(
                    archive,
                    "data/network_check_log.csv",
                    ["checked_at", "client_ip", "direction"],
                )
            )
        if "data/network_check_session_log.csv" in names:
            errors.extend(
                validate_csv_header(
                    archive,
                    "data/network_check_session_log.csv",
                    ["checked_at", "session_id", "client_ip", "direction"],
                )
            )
        if "data/network_probe_log.csv" in names:
            errors.extend(
                validate_csv_header(
                    archive,
                    "data/network_probe_log.csv",
                    ["checked_at", "session_id", "agent_id", "agent_hostname", "client_ip", "server_host"],
                )
            )
        if version and "README_START_HERE_KO.txt" in names:
            readme = archive.read("README_START_HERE_KO.txt").decode("utf-8-sig")
            if version not in readme:
                errors.append(f"README_START_HERE_KO.txt does not mention {version}")
        if "start_tcp_probe_client.cmd" in names:
            errors.extend(validate_manual_probe_launcher(archive))
        if "start_internal_upload.cmd" in names:
            errors.extend(validate_server_launcher(archive))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True, dest="zip_path")
    parser.add_argument("--version", default="")
    args = parser.parse_args(argv)

    errors = verify_zip(args.zip_path, args.version or None)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Release ZIP verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
