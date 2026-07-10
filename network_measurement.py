from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class MeasurementOwner:
    kind: str
    owner_id: str


class NetworkMeasurementGate:
    """Allows only one network measurement to consume the server at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: MeasurementOwner | None = None

    def acquire(self, kind: str, owner_id: str) -> bool:
        owner = MeasurementOwner(kind=kind, owner_id=owner_id)
        with self._lock:
            if self._owner is not None:
                return self._owner == owner
            self._owner = owner
            return True

    def release(self, kind: str, owner_id: str) -> bool:
        owner = MeasurementOwner(kind=kind, owner_id=owner_id)
        with self._lock:
            if self._owner != owner:
                return False
            self._owner = None
            return True

    def current_owner(self) -> MeasurementOwner | None:
        with self._lock:
            return self._owner

    def is_available(self) -> bool:
        return self.current_owner() is None
