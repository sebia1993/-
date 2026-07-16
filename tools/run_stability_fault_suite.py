from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


FAULT_TESTS = [
    "tests/test_fault_injection.py",
    "tests/test_app.py::test_upload_cleans_partial_file_when_space_runs_out_during_copy",
    "tests/test_app.py::test_upload_partial_log_write_failure_rolls_back_csv_and_file",
    "tests/test_bounded_server.py::test_bounded_server_rejects_excess_slow_clients_and_recovers_capacity",
    "tests/test_network_measurement.py::test_measurement_gate_expires_owner_after_absolute_hold_limit",
]


def main() -> int:
    return int(pytest.main(["-q", *FAULT_TESTS]))


if __name__ == "__main__":
    raise SystemExit(main())
