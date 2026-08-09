import secrets
from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass(frozen=True)
class PendingConfirmation:
    operation: str
    record_id: int
    device_id: str
    hardware_id: str | None
    ble_address: str | None
    expires_at: float


class LifecycleConfirmationStore:
    def __init__(self, ttl_seconds: int = 120) -> None:
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, PendingConfirmation] = {}
        self._lock = Lock()

    def issue(self, operation: str, record_id: int, device_id: str, hardware_id: str | None, ble_address: str | None) -> str:
        with self._lock:
            self._purge()
            token = secrets.token_urlsafe(32)
            self._pending[token] = PendingConfirmation(
                operation, record_id, device_id, hardware_id, ble_address, monotonic() + self.ttl_seconds
            )
        return token

    def consume(
        self, token: str, operation: str, record_id: int, device_id: str,
        hardware_id: str | None, ble_address: str | None,
    ) -> None:
        with self._lock:
            pending = self._pending.pop(token, None)
        if pending is None:
            raise ValueError("confirmation token is missing, expired, or already used")
        if pending.expires_at <= monotonic():
            raise ValueError("confirmation token expired")
        if (pending.operation, pending.record_id, pending.device_id, pending.hardware_id, pending.ble_address) != (
            operation, record_id, device_id, hardware_id, ble_address
        ):
            raise ValueError("confirmation token does not match the requested operation and identity")

    def _purge(self) -> None:
        now = monotonic()
        self._pending = {token: value for token, value in self._pending.items() if value.expires_at > now}
