from __future__ import annotations

import socket
import tempfile
import threading
from pathlib import Path

from .client_package import build_client_package, verify_client_package
from .tcp_engine import aggregate_stream_results, run_receiver_stream, run_sender_stream


def _run_direction() -> tuple[dict, dict]:
    sender_socket, receiver_socket = socket.socketpair()
    cancel_event = threading.Event()
    results: dict[str, dict] = {}
    errors: list[BaseException] = []

    def sender() -> None:
        try:
            results["sender"] = aggregate_stream_results(
                [
                    run_sender_stream(
                        sender_socket,
                        stream_id=0,
                        warmup_seconds=0.1,
                        duration_seconds=1,
                        cancel_event=cancel_event,
                    )
                ],
                role="sender",
                duration_seconds=1,
            )
        except BaseException as exc:
            errors.append(exc)
            cancel_event.set()

    def receiver() -> None:
        try:
            results["receiver"] = aggregate_stream_results(
                [
                    run_receiver_stream(
                        receiver_socket,
                        stream_id=0,
                        warmup_seconds=0.1,
                        duration_seconds=1,
                        cancel_event=cancel_event,
                    )
                ],
                role="receiver",
                duration_seconds=1,
            )
        except BaseException as exc:
            errors.append(exc)
            cancel_event.set()

    sender_thread = threading.Thread(target=sender)
    receiver_thread = threading.Thread(target=receiver)
    sender_thread.start()
    receiver_thread.start()
    sender_thread.join(timeout=5)
    receiver_thread.join(timeout=5)
    sender_socket.close()
    receiver_socket.close()
    if sender_thread.is_alive() or receiver_thread.is_alive():
        raise RuntimeError("TCP 자체 점검이 제한 시간 안에 종료되지 않았습니다.")
    if errors:
        raise RuntimeError(str(errors[0]))
    return results["sender"], results["receiver"]


def _run_client_package_check(executable_path: Path | None) -> None:
    if executable_path is not None:
        package = build_client_package(executable_path, "http://127.0.0.1:8000")
        errors = verify_client_package(package.payload, package.server_url)
        if errors:
            raise RuntimeError(errors[0])
        return

    with tempfile.TemporaryDirectory() as directory:
        placeholder = Path(directory) / "InternalUpload.exe"
        placeholder.write_bytes(b"MZ-probe-self-check")
        package = build_client_package(placeholder, "http://127.0.0.1:8000")
        errors = verify_client_package(package.payload, package.server_url)
        if errors:
            raise RuntimeError(errors[0])


def run_probe_self_check(executable_path: Path | None = None) -> int:
    try:
        first_sender, first_receiver = _run_direction()
        second_sender, second_receiver = _run_direction()
        _run_client_package_check(executable_path)
    except Exception as exc:
        print(f"TCP probe self-check failed: {exc}")
        return 1
    results = (first_sender, first_receiver, second_sender, second_receiver)
    if any(int(result.get("bytes", 0)) <= 0 for result in results):
        print("TCP probe self-check failed: no measured bytes")
        return 1
    print("TCP probe self-check passed")
    return 0
