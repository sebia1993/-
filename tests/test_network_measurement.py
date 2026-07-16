from network_measurement import NetworkMeasurementGate


def test_measurement_gate_allows_only_one_owner():
    gate = NetworkMeasurementGate()

    assert gate.acquire("http", "one") is True
    assert gate.acquire("http", "one") is True
    assert gate.acquire("tcp", "two") is False
    assert gate.release("tcp", "two") is False
    assert gate.release("http", "one") is True
    assert gate.acquire("tcp", "two") is True


def test_measurement_gate_expires_owner_after_absolute_hold_limit():
    now = [100.0]
    gate = NetworkMeasurementGate(
        clock=lambda: now[0],
        max_hold_seconds={"http": 10.0},
    )

    assert gate.acquire("http", "one") is True
    now[0] = 108.0
    assert gate.status()["long_running"] is True
    assert gate.acquire("tcp", "two") is False

    now[0] = 110.1
    assert gate.acquire("tcp", "two") is True
    assert gate.release("http", "one") is False
    status = gate.status()
    assert status["kind"] == "tcp"
    assert status["expired_count"] == 1


def test_measurement_gate_does_not_extend_lease_for_same_owner_reacquire():
    now = [0.0]
    gate = NetworkMeasurementGate(
        clock=lambda: now[0],
        max_hold_seconds={"http": 10.0},
    )

    assert gate.acquire("http", "one") is True
    now[0] = 9.0
    assert gate.acquire("http", "one") is True
    now[0] = 10.1

    assert gate.is_available() is True
    assert gate.status()["expired_count"] == 1
