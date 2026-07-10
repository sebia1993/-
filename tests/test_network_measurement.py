from network_measurement import NetworkMeasurementGate


def test_measurement_gate_allows_only_one_owner():
    gate = NetworkMeasurementGate()

    assert gate.acquire("http", "one") is True
    assert gate.acquire("http", "one") is True
    assert gate.acquire("tcp", "two") is False
    assert gate.release("tcp", "two") is False
    assert gate.release("http", "one") is True
    assert gate.acquire("tcp", "two") is True
