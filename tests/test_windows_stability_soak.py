from __future__ import annotations

from pathlib import Path

from tools.run_windows_stability_soak import (
    SOAK_UPLOAD_BYTES,
    build_multipart_upload,
    build_subprocess_environment,
    run_soak,
    write_soak_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_windows_soak_workflow_runs_weekly_for_45_minutes():
    workflow = (PROJECT_ROOT / ".github/workflows/stability-windows.yml").read_text(
        encoding="utf-8"
    )

    assert "schedule:" in workflow
    assert 'cron: "23 18 * * 0"' in workflow
    assert "SOAK_DURATION_MINUTES" in workflow
    assert "|| '45'" in workflow
    assert "run_windows_stability_soak.py" in workflow
    assert '"30"' in workflow
    assert '"60"' in workflow


def test_soak_config_uses_separate_loopback_web_and_probe_ports(tmp_path):
    path = write_soak_config(tmp_path, 18000, 15201)
    content = path.read_text(encoding="utf-8")

    assert "HOST=127.0.0.1" in content
    assert "PORT=18000" in content
    assert "BASE_URL=http://127.0.0.1:18000" in content
    assert "ENABLED=true" in content
    assert "PORT=15201" in content


def test_soak_multipart_contains_complete_file_payload():
    content = b"x" * SOAK_UPLOAD_BYTES
    body, boundary = build_multipart_upload("soak.txt", content)

    assert body.startswith(f"--{boundary}\r\n".encode("ascii"))
    assert body.endswith(f"\r\n--{boundary}--\r\n".encode("ascii"))
    assert content in body


def test_soak_subprocesses_force_utf8_output():
    environment = build_subprocess_environment()

    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"


def test_single_soak_cycle_runs_real_upload_tcp_and_restart():
    summary = run_soak(duration_minutes=0.01, max_cycles=1)

    assert summary.status == "success"
    assert summary.completed_cycles == 1
    assert summary.uploaded_bytes == SOAK_UPLOAD_BYTES
    assert summary.tcp_self_checks == 1
